"""策略归因分析核心脚本"""

import sys
import argparse
import json
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
import statsmodels.api as sm

sys.path.append(str(Path(__file__).parent.parent))
from config import (
    BENCHMARK_INDEX, RISK_FREE_RATE,
    ROLLING_WINDOW, MIN_TRADING_DAYS,
    REPORT_TITLE, OUTPUT_DIR
)
from scripts.data_provider import get_stock_prices, get_benchmark_prices
from scripts.brinson import brinson_analysis


def load_trades(csv_path):
    """加载交割单数据"""
    df = pd.read_csv(csv_path, dtype={'code': str})  # 确保代码列为字符串
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
    df = df.sort_values('date').reset_index(drop=True)

    # 确保数值列为正确类型
    numeric_cols = ['quantity', 'price', 'amount', 'brokerage_fee', 'stamp_duty',
                    'transfer_fee', 'other_fee', 'net_amount']
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    print(f"加载 {len(df)} 条交易记录")
    print(f"时间范围: {df['date'].min()} 至 {df['date'].max()}")
    return df


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
        # 持股清单是最新持仓，需要反推期初持仓：
        # 期初持仓 = 最新持仓 - 期间买入 + 期间卖出
        latest = {}
        for _, row in holdings_df.iterrows():
            code = str(row['code']).strip()
            latest[code] = {
                'quantity': int(row['quantity']),
                'cost_price': float(row.get('cost_price', 0)),
                'name': str(row.get('name', '')),
            }

        # 计算期间每只股票的净买入量
        net_bought = {}  # code → net quantity change (买入为正, 卖出为负)
        for _, trade in trades_df.iterrows():
            code = str(trade['code']).strip()
            if trade['direction'] == '买入':
                net_bought[code] = net_bought.get(code, 0) + trade['quantity']
            elif trade['direction'] == '卖出':
                net_bought[code] = net_bought.get(code, 0) - abs(trade['quantity'])

        # 反推期初持仓
        all_codes = set(list(latest.keys()) + list(net_bought.keys()))
        for code in all_codes:
            latest_qty = latest.get(code, {}).get('quantity', 0)
            net_change = net_bought.get(code, 0)
            initial_qty = latest_qty - net_change  # 期初 = 最新 - 净买入

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
        # 从卖出记录反推期初持仓（无成本信息）
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

            # 记录股票名称
            if trade.get('name') and not positions[code]['name']:
                positions[code]['name'] = trade['name']

            # 现金流：买入花钱（减少），卖出收钱（增加），分红入账，扣税扣除
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


def calculate_portfolio_value(snapshots, start_date, end_date):
    """计算每日组合市值（持仓市值 + 现金）"""
    # 获取所有持仓股票的行情
    all_codes = set()
    for snap in snapshots:
        all_codes.update(snap['positions'].keys())

    print(f"获取 {len(all_codes)} 只股票的行情数据...")
    stock_prices = {}
    for code in all_codes:
        prices = get_stock_prices(code, start_date.strftime('%Y%m%d'), end_date.strftime('%Y%m%d'))
        if not prices.empty:
            stock_prices[code] = prices.set_index('date')['close']

    # 计算每日市值（持仓 + 现金）
    daily_values = []
    for snap in snapshots:
        date = snap['date']
        stock_value = 0.0

        for code, pos in snap['positions'].items():
            if code in stock_prices and date in stock_prices[code].index:
                price = stock_prices[code].loc[date]
                stock_value += price * pos['quantity']

        total_value = stock_value + snap.get('cash', 0.0)
        daily_values.append({
            'date': date,
            'value': total_value,
            'stock_value': stock_value,
            'cash': snap.get('cash', 0.0)
        })

    return pd.DataFrame(daily_values)


def calculate_returns(portfolio_values, benchmark_prices, trades_df):
    """计算组合和基准收益率"""
    # 合并组合市值和基准价格
    df = portfolio_values.merge(benchmark_prices, on='date', how='outer', suffixes=('_portfolio', '_benchmark'))
    df = df.sort_values('date').reset_index(drop=True)

    # 前向填充（处理非交易日）
    df['value'] = df['value'].ffill()
    df['close'] = df['close'].ffill()

    # 计算组合收益率（value 已包含现金，直接计算）
    df['portfolio_return'] = df['value'].pct_change().fillna(0)

    # 计算基准收益率
    df['benchmark_return'] = df['close'].pct_change().fillna(0)

    # 计算无风险利率（日化）
    rf_daily = (1 + RISK_FREE_RATE) ** (1/252) - 1
    df['rf'] = rf_daily

    # 计算超额收益率
    df['excess_portfolio'] = df['portfolio_return'] - df['rf']
    df['excess_benchmark'] = df['benchmark_return'] - df['rf']

    return df


def alpha_beta_analysis(returns_df):
    """Alpha/Beta 回归分析"""
    # 过滤有效数据，确保数值类型
    valid = returns_df[['excess_portfolio', 'excess_benchmark']].copy()
    valid = valid.dropna()

    # 确保数据类型为 float
    valid['excess_portfolio'] = valid['excess_portfolio'].astype(float)
    valid['excess_benchmark'] = valid['excess_benchmark'].astype(float)

    if len(valid) < MIN_TRADING_DAYS:
        raise ValueError(f"交易日数量不足（{len(valid)} < {MIN_TRADING_DAYS}）")

    # OLS 回归
    X = sm.add_constant(valid['excess_benchmark'].values, has_constant='add')
    y = valid['excess_portfolio'].values
    model = sm.OLS(y, X).fit()

    if len(model.params) < 2:
        # 数据波动不足，无法拟合 Beta
        alpha_daily = model.params[0]
        beta = 0.0
    else:
        alpha_daily = model.params[0]
        beta = model.params[1]
    r_squared = model.rsquared

    # 年化 Alpha
    alpha_annual = alpha_daily * 252

    # 计算其他指标（确保数值类型）
    value_series = returns_df['value'].astype(float)
    close_series = returns_df['close'].astype(float)
    portfolio_return = returns_df['portfolio_return'].astype(float)

    total_return = (value_series.iloc[-1] / value_series.iloc[0]) - 1
    benchmark_total = (close_series.iloc[-1] / close_series.iloc[0]) - 1
    excess_return = total_return - benchmark_total

    # 夏普比率
    sharpe = (portfolio_return.mean() / portfolio_return.std()) * np.sqrt(252)

    # 最大回撤
    cumulative = (1 + portfolio_return).cumprod()
    running_max = cumulative.expanding().max()
    drawdown = (cumulative - running_max) / running_max
    max_drawdown = drawdown.min()

    # 年化波动率
    volatility = portfolio_return.std() * np.sqrt(252)

    return {
        'alpha_annual': alpha_annual,
        'beta': beta,
        'r_squared': r_squared,
        'total_return': total_return,
        'benchmark_total': benchmark_total,
        'excess_return': excess_return,
        'sharpe': sharpe,
        'max_drawdown': max_drawdown,
        'volatility': volatility,
    }


def print_terminal_report(results, start_date, end_date, brinson_result=None):
    """打印终端报告"""
    print("\n" + "=" * 50)
    print(f"{REPORT_TITLE}")
    print(f"分析区间：{start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}")
    print("=" * 50)

    print("\n【核心指标】")
    print(f"组合总收益率：     {results['total_return']:+.2%}")
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

    # Brinson 归因
    if brinson_result and brinson_result.get("details"):
        print("\n【Brinson 归因】")
        # 表头
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

        # 结论
        ta = brinson_result["total_allocation"]
        ts = brinson_result["total_selection"]
        print()
        if abs(ta) > abs(ts):
            print(f"→ 超额收益主要来自行业配置（配置效应 {ta:+.2%} > 选择效应 {ts:+.2%}）")
        else:
            print(f"→ 超额收益主要来自个股选择（选择效应 {ts:+.2%} > 配置效应 {ta:+.2%}）")

        if brinson_result.get("verification_diff") is not None:
            diff = brinson_result["verification_diff"]
            if diff < 0.01:
                print(f"→ 校验通过：三效应之和与超额收益差异 {diff:.4%}")

    print("=" * 50)


def generate_md_report(returns_df, results, output_path, start_date, end_date, brinson_result=None):
    """生成 Markdown 报告"""
    # 净值序列
    cumulative_portfolio = (1 + returns_df['portfolio_return']).cumprod()
    cumulative_benchmark = (1 + returns_df['benchmark_return']).cumprod()

    # 月度收益
    returns_df = returns_df.copy()
    returns_df['month'] = returns_df['date'].dt.to_period('M')
    monthly = returns_df.groupby('month').agg({
        'portfolio_return': lambda x: (1 + x).prod() - 1,
        'benchmark_return': lambda x: (1 + x).prod() - 1,
    })
    monthly['excess'] = monthly['portfolio_return'] - monthly['benchmark_return']

    # 收益归因
    beta_contrib = results['benchmark_total'] * results['beta']
    alpha_contrib = results['total_return'] - beta_contrib

    # Alpha/Beta 评价
    alpha_tag = "✓ 策略有效" if results['alpha_annual'] > 0 else "✗ 策略失效"
    beta_tag = "✓ 正常" if 0.5 < results['beta'] < 1.5 else "⚠ 异常"
    r2_tag = "✓ 良好" if results['r_squared'] > 0.7 else "⚠ 拟合度低"

    # 结论
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

    # 核心指标
    lines.append(f"## 核心指标")
    lines.append(f"")
    lines.append(f"| 指标 | 数值 | 评价 |")
    lines.append(f"|------|------|------|")
    lines.append(f"| 组合总收益率 | {results['total_return']:+.2%} | |")
    lines.append(f"| 基准总收益率 | {results['benchmark_total']:+.2%} | |")
    lines.append(f"| 超额收益率 | {results['excess_return']:+.2%} | |")
    lines.append(f"| Alpha（年化） | {results['alpha_annual']:+.2%} | {alpha_tag} |")
    lines.append(f"| Beta | {results['beta']:.2f} | {beta_tag} |")
    lines.append(f"| R² | {results['r_squared']:.2f} | {r2_tag} |")
    lines.append(f"| 夏普比率 | {results['sharpe']:.2f} | |")
    lines.append(f"| 最大回撤 | {results['max_drawdown']:.2%} | |")
    lines.append(f"| 年化波动率 | {results['volatility']:.2%} | |")
    lines.append(f"")

    # 收益归因
    lines.append(f"## 收益归因")
    lines.append(f"")
    lines.append(f"| 来源 | 贡献 |")
    lines.append(f"|------|------|")
    lines.append(f"| 市场贡献（Beta） | {beta_contrib:+.2%} |")
    lines.append(f"| 策略贡献（Alpha） | {alpha_contrib:+.2%} |")
    lines.append(f"")

    # 每日净值
    lines.append(f"## 每日净值")
    lines.append(f"")
    lines.append(f"| 日期 | 组合净值 | 基准净值 | 组合日收益 | 基准日收益 |")
    lines.append(f"|------|----------|----------|------------|------------|")
    for i, row in returns_df.iterrows():
        date_str = row['date'].strftime('%Y-%m-%d')
        pnv = cumulative_portfolio.iloc[i]
        bnv = cumulative_benchmark.iloc[i]
        pr = row['portfolio_return']
        br = row['benchmark_return']
        lines.append(f"| {date_str} | {pnv:.4f} | {bnv:.4f} | {pr:+.2%} | {br:+.2%} |")
    lines.append(f"")

    # 月度超额收益
    if len(monthly) > 0:
        lines.append(f"## 月度超额收益")
        lines.append(f"")
        lines.append(f"| 月份 | 组合收益 | 基准收益 | 超额收益 |")
        lines.append(f"|------|----------|----------|----------|")
        for month, row in monthly.iterrows():
            lines.append(f"| {month} | {row['portfolio_return']:+.2%} | {row['benchmark_return']:+.2%} | {row['excess']:+.2%} |")
        lines.append(f"")

    # Brinson 归因
    if brinson_result and brinson_result.get("details"):
        lines.append(f"## Brinson 归因（BHB 模型）")
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
        lines.append(f"")

        ta = brinson_result["total_allocation"]
        ts = brinson_result["total_selection"]
        if abs(ta) > abs(ts):
            lines.append(f"> 超额收益主要来自**行业配置**（配置效应 {ta:+.2%} > 选择效应 {ts:+.2%}）")
        else:
            lines.append(f"> 超额收益主要来自**个股选择**（选择效应 {ts:+.2%} > 配置效应 {ta:+.2%}）")
        lines.append(f"")

    # 结论
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
    parser.add_argument("--holdings", help="最新持仓 CSV 路径（对账单持股清单，用于反推期初持仓）")
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

    start = pd.to_datetime(args.start_date) if args.start_date else trades['date'].min()
    end = pd.to_datetime(args.end_date) if args.end_date else trades['date'].max()

    # 2. 重建持仓
    print("正在重建持仓...")
    snapshots = rebuild_positions(trades, holdings_df=holdings_df)

    # 3. 计算市值
    print("正在计算组合市值...")
    portfolio_values = calculate_portfolio_value(snapshots, start, end)

    # 4. 获取基准数据
    print("正在获取基准指数数据...")
    benchmark_prices = get_benchmark_prices(BENCHMARK_INDEX, start.strftime('%Y%m%d'), end.strftime('%Y%m%d'))

    # 5. 计算收益率
    print("正在计算收益率...")
    returns_df = calculate_returns(portfolio_values, benchmark_prices, trades)

    # 6. Alpha/Beta 分析
    print("正在进行 Alpha/Beta 分析...")
    results = alpha_beta_analysis(returns_df)

    # 7. Brinson 归因分析
    print("正在进行 Brinson 归因分析...")
    brinson_result = brinson_analysis(snapshots, portfolio_values, benchmark_prices, start, end)

    # 8. 输出报告
    print_terminal_report(results, start, end, brinson_result)

    if args.output:
        output_path = args.output
    else:
        output_path = f"{OUTPUT_DIR}/{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}_report.md"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    generate_md_report(returns_df, results, output_path, start, end, brinson_result)


if __name__ == "__main__":
    main()
