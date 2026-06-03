"""风控回测 — CLI 入口

Usage:
    python -m risk_control.backtest.run [--start YYYYMMDD] [--end YYYYMMDD] [--sweep]
    ./quickstart.sh backtest [start_date] [end_date] [--sweep]
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from risk_control.backtest.scenarios import (
    portfolio_from_toml,
    fetch_backtest_prices,
    fetch_market_index,
)
from risk_control.backtest.engine import run_backtest
from risk_control.backtest.params import run_parameter_sweep, DEFAULT_SWEEP_PARAMS
from risk_control.backtest.metrics import compute_metrics
from risk_control.backtest.report import generate_report


def main():
    parser = argparse.ArgumentParser(description="风控回测")
    parser.add_argument("--start", help="起始日期 YYYYMMDD，默认6个月前")
    parser.add_argument("--end", help="结束日期 YYYYMMDD，默认昨天")
    parser.add_argument("--sweep", action="store_true", help="运行参数扫描")
    parser.add_argument("--regime", default="neutral", choices=["bull", "bear", "neutral"],
                        help="市场区间假设")
    args = parser.parse_args()

    # 日期默认值
    end_date = args.end or (date.today() - timedelta(days=1)).strftime("%Y%m%d")
    start_date = args.start or (date.today() - timedelta(days=180)).strftime("%Y%m%d")

    print(f"📊 风控回测")
    print(f"   区间: {start_date} ~ {end_date}")
    print(f"   模式: {'参数扫描' if args.sweep else '单次运行'}")
    print(f"   市场区间: {args.regime}")
    print()

    # 加载持仓
    print("  加载持仓配置...")
    portfolio_df, total_equity = portfolio_from_toml()
    codes = portfolio_df["code"].tolist()
    print(f"  持仓 {len(codes)} 只: {', '.join(codes)}")
    print(f"  总权益: {total_equity:,.0f}")
    print()

    # 获取历史数据
    print("  获取历史行情...")
    prices_dict = fetch_backtest_prices(codes, start_date, end_date)
    loaded = [c for c in codes if c in prices_dict and not prices_dict[c].empty]
    print(f"  已加载 {len(loaded)}/{len(codes)} 只股票行情")

    # 加载市场指数
    index_df = fetch_market_index(start_date, end_date)
    if not index_df.empty:
        prices_dict["000001"] = index_df
        print("  已加载上证指数")
    print()

    if not loaded:
        print("  ❌ 无可用行情数据，无法回测")
        sys.exit(1)

    # 运行回测
    if args.sweep:
        print(f"  运行参数扫描（{_count_combinations(DEFAULT_SWEEP_PARAMS)} 种组合）...")
        sweep_results = run_parameter_sweep(
            portfolio_df=portfolio_df,
            prices_dict=prices_dict,
            start_date=start_date,
            end_date=end_date,
            total_equity=total_equity,
            market_regime=args.regime,
        )
    else:
        print("  运行单次回测（当前参数）...")
        from risk_control.backtest.params import SweepResult
        import risk_control.config as cfg
        result = run_backtest(
            portfolio_df=portfolio_df,
            prices_dict=prices_dict,
            start_date=start_date,
            end_date=end_date,
            total_equity=total_equity,
            market_regime=args.regime,
        )
        sweep_results = [SweepResult(
            params={
                "STOP_LOSS_ATR_MULTIPLIER": cfg.STOP_LOSS_ATR_MULTIPLIER,
                "TRAILING_STOP_ATR_MULTIPLIER": cfg.TRAILING_STOP_ATR_MULTIPLIER,
            },
            result=result,
        )]

    # 生成报告
    print()
    print("  生成报告...")
    json_path, md_path = generate_report(
        sweep_results=sweep_results,
        prices_dict=prices_dict,
        initial_equity=total_equity,
    )

    print(f"  ✅ JSON: {json_path}")
    print(f"  ✅ Markdown: {md_path}")
    print()

    # 打印摘要
    _print_summary(sweep_results)


def _count_combinations(sweep_params):
    count = 1
    for values in sweep_params.values():
        count *= len(values)
    return count


def _print_summary(sweep_results):
    """打印简要结果"""
    print("  === 结果摘要 ===")
    print()
    for sr in sweep_results[:5]:  # 最多显示5个
        m = sr.metrics
        if not m:
            continue
        p = sr.params
        print(f"  ATR止损={p.get('STOP_LOSS_ATR_MULTIPLIER', '?')}x "
              f"移动止损={p.get('TRAILING_STOP_ATR_MULTIPLIER', '?')}x")
        print(f"    回撤: {m['max_drawdown_with_rc']:.2%} (持有: {m['max_drawdown_buy_hold']:.2%})")
        print(f"    收益: {m['total_return_with_rc']:.2%} (持有: {m['total_return_buy_hold']:.2%})")
        print(f"    信号: {m['total_signals_fired']}次 → 交易: {m['total_trades_executed']}次")
        print()

    if len(sweep_results) > 5:
        print(f"  ... 共 {len(sweep_results)} 种组合，详见报告文件")


if __name__ == "__main__":
    main()
