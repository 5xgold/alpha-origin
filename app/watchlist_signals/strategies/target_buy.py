"""回调买点策略。"""

from watchlist_signals.registry import register_watch_signal
from watchlist_signals.state import get_trigger_days, record_signal
from risk_control.signals.alert import escalate_level


@register_watch_signal("target_buy")
def check(watchlist_df, *, state, latest_prices=None, **kwargs):
    latest_prices = latest_prices or {}
    results = []
    for _, row in watchlist_df.iterrows():
        target = row.get("target_buy_price")
        current = latest_prices.get(str(row["code"]), {}).get("current_price")
        if target is None or current is None:
            continue
        if float(current) > float(target):
            continue

        code = str(row["code"])
        record = record_signal(state, code, "target_buy")
        level = escalate_level("watch", get_trigger_days(state, code, "target_buy"))
        results.append({
            "code": code,
            "name": str(row["name"]),
            "strategy": "target_buy",
            "signal_type": "buy",
            "alert_level": level,
            "title": "回调到目标买点",
            "detail": f"现价{float(current):.2f} <= 目标买点{float(target):.2f}",
            "response_plan": "检查缩量、支撑和仓位计划后再决定是否分批买入",
            "first_triggered": record.get("first_triggered", ""),
            "trigger_count": record.get("trigger_count", 1),
        })
    return results
