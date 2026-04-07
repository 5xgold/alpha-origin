"""策略归因分析核心脚本"""

import sys
import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import akshare as ak
import statsmodels.api as sm

sys.path.append(str(Path(__file__).parent.parent))
from config import (
    BENCHMARK_INDEX, RISK_FREE_RATE, CACHE_DIR,
    CACHE_EXPIRY_DAYS, ROLLING_WINDOW, MIN_TRADING_DAYS,
    REPORT_TITLE, OUTPUT_DIR
)


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


def get_stock_prices(code, start_date, end_date):
    """获取股票历史行情（带缓存）- 支持 A 股和港股"""
    code_str = str(code).strip()
    # 港股代码：5位纯数字（如 01810）
    is_hk = len(code_str) == 5 and code_str.isdigit()
    # A股代码补齐6位
    if not is_hk:
        code_str = code_str.zfill(6)

    cache_file = Path(CACHE_DIR) / f"{code_str}_{start_date}_{end_date}.csv"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        cache_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - cache_time < timedelta(days=CACHE_EXPIRY_DAYS):
            return pd.read_csv(cache_file, parse_dates=['date'])

    try:
        if is_hk:
            df = ak.stock_hk_hist(symbol=code_str, start_date=start_date, end_date=end_date, adjust="qfq")
            df = df.rename(columns={
                '日期': 'date',
                '开盘': 'open',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
            })
        else:
            df = ak.stock_zh_a_hist(symbol=code_str, start_date=start_date, end_date=end_date, adjust="qfq")
            df = df.rename(columns={
                '日期': 'date',
                '开盘': 'open',
                '收盘': 'close',
                '最高': 'high',
                '最低': 'low',
                '成交量': 'volume',
                '成交额': 'amount',
                '振幅': 'amplitude',
                '涨跌幅': 'pct_change',
                '涨跌额': 'change',
                '换手率': 'turnover'
            })
        df['date'] = pd.to_datetime(df['date'])
        df.to_csv(cache_file, index=False)
        return df
    except Exception as e:
        print(f"警告: 获取 {code_str} 行情失败: {e}")
        return pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])


def get_benchmark_prices(start_date, end_date):
    """获取基准指数行情（带缓存）- 保存完整 OHLCV 数据"""
    cache_file = Path(CACHE_DIR) / f"benchmark_{BENCHMARK_INDEX}_{start_date}_{end_date}.csv"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        cache_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - cache_time < timedelta(days=CACHE_EXPIRY_DAYS):
            return pd.read_csv(cache_file, parse_dates=['date'])

    try:
        # 尝试 sh/sz 两个前缀，兼容沪深指数
        df = None
        for prefix in ['sz', 'sh']:
            try:
                df = ak.stock_zh_index_daily(symbol=f"{prefix}{BENCHMARK_INDEX}")
                if df is not None and not df.empty:
                    break
            except Exception:
                continue

        if df is None or df.empty:
            raise ValueError(f"无法获取指数 {BENCHMARK_INDEX} 数据")

        df = df.rename(columns={
            'date': 'date',
            'open': 'open',
            'close': 'close',
            'high': 'high',
            'low': 'low',
            'volume': 'volume'
        })
        df['date'] = pd.to_datetime(df['date'])
        start_dt = pd.to_datetime(start_date, format='%Y%m%d')
        end_dt = pd.to_datetime(end_date, format='%Y%m%d')
        df = df[(df['date'] >= start_dt) & (df['date'] <= end_dt)]

        if df.empty:
            raise ValueError(f"指数 {BENCHMARK_INDEX} 在 {start_date}~{end_date} 无数据")

        df.to_csv(cache_file, index=False)
        return df
    except Exception as e:
        print(f"错误: 获取基准指数失败: {e}")
        sys.exit(1)


def rebuild_positions(trades_df):
    """重建每日持仓（含现金），自动识别期初已持有的股票"""
    # 第一步：扫描所有交易，识别期初持仓
    # 如果某只股票在卖出前从未买入过，说明是期初就持有的
    pre_existing = {}  # {code: quantity}
    running_qty = {}   # {code: cumulative_quantity}

    for _, trade in trades_df.iterrows():
        code = trade['code']
        if code not in running_qty:
            running_qty[code] = 0

        if trade['direction'] == '买入':
            running_qty[code] += trade['quantity']
        elif trade['direction'] == '卖出':
            running_qty[code] -= abs(trade['quantity'])

        # 如果累计数量为负，说明卖了期初持仓
        if running_qty[code] < 0:
            needed = abs(running_qty[code])
            if code not in pre_existing or needed > pre_existing[code]:
                pre_existing[code] = needed

    if pre_existing:
        print(f"检测到期初持仓: {', '.join(f'{c}x{q}' for c, q in pre_existing.items())}")

    # 第二步：初始化持仓（期初股票）
    positions = {}
    for code, qty in pre_existing.items():
        positions[code] = {'quantity': qty, 'cost_basis': 0.0}

    cash = 0.0
    daily_snapshots = []
    trade_dates = trades_df['date'].unique()

    for date in trade_dates:
        day_trades = trades_df[trades_df['date'] == date]

        for _, trade in day_trades.iterrows():
            code = trade['code']
            if code not in positions:
                positions[code] = {'quantity': 0, 'cost_basis': 0.0}

            # 现金流：买入花钱（减少），卖出收钱（增加）
            if trade['direction'] == '买入':
                cash -= abs(trade['net_amount'])
            else:
                cash += abs(trade['net_amount'])

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


def print_terminal_report(results, start_date, end_date):
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

    print("=" * 50)


def generate_md_report(returns_df, results, output_path, start_date, end_date):
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
    parser.add_argument("--start-date", help="开始日期 (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="结束日期 (YYYY-MM-DD)")
    parser.add_argument("--output", help="输出报告路径 (.md)")

    args = parser.parse_args()

    # 1. 加载数据
    print("正在加载交割单...")
    trades = load_trades(args.trades)

    start = pd.to_datetime(args.start_date) if args.start_date else trades['date'].min()
    end = pd.to_datetime(args.end_date) if args.end_date else trades['date'].max()

    # 2. 重建持仓
    print("正在重建持仓...")
    snapshots = rebuild_positions(trades)

    # 3. 计算市值
    print("正在计算组合市值...")
    portfolio_values = calculate_portfolio_value(snapshots, start, end)

    # 4. 获取基准数据
    print("正在获取基准指数数据...")
    benchmark_prices = get_benchmark_prices(start.strftime('%Y%m%d'), end.strftime('%Y%m%d'))

    # 5. 计算收益率
    print("正在计算收益率...")
    returns_df = calculate_returns(portfolio_values, benchmark_prices, trades)

    # 6. Alpha/Beta 分析
    print("正在进行 Alpha/Beta 分析...")
    results = alpha_beta_analysis(returns_df)

    # 7. 输出报告
    print_terminal_report(results, start, end)

    if args.output:
        output_path = args.output
    else:
        output_path = f"{OUTPUT_DIR}/{datetime.now().strftime('%Y-%m-%d')}_report.md"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    generate_md_report(returns_df, results, output_path, start, end)


if __name__ == "__main__":
    main()
