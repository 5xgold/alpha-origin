"""策略归因分析核心脚本"""

import sys
import argparse
from datetime import datetime
from pathlib import Path
import pandas as pd
import numpy as np
import statsmodels.api as sm

sys.path.append(str(Path(__file__).parent.parent))
from config import (
    BENCHMARK_INDEX, RISK_FREE_RATE,
    REPORT_TITLE, OUTPUT_DIR,
    parse_benchmark_config,
)
from shared.data_provider import get_stock_prices, get_benchmark_prices, get_composite_benchmark_prices
from scripts.brinson import brinson_analysis


def load_trades(csv_path):
    """加载交割单数据"""
    df = pd.read_csv(csv_path, dtype={'code': str})
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
    df = df.sort_values('date').reset_index(drop=True)

    numeric_cols = ['quantity', 'price', 'amount', 'brokerage_fee', 'stamp_duty',
                    'transfer_fee', 'other_fee', 'net_amount']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    print(f"加载 {len(df)} 条交易记录")
    print(f"时间范围: {df['date'].min()} 至 {df['date'].max()}")
    return df


def load_cash_flows(csv_path):
    """加载外部资金流 CSV

    Returns:
        DataFrame[date, amount, type]
    """
    df = pd.read_csv(csv_path)
    df['date'] = pd.to_datetime(df['date'])
    df['amount'] = pd.to_numeric(df['amount'], errors='coerce').fillna(0)
    print(f"加载 {len(df)} 条外部资金流")
    return df


def _normalize_cash_flows(cash_flows_df):
    """按日聚合外部资金流，返回 Series[date -> amount]。"""
    if cash_flows_df is None or cash_flows_df.empty:
        return pd.Series(dtype=float)

    flows = cash_flows_df.copy()
    flows['date'] = pd.to_datetime(flows['date'])
    return flows.groupby('date')['amount'].sum().sort_index().astype(float)


def rebuild_positions(trades_df, holdings_df=None):
    """重建每日持仓（含现金）

    Args:
        trades_df: 交易记录
        holdings_df: 最新持仓 DataFrame[code, name, quantity, cost_price]，
                     来自对账单「客户持股清单」（打印日期的持仓快照）。
                     若提供，则从最新持仓反推期初持仓，再正向重放交易。
                     若不提供，则从卖出记录推断期初持仓（成本为0）。
    """
    positions = {}

    if holdings_df is not None:
        latest = {}
        for _, row in holdings_df.iterrows():
            code = str(row['code']).strip()
            latest[code] = {
                'quantity': int(row['quantity']),
                'cost_price': float(row.get('cost_price', 0)),
                'name': str(row.get('name', '')),
            }

        net_bought = {}
        for _, trade in trades_df.iterrows():
            code = str(trade['code']).strip()
            if trade['direction'] == '买入':
                net_bought[code] = net_bought.get(code, 0) + trade['quantity']
            elif trade['direction'] == '卖出':
                net_bought[code] = net_bought.get(code, 0) - abs(trade['quantity'])

        all_codes = set(list(latest.keys()) + list(net_bought.keys()))
        for code in all_codes:
            latest_qty = latest.get(code, {}).get('quantity', 0)
            net_change = net_bought.get(code, 0)
            initial_qty = latest_qty - net_change

            if initial_qty > 0:
                cost_price = latest.get(code, {}).get('cost_price', 0)
                name = latest.get(code, {}).get('name', '')
                positions[code] = {
                    'quantity': initial_qty,
                    'cost_basis': cost_price * initial_qty,
                    'name': name,
                }

        print(f"从最新持仓反推期初持仓: {', '.join(f'{c}x{v['quantity']}' for c, v in positions.items())}")
    else:
        pre_existing = {}
        running_qty = {}

        for _, trade in trades_df.iterrows():
            code = trade['code']
            if code not in running_qty:
                running_qty[code] = 0

            if trade['direction'] == '买入':
                running_qty[code] += trade['quantity']
            elif trade['direction'] == '卖出':
                running_qty[code] -= abs(trade['quantity'])

            if running_qty[code] < 0:
                needed = abs(running_qty[code])
                if code not in pre_existing or needed > pre_existing[code]:
                    pre_existing[code] = needed

        if pre_existing:
            print(f"检测到期初持仓: {', '.join(f'{c}x{q}' for c, q in pre_existing.items())}")

        for code, qty in pre_existing.items():
            positions[code] = {'quantity': qty, 'cost_basis': 0.0, 'name': ''}

    cash = 0.0
    daily_snapshots = []
    trade_dates = trades_df['date'].unique()

    for date in trade_dates:
        day_trades = trades_df[trades_df['date'] == date]

        for _, trade in day_trades.iterrows():
            code = trade['code']
            if code not in positions:
                positions[code] = {'quantity': 0, 'cost_basis': 0.0, 'name': ''}

            if trade.get('name') and not positions[code]['name']:
                positions[code]['name'] = trade['name']

            if trade['direction'] == '买入':
                cash -= abs(trade['net_amount'])
            elif trade['direction'] == '卖出':
                cash += abs(trade['net_amount'])
            elif trade['direction'] == '分红':
                cash += abs(trade['net_amount'])
            elif trade['direction'] == '扣税':
                cash -= abs(trade['net_amount'])

            if trade['direction'] == '买入':
                positions[code]['quantity'] += trade['quantity']
                positions[code]['cost_basis'] += abs(trade['net_amount'])
            elif trade['direction'] == '卖出':
                sell_qty = abs(trade['quantity'])
                positions[code]['quantity'] -= sell_qty
                if positions[code]['quantity'] > 0:
                    original_qty = positions[code]['quantity'] + sell_qty
                    if original_qty > 0:
                        ratio = sell_qty / original_qty
                        positions[code]['cost_basis'] *= (1 - ratio)
                else:
                    positions[code]['cost_basis'] = 0.0

        snapshot = {
            'date': date,
            'positions': {k: v.copy() for k, v in positions.items() if v['quantity'] > 0},
            'cash': cash
        }
        daily_snapshots.append(snapshot)

    return daily_snapshots


def calculate_portfolio_value(snapshots, start_date, end_date, extra_dates=None):
    """计算每日组合市值，按估值日历前向持有快照。"""
    all_codes = set()
    for snap in snapshots:
        all_codes.update(snap['positions'].keys())

    start_str = start_date.strftime('%Y%m%d')
    end_str = end_date.strftime('%Y%m%d')

    stock_prices = {}
    for code in all_codes:
        df = get_stock_prices(code, start_str, end_str)
        if df is not None and not df.empty:
            # get_stock_prices 返回 DataFrame[date,open,close,...]
            # 转为 Series[date → close] 方便查价
            s = df.set_index('date')['close']
            s.index = pd.to_datetime(s.index)
            stock_prices[code] = s

    valuation_dates = set()
    if extra_dates is not None:
        valuation_dates.update(pd.to_datetime(extra_dates))
    for snap in snapshots:
        if start_date <= snap['date'] <= end_date:
            valuation_dates.add(pd.to_datetime(snap['date']))
    for series in stock_prices.values():
        valuation_dates.update(pd.to_datetime(d) for d in series.index if start_date <= d <= end_date)

    valuation_dates = sorted(d for d in valuation_dates if start_date <= d <= end_date)

    daily_values = []
    sorted_snapshots = sorted(snapshots, key=lambda x: x['date'])
    snap_idx = 0
    current_positions = {}
    current_cash = 0.0

    for date in valuation_dates:
        while snap_idx < len(sorted_snapshots) and sorted_snapshots[snap_idx]['date'] <= date:
            current_positions = {
                code: info.copy()
                for code, info in sorted_snapshots[snap_idx]['positions'].items()
            }
            current_cash = float(sorted_snapshots[snap_idx]['cash'])
            snap_idx += 1

        # 首个持仓快照出现前无法可靠估值，跳过前置空窗期
        if snap_idx == 0 and not current_positions and current_cash == 0.0:
            continue

        stock_value = 0.0
        for code, pos in current_positions.items():
            if code in stock_prices:
                price_series = stock_prices[code]
                valid = price_series[price_series.index <= date]
                if not valid.empty:
                    stock_value += valid.iloc[-1] * pos['quantity']

        total = stock_value + current_cash
        daily_values.append({
            'date': date,
            'value': total,
            'stock_value': stock_value,
            'cash': current_cash
        })

    return pd.DataFrame(daily_values), stock_prices


def build_performance_curves(portfolio_values, cash_flows_df=None):
    """从交易盈亏曲线派生账户曲线与策略曲线。"""
    if portfolio_values.empty:
        return portfolio_values.copy()

    curves = portfolio_values.copy().sort_values('date').reset_index(drop=True)
    flow_series = _normalize_cash_flows(cash_flows_df)
    curves['external_flow'] = curves['date'].map(flow_series).fillna(0.0)
    curves['cumulative_flow'] = curves['external_flow'].cumsum()
    curves['account_value'] = curves['value'] + curves['cumulative_flow']

    strategy_returns = [0.0]
    strategy_nav = [1.0]
    for i in range(1, len(curves)):
        prev_account = float(curves.loc[i - 1, 'account_value'])
        curr_account = float(curves.loc[i, 'account_value'])
        flow = float(curves.loc[i, 'external_flow'])
        adjusted_prev = prev_account + flow

        ret = 0.0
        if adjusted_prev > 0:
            ret = curr_account / adjusted_prev - 1

        strategy_returns.append(ret)
        strategy_nav.append(strategy_nav[-1] * (1 + ret))

    curves['strategy_return'] = strategy_returns
    curves['strategy_nav'] = strategy_nav
    return curves


def calculate_returns(curve_df, benchmark_prices, value_col='strategy_nav', return_col='portfolio_return'):
    """计算组合与基准的日收益率序列。"""
    pv = curve_df.set_index('date')[value_col]

    # benchmark_prices 可能是 DataFrame[date,close,...] 或 Series
    if isinstance(benchmark_prices, pd.DataFrame):
        bm = benchmark_prices.set_index('date')['close']
        bm.index = pd.to_datetime(bm.index)
    else:
        bm = benchmark_prices

    common_dates = sorted(set(pv.index) & set(bm.index))
    if len(common_dates) < 2:
        raise ValueError("组合与基准的重叠交易日不足")

    pv = pv.loc[common_dates]
    bm = bm.loc[common_dates]

    portfolio_returns = pv.pct_change().dropna()
    benchmark_returns = bm.pct_change().dropna()

    common = sorted(set(portfolio_returns.index) & set(benchmark_returns.index))
    df = pd.DataFrame({
        'date': common,
        return_col: portfolio_returns.loc[common].values,
        'benchmark_return': benchmark_returns.loc[common].values,
    })
    df['excess_return'] = df[return_col] - df['benchmark_return']

    return df


def alpha_beta_analysis(returns_df, return_col='portfolio_return'):
    """Alpha/Beta 回归分析（默认基于策略收益口径）。"""
    y = returns_df[return_col].values
    X = returns_df['benchmark_return'].values
    X_const = sm.add_constant(X)

    model = sm.OLS(y, X_const).fit()
    alpha_daily = model.params[0]
    beta = model.params[1]
    r_squared = model.rsquared

    trading_days = len(returns_df)
    total_return = (1 + returns_df[return_col]).prod() - 1
    benchmark_total = (1 + returns_df['benchmark_return']).prod() - 1
    excess_return = total_return - benchmark_total

    alpha_annual = alpha_daily * 252

    volatility = returns_df[return_col].std() * np.sqrt(252)
    daily_rf = RISK_FREE_RATE / 252
    ret_std = returns_df[return_col].std()
    sharpe = (returns_df[return_col].mean() - daily_rf) / ret_std * np.sqrt(252) if ret_std > 0 else 0

    cumulative = (1 + returns_df[return_col]).cumprod()
    rolling_max = cumulative.cummax()
    drawdown = (cumulative - rolling_max) / rolling_max
    max_drawdown = abs(drawdown.min())

    return {
        'alpha_daily': alpha_daily,
        'alpha_annual': alpha_annual,
        'beta': beta,
        'r_squared': r_squared,
        'total_return': total_return,
        'benchmark_total': benchmark_total,
        'excess_return': excess_return,
        'volatility': volatility,
        'sharpe': sharpe,
        'max_drawdown': max_drawdown,
        'trading_days': trading_days,
    }


def calculate_account_metrics(curves_df):
    """计算账户净值口径指标（含外部资金流）。"""
    if curves_df.empty:
        return {
            'net_flow': 0.0,
            'ending_value': 0.0,
            'pnl': 0.0,
            'max_drawdown': 0.0,
            'return': 0.0,
        }

    curves = curves_df.copy()
    curves = curves[curves['account_value'].astype(float) > 0].reset_index(drop=True)
    if curves.empty:
        return {
            'net_flow': 0.0,
            'ending_value': 0.0,
            'pnl': 0.0,
            'max_drawdown': 0.0,
            'return': 0.0,
        }

    account_value = curves['account_value'].astype(float)
    cumulative = account_value.copy()
    rolling_max = cumulative.cummax()
    drawdown = (cumulative - rolling_max) / rolling_max.replace(0, np.nan)
    max_drawdown = abs(drawdown.min()) if not drawdown.dropna().empty else 0.0

    start_value = float(account_value.iloc[0])
    end_value = float(account_value.iloc[-1])
    account_return = (end_value / start_value - 1) if start_value > 0 else 0.0
    flow_delta = float(curves['cumulative_flow'].iloc[-1] - curves['cumulative_flow'].iloc[0])
    account_pnl = end_value - start_value - flow_delta

    return {
        'net_flow': flow_delta,
        'ending_value': end_value,
        'pnl': float(account_pnl),
        'max_drawdown': float(max_drawdown),
        'return': float(account_return),
    }


def print_terminal_report(results, start_date, end_date, brinson_result=None):
    """终端输出分析结果"""
    print("\n" + "=" * 50)
    print(f"  {REPORT_TITLE}")
    print(f"分析区间：{start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}")
    print("=" * 50)

    print("\n【账户表现】")
    print(f"账户期末净值：     {results['account_ending_value']:+,.2f}")
    print(f"净流入/流出：      {results['account_net_flow']:+,.2f}")
    print(f"账户盈亏：         {results['account_pnl']:+,.2f}")
    print(f"账户净值收益率：    {results['account_return']:+.2%}")
    print(f"账户最大回撤：     {results['account_max_drawdown']:.2%}")

    print("\n【策略表现】")
    print(f"时间加权收益率(TWR)：{results['twr']:+.2%}")
    print(f"策略总收益率：     {results['total_return']:+.2%}")
    print(f"基准总收益率：     {results['benchmark_total']:+.2%}")
    print(f"超额收益率：       {results['excess_return']:+.2%}")
    print()
    print(f"Alpha（年化）：    {results['alpha_annual']:+.2%}  {'✓ 策略有效' if results['alpha_annual'] > 0 else '✗ 策略失效'}")
    print(f"Beta：             {results['beta']:.2f}   {'✓ 市场敏感度正常' if 0.5 < results['beta'] < 1.5 else '⚠ 异常'}")
    print(f"R²：               {results['r_squared']:.2f}   {'✓ 模型拟合良好' if results['r_squared'] > 0.7 else '⚠ 拟合度低'}")
    print()
    print(f"夏普比率：         {results['sharpe']:.2f}")
    print(f"最大回撤：         {results['max_drawdown']:.2%}")
    print(f"年化波动率：       {results['volatility']:.2%}")

    print("\n【收益归因】")
    beta_contrib = results['benchmark_total'] * results['beta']
    alpha_contrib = results['total_return'] - beta_contrib
    print(f"市场贡献（Beta）： {beta_contrib:+.2%}")
    print(f"策略贡献（Alpha）： {alpha_contrib:+.2%}")

    print("\n【结论】")
    if results['alpha_annual'] > 0:
        print("策略表现优异，Alpha 显著为正。")
        if results['benchmark_total'] < 0:
            print("在下跌市场中仍获得正收益，风控有效。")
    else:
        print("策略表现不佳，Alpha 为负。")
        if results['beta'] > 1:
            print("市场敏感度过高，放大了市场波动。")

    if brinson_result and brinson_result.get("details"):
        print("\n【Brinson 归因】")
        header = f"{'行业':<10} | {'组合权重':>8} | {'基准权重':>8} | {'组合收益':>8} | {'基准收益':>8} | {'配置效应':>8} | {'选择效应':>8} | {'交互效应':>8}"
        print(header)
        print("-" * len(header))

        for d in brinson_result["details"]:
            sector = d["sector"][:10].ljust(10)
            print(f"{sector} | {d['portfolio_weight']:>7.1%} | {d['benchmark_weight']:>7.1%} | "
                  f"{d['portfolio_return']:>+7.1%} | {d['benchmark_return']:>+7.1%} | "
                  f"{d['allocation']:>+7.2%} | {d['selection']:>+7.2%} | {d['interaction']:>+7.2%}")

        print("-" * len(header))
        print(f"{'合计':<10} | {'100.0%':>8} | {'100.0%':>8} | {'':>8} | {'':>8} | "
              f"{brinson_result['total_allocation']:>+7.2%} | "
              f"{brinson_result['total_selection']:>+7.2%} | "
              f"{brinson_result['total_interaction']:>+7.2%}")
        if brinson_result.get("residual_effect") is not None:
            print(f"{'残差':<10} | {'':>8} | {'':>8} | {'':>8} | {'':>8} | "
                  f"{'':>7} | {'':>7} | {brinson_result['residual_effect']:>+7.2%}")

        ta = brinson_result["total_allocation"]
        ts = brinson_result["total_selection"]
        print()
        if abs(ta) > abs(ts):
            print(f"→ 超额收益主要来自行业配置（配置效应 {ta:+.2%} > 选择效应 {ts:+.2%}）")
        else:
            print(f"→ 超额收益主要来自个股选择（选择效应 {ts:+.2%} > 配置效应 {ta:+.2%}）")

        if brinson_result.get("residual_effect") is not None:
            residual = brinson_result["residual_effect"]
            print(f"→ 现金与交易残差 {residual:+.2%}")

    print("=" * 50)


def generate_md_report(returns_df, results, output_path, start_date, end_date, brinson_result=None):
    """生成 Markdown 报告"""
    returns_df = returns_df.copy()
    returns_df['month'] = returns_df['date'].dt.to_period('M')
    monthly = returns_df.groupby('month').agg({
        'portfolio_return': lambda x: (1 + x).prod() - 1,
        'benchmark_return': lambda x: (1 + x).prod() - 1,
    })
    monthly['excess'] = monthly['portfolio_return'] - monthly['benchmark_return']

    beta_contrib = results['benchmark_total'] * results['beta']
    alpha_contrib = results['total_return'] - beta_contrib

    alpha_tag = "✓ 策略有效" if results['alpha_annual'] > 0 else "✗ 策略失效"
    beta_tag = "✓ 正常" if 0.5 < results['beta'] < 1.5 else "⚠ 异常"
    r2_tag = "✓ 良好" if results['r_squared'] > 0.7 else "⚠ 拟合度低"

    if results['alpha_annual'] > 0:
        conclusion = "策略表现优异，Alpha 显著为正。"
        if results['benchmark_total'] < 0:
            conclusion += "在下跌市场中仍获得正收益，风控有效。"
    else:
        conclusion = "策略表现不佳，Alpha 为负。"
        if results['beta'] > 1:
            conclusion += "市场敏感度过高，放大了市场波动。"

    lines = []
    lines.append(f"# {REPORT_TITLE}")
    lines.append(f"")
    lines.append(f"分析区间：{start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}")
    lines.append(f"")

    lines.append(f"## 账户表现")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 | 评价 |")
    lines.append(f"|------|------|------|")
    lines.append(f"| 账户期末净值 | {results['account_ending_value']:+,.2f} | |")
    lines.append(f"| 净流入/流出 | {results['account_net_flow']:+,.2f} | |")
    lines.append(f"| 账户盈亏 | {results['account_pnl']:+,.2f} | |")
    lines.append(f"| 账户净值收益率 | {results['account_return']:+.2%} | 含外部资金流 |")
    lines.append(f"| 账户最大回撤 | {results['account_max_drawdown']:.2%} | |")
    lines.append(f"")

    lines.append(f"## 策略表现")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 | 评价 |")
    lines.append(f"|------|------|------|")
    lines.append(f"| 时间加权收益率(TWR) | {results['twr']:+.2%} | 剔除外部资金流 |")
    lines.append(f"| 策略总收益率 | {results['total_return']:+.2%} | |")
    lines.append(f"| 基准总收益率 | {results['benchmark_total']:+.2%} | |")
    lines.append(f"| 超额收益率 | {results['excess_return']:+.2%} | |")
    lines.append(f"| Alpha（年化） | {results['alpha_annual']:+.2%} | {alpha_tag} |")
    lines.append(f"| Beta | {results['beta']:.2f} | {beta_tag} |")
    lines.append(f"| R² | {results['r_squared']:.2f} | {r2_tag} |")
    lines.append(f"| 夏普比率 | {results['sharpe']:.2f} | |")
    lines.append(f"| 最大回撤 | {results['max_drawdown']:.2%} | |")
    lines.append(f"| 年化波动率 | {results['volatility']:.2%} | |")
    lines.append(f"")

    lines.append(f"## 收益归因")
    lines.append(f"")
    lines.append(f"| 来源 | 贡献 |")
    lines.append(f"|------|------|")
    lines.append(f"| 市场贡献（Beta） | {beta_contrib:+.2%} |")
    lines.append(f"| 策略贡献（Alpha） | {alpha_contrib:+.2%} |")
    lines.append(f"")

    lines.append(f"## 月度收益")
    lines.append(f"")
    lines.append(f"| 月份 | 策略收益 | 基准收益 | 超额收益 |")
    lines.append(f"|------|----------|----------|----------|")
    for month, row in monthly.iterrows():
        lines.append(f"| {month} | {row['portfolio_return']:+.2%} | {row['benchmark_return']:+.2%} | {row['excess']:+.2%} |")
    lines.append(f"")

    if brinson_result and brinson_result.get("details"):
        lines.append(f"## Brinson 归因分析")
        lines.append(f"")
        lines.append(f"| 行业 | 组合权重 | 基准权重 | 组合收益 | 基准收益 | 配置效应 | 选择效应 | 交互效应 |")
        lines.append(f"|------|----------|----------|----------|----------|----------|----------|----------|")
        for d in brinson_result["details"]:
            lines.append(
                f"| {d['sector']} "
                f"| {d['portfolio_weight']:.1%} "
                f"| {d['benchmark_weight']:.1%} "
                f"| {d['portfolio_return']:+.1%} "
                f"| {d['benchmark_return']:+.1%} "
                f"| {d['allocation']:+.2%} "
                f"| {d['selection']:+.2%} "
                f"| {d['interaction']:+.2%} |"
            )
        lines.append(
            f"| **合计** | 100.0% | 100.0% | | "
            f"| **{brinson_result['total_allocation']:+.2%}** "
            f"| **{brinson_result['total_selection']:+.2%}** "
            f"| **{brinson_result['total_interaction']:+.2%}** |"
        )
        if brinson_result.get("residual_effect") is not None:
            lines.append(
                f"| **现金与交易残差** |  |  |  |  |  |  | **{brinson_result['residual_effect']:+.2%}** |"
            )
        lines.append(f"")

        ta = brinson_result["total_allocation"]
        ts = brinson_result["total_selection"]
        if abs(ta) > abs(ts):
            lines.append(f"> 超额收益主要来自**行业配置**（配置效应 {ta:+.2%} > 选择效应 {ts:+.2%}）")
        else:
            lines.append(f"> 超额收益主要来自**个股选择**（选择效应 {ts:+.2%} > 配置效应 {ta:+.2%}）")
        if brinson_result.get("residual_effect") is not None:
            lines.append(f"> 现金与交易残差：{brinson_result['residual_effect']:+.2%}")
        lines.append(f"")

    lines.append(f"## 结论")
    lines.append(f"")
    lines.append(conclusion)
    lines.append(f"")

    md_content = "\n".join(lines)
    Path(output_path).write_text(md_content, encoding='utf-8')
    print(f"\n详细报告已生成：{output_path}")


def main():
    parser = argparse.ArgumentParser(description="策略归因分析")
    parser.add_argument("--trades", required=True, help="交割单 CSV 路径")
    parser.add_argument("--holdings", help="最新持仓 CSV 路径")
    parser.add_argument("--cash-flows", help="外部资金流 CSV 路径（用于 TWR 计算）")
    parser.add_argument("--start-date", help="开始日期 (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="结束日期 (YYYY-MM-DD)")
    parser.add_argument("--output", help="输出报告路径 (.md)")

    args = parser.parse_args()

    # 1. 加载数据
    print("正在加载交割单...")
    trades = load_trades(args.trades)

    holdings_df = None
    if args.holdings:
        print(f"正在加载最新持仓: {args.holdings}")
        holdings_df = pd.read_csv(args.holdings, dtype={'code': str})

    cash_flows_df = None
    if args.cash_flows:
        print(f"正在加载外部资金流: {args.cash_flows}")
        cash_flows_df = load_cash_flows(args.cash_flows)

    start = pd.to_datetime(args.start_date) if args.start_date else trades['date'].min()
    end = pd.to_datetime(args.end_date) if args.end_date else trades['date'].max()

    # 2. 重建持仓
    print("正在重建持仓...")
    snapshots = rebuild_positions(trades, holdings_df=holdings_df)

    # 3. 获取基准数据
    print("正在获取基准指数数据...")
    benchmark_config = parse_benchmark_config(BENCHMARK_INDEX)
    if len(benchmark_config) == 1:
        benchmark_prices = get_benchmark_prices(benchmark_config[0]["index"], start.strftime('%Y%m%d'), end.strftime('%Y%m%d'))
    else:
        benchmark_prices = get_composite_benchmark_prices(benchmark_config, start.strftime('%Y%m%d'), end.strftime('%Y%m%d'))

    # 4. 计算底层估值与双口径曲线
    print("正在计算组合市值...")
    extra_dates = []
    if benchmark_prices is not None and not benchmark_prices.empty:
        extra_dates.extend(pd.to_datetime(benchmark_prices['date']).tolist())
    if cash_flows_df is not None and not cash_flows_df.empty:
        extra_dates.extend(pd.to_datetime(cash_flows_df['date']).tolist())

    portfolio_values, stock_prices = calculate_portfolio_value(
        snapshots, start, end, extra_dates=extra_dates,
    )
    curves_df = build_performance_curves(portfolio_values, cash_flows_df)

    # 5. 计算策略收益率
    print("正在计算收益率...")
    returns_df = calculate_returns(curves_df, benchmark_prices, value_col='strategy_nav')

    # 6. Alpha/Beta 分析
    print("正在进行 Alpha/Beta 分析...")
    results = alpha_beta_analysis(returns_df)
    results['twr'] = results['total_return']
    results.update({
        'account_net_flow': 0.0,
        'account_ending_value': 0.0,
        'account_pnl': 0.0,
        'account_max_drawdown': 0.0,
        'account_return': 0.0,
    })
    results.update({
        f"account_{k}": v
        for k, v in calculate_account_metrics(curves_df).items()
    })
    print(f"\n时间加权收益率 (TWR): {results['twr']:+.2%}")

    # 8. Brinson 归因分析
    print("正在进行 Brinson 归因分析...")
    strategy_curve = curves_df[['date', 'strategy_nav']].rename(columns={'strategy_nav': 'value'})
    brinson_result = brinson_analysis(
        snapshots, strategy_curve, benchmark_prices, start, end,
        stock_prices, benchmark_config=benchmark_config,
    )

    # 9. 输出报告
    print_terminal_report(results, start, end, brinson_result)

    if args.output:
        output_path = args.output
    else:
        output_path = f"{OUTPUT_DIR}/{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}_report_{datetime.now().strftime('%H%M')}.md"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    generate_md_report(returns_df, results, output_path, start, end, brinson_result)


if __name__ == "__main__":
    main()
