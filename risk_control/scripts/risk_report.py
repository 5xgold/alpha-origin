"""风控检查报告 — 主入口

Usage:
    python scripts/risk_report.py --equity 500000
"""

import sys
import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.parent))
from shared.data_provider import get_stock_prices, get_benchmark_prices, get_composite_benchmark_prices
from shared.portfolio_config import load_account_config, load_portfolio_from_toml
from shared.config import parse_benchmark_config
from risk_control.config import (
    MARKET_INDEX, ATR_PERIOD, PORTFOLIO_LOOKBACK_DAYS, DATA_FREQ,
    FAMILIARITY_DIMENSIONS, FAMILIARITY_LEVEL_LABELS,
    get_regime_params,
)
from risk_control.scripts.risk_calc import calc_realized_vol
from risk_control.scripts.position_check import check_positions
from risk_control.scripts.stop_loss import calc_stop_take_levels, check_circuit_breaker
from risk_control.scripts.anomaly_detect import detect_anomalies
from risk_control.signals import (
    run_all_signals, load_state, save_state, clear_stale_signals,
    clear_inactive_signal_records,
    classify_alerts, format_alert_section,
)


# ═══════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════

ROOT_DIR = Path(__file__).parent.parent.parent
DEFAULT_PORTFOLIO_TOML = ROOT_DIR / "portfolio.toml"


def _market_index_label(components):
    """生成市场指数的显示名称"""
    _INDEX_NAMES = {
        "000001": "上证指数", "000300": "沪深300", "000905": "中证500",
        "399001": "深证成指", "HK.800000": "恒生指数",
    }
    if len(components) == 1:
        return _INDEX_NAMES.get(components[0]["index"], components[0]["index"])
    parts = []
    for c in components:
        name = _INDEX_NAMES.get(c["index"], c["index"])
        parts.append(f"{name} {c['weight']:.0%}")
    return " + ".join(parts)


def fetch_prices(portfolio_df, lookback_days=None):
    """获取持仓股票 + 市场指数的历史行情

    Returns:
        prices_dict: {code: DataFrame[date, open, high, low, close, volume]}
        market_prices: DataFrame (市场指数)
        market_index_name: str (指数组合名称)
    """
    if lookback_days is None:
        lookback_days = max(PORTFOLIO_LOOKBACK_DAYS, ATR_PERIOD * 3)

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=int(lookback_days * 1.8))).strftime("%Y%m%d")

    prices_dict = {}
    codes = portfolio_df["code"].astype(str).tolist()

    for code in codes:
        try:
            df = get_stock_prices(code, start_date, end_date)
            if df is not None and not df.empty:
                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"])
                prices_dict[code] = df
        except Exception as e:
            print(f"  警告: {code} 行情获取失败: {e}")

    # 市场指数（支持单指数或多指数合成）
    components = parse_benchmark_config(MARKET_INDEX)
    market_index_name = _market_index_label(components)
    market_prices = None
    try:
        if len(components) == 1:
            market_prices = get_benchmark_prices(components[0]["index"], start_date, end_date)
        else:
            market_prices = get_composite_benchmark_prices(components, start_date, end_date)
    except Exception as e:
        print(f"  警告: {market_index_name} 行情获取失败: {e}")

    return prices_dict, market_prices, market_index_name


def enrich_portfolio(portfolio_df, prices_dict):
    """给持仓添加 current_price / market_value / price_status 列"""
    current_prices = []
    market_values = []
    price_statuses = []

    for _, row in portfolio_df.iterrows():
        code = str(row["code"])
        qty = float(row["quantity"])
        cost = float(row["cost_price"])

        if code in prices_dict and not prices_dict[code].empty:
            price = float(prices_dict[code]["close"].iloc[-1])
            status = "market"
            market_value = price * qty
        elif cost > 0:
            # 行情缺失时用成本价兜底，避免把真实持仓静默记成 0
            price = cost
            status = "cost_fallback"
            market_value = price * qty
        else:
            price = pd.NA
            status = "missing"
            market_value = pd.NA

        current_prices.append(price)
        market_values.append(market_value)
        price_statuses.append(status)

    portfolio_df = portfolio_df.copy()
    portfolio_df["current_price"] = current_prices
    portfolio_df["market_value"] = market_values
    portfolio_df["price_status"] = price_statuses
    return portfolio_df


def validate_portfolio_prices(portfolio_df):
    """校验持仓估值输入，阻止生成误导性报告。"""
    missing_rows = portfolio_df[portfolio_df["price_status"] == "missing"]
    if missing_rows.empty:
        return

    missing_codes = ", ".join(
        f"{row['name']}({row['code']})" for _, row in missing_rows.iterrows()
    )
    raise ValueError(
        "以下持仓缺少行情且成本价为 0，无法可靠估值: "
        f"{missing_codes}. 请补充成本价或检查行情源后重试。"
    )


# ═══════════════════════════════════════════
# 报告格式化
# ═══════════════════════════════════════════

SIGNAL_LABELS = {
    "hold": "✅持有",
    "stop_loss": "🔴止损",
    "take_profit": "🟡止盈",
    "trailing_stop": "🟠移动止损",
}

ACTION_LABELS = {
    "safe": "✅ 安全",
    "warning": "⚠️ 预警",
    "reduce_50": "🔴 建议减仓50%",
    "liquidate": "🔴 建议清仓",
}


def _fmt_pct(v, width=6):
    return f"{v:+.1%}".rjust(width)


def _fmt_price(v, width=8):
    if pd.isna(v) or v is None or v == 0:
        return "N/A".rjust(width)
    return f"{float(v):.3f}".rjust(width)


def _fmt_money(v):
    return f"¥{v:,.0f}"


def _fmt_atr_multiplier(v):
    return f"{float(v):.1f}".rstrip("0").rstrip(".")


def format_terminal_report(today, portfolio_df, total_equity, pos_result, sl_levels, cb_result, anomaly_result, alert_groups=None):
    """生成终端输出文本"""
    lines = []
    w = 72

    lines.append("═" * w)
    lines.append(f"{'风控检查报告 ' + today:^{w}}")
    lines.append("═" * w)

    # 组合概览
    total_mv = portfolio_df["market_value"].sum()
    cash = total_equity - total_mv
    lines.append("")
    lines.append("📊 组合概览")
    lines.append(f"  总权益: {_fmt_money(total_equity)}  "
                 f"持仓市值: {_fmt_money(total_mv)}  "
                 f"现金: {_fmt_money(cash)}")
    lines.append(f"  仓位: {pos_result['current_position']:.0%}  "
                 f"持仓数: {len(portfolio_df)}")
    regime = get_regime_params()
    regime_label = regime["label"]
    lines.append(f"  市场区间: {regime_label} (止损×{regime['stop_loss_multiplier']:.1f} "
                 f"止盈×{regime['take_profit_multiplier']:.1f})")

    # 第一道防线
    lines.append("")
    lines.append("🛡️ 第一道防线：仓位管理")
    lines.append(f"  {pos_result['market_index_name']}波动率: {pos_result['market_vol']:.1f}% → "
                 f"建议仓位 ≤{pos_result['suggested_position']:.0%}")

    if pos_result["position_warning"]:
        lines.append(f"  ⚠️ 当前仓位 {pos_result['current_position']:.0%} "
                     f"超出建议 {pos_result['suggested_position']:.0%}")

    for v in pos_result["stock_violations"]:
        level = v.get("familiarity_level", "low")
        tag = FAMILIARITY_LEVEL_LABELS.get(level, "低") + "熟悉"
        lines.append(f"  ⚠️ {v['name']} 占比 {v['weight']:.0%} > {tag}上限 {v['limit']:.0%}")

    for v in pos_result["sector_violations"]:
        names = "、".join(v["codes"])
        lines.append(f"  ⚠️ {v['sector']} 占比 {v['weight']:.0%} > {v['limit']:.0%} 上限 ({names})")

    if not pos_result["position_warning"] and not pos_result["stock_violations"] and not pos_result["sector_violations"]:
        lines.append("  ✅ 仓位正常")

    # 熟悉程度概览
    lines.append("")
    lines.append("  个股熟悉程度评估:")
    lines.append(f"  {'股票':<12} {'商模':>4} {'股东':>4} {'估值':>4} {'趋势':>4}  {'等级':>4} {'上限':>4}")
    lines.append("  " + "─" * 48)
    for sf in pos_result.get("stock_familiarity", []):
        detail = sf["detail"] if isinstance(sf["detail"], dict) else {}
        marks = [("✓" if detail.get(d, False) else "✗") for d in FAMILIARITY_DIMENSIONS]
        label = FAMILIARITY_LEVEL_LABELS[sf["level"]]
        lines.append(f"  {sf['name']:<12} {marks[0]:>4} {marks[1]:>4} {marks[2]:>4} {marks[3]:>4}  {label:>4} {sf['limit']:>4.0%}")

    # 第二道防线
    lines.append("")
    lines.append("🎯 第二道防线：止损止盈")

    # 表头
    header = f"  {'股票':<10} {'成本':>8} {'现价':>8} {'止损':>8} {'ATR':>6} {'移动止':>8} {'盈亏':>7} {'信号':<6}"
    lines.append(header)
    lines.append("  " + "─" * (w - 4))

    for sl in sl_levels:
        signal_label = SIGNAL_LABELS.get(sl["signal"], sl["signal"])
        atr_str = f"{sl.get('atr') or 0:.3f}".rjust(6) if sl.get("atr") else "N/A  ".rjust(6)
        trail_str = _fmt_price(sl.get("trailing_stop")) if sl.get("trailing_stop") else "     —"
        lines.append(
            f"  {sl['name']:<10} "
            f"{_fmt_price(sl['cost_price'])} "
            f"{_fmt_price(sl['current_price'])} "
            f"{_fmt_price(sl['stop_loss'])} "
            f"{atr_str} "
            f"{trail_str} "
            f"{_fmt_pct(sl['pnl_pct'], 7)} "
            f"{signal_label}"
        )

    # 熔断
    lines.append("")
    cb_triggered = any(cb_result[k]["triggered"] for k in ["daily", "weekly", "monthly"])
    if cb_triggered:
        for period in ["daily", "weekly", "monthly"]:
            info = cb_result[period]
            if info["triggered"]:
                label = {"daily": "日", "weekly": "周", "monthly": "月"}[period]
                lines.append(f"  🔴 {label}回撤 {info['drawdown']:+.2%} 触发熔断 (阈值 -{info['threshold']:.0%})")
        if cb_result["action"]:
            lines.append(f"  → 熔断动作: {ACTION_LABELS.get(cb_result['action'], cb_result['action'])}")
    else:
        lines.append(f"  组合熔断: ✅ 未触发 "
                     f"(日 {cb_result['daily']['drawdown']:+.2%} / "
                     f"周 {cb_result['weekly']['drawdown']:+.2%} / "
                     f"月 {cb_result['monthly']['drawdown']:+.2%})")

    # 第三道防线
    lines.append("")
    lines.append("🔍 第三道防线：异常检测")

    if anomaly_result["signals"]:
        for sig in anomaly_result["signals"]:
            type_labels = {
                "vol_spike": "波动率突变",
                "liquidity_dry": "流动性枯竭",
                "high_correlation": "相关性过高",
                "external_shock": "外部冲击",
            }
            label = type_labels.get(sig["type"], sig["type"])
            lines.append(f"  ⚠️ {label}: {sig['code']} ({sig['detail']})")
        lines.append(f"  → {anomaly_result['alert_count']}类信号，共{anomaly_result['signal_count']}条: "
                     f"{ACTION_LABELS.get(anomaly_result['action'], anomaly_result['action'])}")
    else:
        lines.append("  ✅ 无异常信号")

    # 信号系统
    if alert_groups is not None:
        lines.append("")
        lines.extend(format_alert_section(alert_groups))

    # 操作建议
    suggestions = _generate_suggestions(pos_result, sl_levels, cb_result, anomaly_result)
    if suggestions:
        lines.append("")
        lines.append("📋 明日操作建议")
        for i, s in enumerate(suggestions, 1):
            lines.append(f"  {i}. {s}")

    lines.append("")
    lines.append("═" * w)

    return "\n".join(lines)


def _generate_suggestions(pos_result, sl_levels, cb_result, anomaly_result):
    """根据检查结果生成操作建议（附带计算依据）"""
    suggestions = []

    # 仓位建议
    if pos_result["position_warning"]:
        diff = pos_result["current_position"] - pos_result["suggested_position"]
        mv = pos_result.get("market_vol", 0)
        suggestions.append(
            f"减仓 {diff:.0%} 至建议仓位 {pos_result['suggested_position']:.0%} 以下 "
            f"(依据: {pos_result.get('market_index_name', '市场')}波动率={mv:.1f}% → 查表建议上限{pos_result['suggested_position']:.0%})"
        )

    for v in pos_result["stock_violations"]:
        level = v.get("familiarity_level", "low")
        tag = FAMILIARITY_LEVEL_LABELS.get(level, "低") + "熟悉"
        suggestions.append(
            f"减仓{v['name']}至{v['limit']:.0%}以下 "
            f"(依据: {tag}上限{v['limit']:.0%}，当前占{v['weight']:.0%})"
        )

    for v in pos_result["sector_violations"]:
        suggestions.append(
            f"减仓{v['sector']}板块至{v['limit']:.0%}以下 "
            f"(依据: 板块上限{v['limit']:.0%}，当前占{v['weight']:.0%})"
        )

    # 止损建议
    for sl in sl_levels:
        if sl["signal"] == "stop_loss":
            atr = sl.get("atr") or 0
            sl_mult = _fmt_atr_multiplier(sl.get("stop_loss_atr_multiplier", 2.0))
            suggestions.append(
                f"🔴 {sl['name']}({sl['code']}) 已触及止损 "
                f"(依据: 成本{sl['cost_price']:.3f} - {sl_mult}×ATR{atr:.3f} = 止损价{sl['stop_loss']:.3f} → 现价{sl['current_price']:.3f} < 止损价)"
            )
        elif sl["signal"] == "trailing_stop":
            atr = sl.get("atr") or 0
            rh = sl.get("recent_high") or 0
            trail_mult = _fmt_atr_multiplier(sl.get("trailing_stop_atr_multiplier", 1.5))
            suggestions.append(
                f"🟠 {sl['name']}({sl['code']}) 触及移动止损 "
                f"(依据: 近14日最高{rh:.3f} - {trail_mult}×ATR{atr:.3f} = 触发价{sl['trailing_stop']:.3f} → 现价{sl['current_price']:.3f} ≤ 触发价)"
            )
        elif sl["signal"] == "take_profit":
            triggered = [t for t in sl["take_profit_tiers"] if t["triggered"]]
            if triggered:
                tiers_str = ", ".join(
                    f"+{t['trigger_pct']:.0%}触发卖出{int(t['sell_ratio']*100)}%"
                    for t in triggered
                )
                base = sl["cost_price"] if sl["cost_price"] > 0 else sl["current_price"]
                suggestions.append(
                    f"🟡 {sl['name']}({sl['code']}) 盈利 {sl['pnl_pct']:.1%}，建议分批止盈 "
                    f"(依据: 触发{tiers_str}，基准价{base:.3f})"
                )

    # 熔断建议
    if cb_result["action"]:
        cb_info = ", ".join(
            f"{p}{cb_result[p]['drawdown']:+.1%}"
            for p in ["daily", "weekly", "monthly"]
            if cb_result[p]["triggered"]
        )
        suggestions.append(
            f"组合熔断触发 ({cb_info})，"
            f"{ACTION_LABELS.get(cb_result['action'], cb_result['action'])}"
        )

    # 异常建议
    for sig in anomaly_result["signals"]:
        if sig["type"] == "vol_spike":
            suggestions.append(f"关注{sig['code']}波动率异常 ({sig['detail']})")
        elif sig["type"] == "liquidity_dry":
            suggestions.append(f"关注{sig['code']}流动性风险 ({sig['detail']})")

    return suggestions


def format_md_report(today, terminal_text):
    """包装为 Markdown 格式"""
    return f"# 风控检查报告 {today}\n\n```\n{terminal_text}\n```\n"


def _json_default(value):
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if pd.isna(value):
        return None
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


# ═══════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════

def build_risk_snapshot(total_equity):
    """执行完整风控检查并返回结构化快照"""
    today = datetime.now().strftime("%Y-%m-%d")

    # 1. 加载持仓
    portfolio_df = load_portfolio_from_toml(str(DEFAULT_PORTFOLIO_TOML))

    # 2. 获取行情
    prices_dict, market_prices, market_index_name = fetch_prices(portfolio_df)

    # 3. 丰富持仓数据
    portfolio_df = enrich_portfolio(portfolio_df, prices_dict)
    validate_portfolio_prices(portfolio_df)

    # 4. 市场波动率
    market_vol = 0.0
    if market_prices is not None and not market_prices.empty:
        market_vol = calc_realized_vol(market_prices, window=20)

    # 5. 第一道防线
    pos_result = check_positions(portfolio_df, total_equity, market_vol, market_index_name)

    # 6. 第二道防线
    sl_levels = calc_stop_take_levels(portfolio_df, prices_dict)
    cb_result = check_circuit_breaker(portfolio_df, prices_dict)

    # 7. 第三道防线
    anomaly_result = detect_anomalies(portfolio_df, prices_dict)

    # 8. 信号系统
    sig_state = load_state()
    clear_stale_signals(sig_state, portfolio_df["code"].astype(str).tolist())
    signal_results = run_all_signals(
        portfolio_df, prices_dict,
        state=sig_state,
        total_equity=total_equity,
        market_vol=market_vol,
        sl_levels=sl_levels,
    )
    active_signal_keys = {
        (sig["code"], sig.get("state_key", sig["strategy"]))
        for sig in signal_results
    }
    clear_inactive_signal_records(sig_state, active_signal_keys)
    alert_groups = classify_alerts(signal_results)
    save_state(sig_state)

    total_mv = float(portfolio_df["market_value"].sum())
    cash = float(total_equity - total_mv)

    return {
        "today": today,
        "data_freq": DATA_FREQ,
        "total_equity": float(total_equity),
        "portfolio_summary": {
            "holding_count": int(len(portfolio_df)),
            "total_market_value": total_mv,
            "cash": cash,
            "current_position": float(pos_result["current_position"]),
        },
        "market": {
            "index_name": market_index_name,
            "volatility": float(market_vol),
            "regime": get_regime_params(),
            "prices": market_prices.copy() if market_prices is not None else pd.DataFrame(),
        },
        "portfolio_df": portfolio_df.copy(),
        "prices_dict": prices_dict,
        "position": pos_result,
        "stop_loss": sl_levels,
        "circuit_breaker": cb_result,
        "anomaly": anomaly_result,
        "signals": signal_results,
        "alert_groups": alert_groups,
    }


def export_risk_snapshot(snapshot, output_path):
    """导出不含 DataFrame 的风险快照 JSON，供其他模块复用"""
    payload = {
        "today": snapshot["today"],
        "data_freq": snapshot["data_freq"],
        "total_equity": snapshot["total_equity"],
        "portfolio_summary": snapshot["portfolio_summary"],
        "market": {
            "index_name": snapshot["market"]["index_name"],
            "volatility": snapshot["market"]["volatility"],
            "regime": snapshot["market"]["regime"],
            "prices": snapshot["market"]["prices"].to_dict(orient="records"),
        },
        "portfolio": snapshot["portfolio_df"].to_dict(orient="records"),
        "position": snapshot["position"],
        "stop_loss": snapshot["stop_loss"],
        "circuit_breaker": snapshot["circuit_breaker"],
        "anomaly": snapshot["anomaly"],
        "signals": snapshot["signals"],
        "alert_groups": snapshot["alert_groups"],
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


def run_risk_check(total_equity):
    """执行完整风控检查"""
    snapshot = build_risk_snapshot(total_equity)
    today = snapshot["today"]
    print(f"风控检查 {today}")
    print(f"数据频率: {snapshot['data_freq']}")
    print()
    print("加载持仓...")
    print(f"  {snapshot['portfolio_summary']['holding_count']} 只持仓")
    print("获取行情数据...")
    print(f"  获取到 {len(snapshot['prices_dict'])} 只股票行情")
    print(f"  市场指数: {snapshot['market']['index_name']}")
    print(f"  {snapshot['market']['index_name']} 波动率: {snapshot['market']['volatility']:.1f}%")
    print("检查仓位...")
    print("计算止损止盈...")
    print("检测异常信号...")
    print("运行信号策略...")
    print(f"  {len(snapshot['signals'])} 条信号")

    # 9. 输出报告
    print()
    report_text = format_terminal_report(
        today, snapshot["portfolio_df"], total_equity,
        snapshot["position"], snapshot["stop_loss"],
        snapshot["circuit_breaker"], snapshot["anomaly"],
        alert_groups=snapshot["alert_groups"],
    )
    print(report_text)

    # 10. 保存 Markdown
    output_dir = Path(__file__).parent.parent.parent / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M')
    md_path = output_dir / f"risk_report_{ts}.md"
    md_path.write_text(format_md_report(today, report_text), encoding="utf-8")
    json_path = output_dir / f"risk_snapshot_{ts}.json"
    export_risk_snapshot(snapshot, json_path)
    print(f"\n报告已保存: {md_path}")
    print(f"快照已保存: {json_path}")
    return snapshot


def main():
    parser = argparse.ArgumentParser(description="风控检查报告")
    parser.add_argument("--equity", type=float, default=None,
                        help="总权益（含现金），不指定则从 portfolio.toml 读取")
    args = parser.parse_args()

    equity = args.equity
    if equity is None:
        try:
            account = load_account_config(str(DEFAULT_PORTFOLIO_TOML))
            equity = account.get("total_equity")
        except FileNotFoundError:
            pass
    if equity is None:
        parser.error("未指定 --equity 且 portfolio.toml 中无 [account].total_equity")

    run_risk_check(equity)


if __name__ == "__main__":
    main()
