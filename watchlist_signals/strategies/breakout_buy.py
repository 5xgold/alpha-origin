"""突破买点策略。"""

from watchlist_signals.registry import register_watch_signal
from watchlist_signals.state import get_trigger_days, record_signal
from risk_control.signals.alert import escalate_level


@register_watch_signal("breakout_buy")
def check(watchlist_df, *, state, latest_prices=None, **kwargs):
    latest_prices = latest_prices or {}
    results = []
    for _, row in watchlist_df.iterrows():
        breakout = row.get("breakout_price")
        price_info = latest_prices.get(str(row["code"]), {})
        current = price_info.get("current_price")
        previous = price_info.get("previous_close")
        if breakout is None or current is None or previous is None:
            continue
        if not (float(previous) < float(breakout) <= float(current)):
            continue

        code = str(row["code"])
        record = record_signal(state, code, "breakout_buy")
        level = escalate_level("watch", get_trigger_days(state, code, "breakout_buy"))
        results.append({
            "code": code,
            "name": str(row["name"]),
            "strategy": "breakout_buy",
            "signal_type": "buy",
            "alert_level": level,
            "title": "突破买点",
            "detail": f"昨收{float(previous):.2f}，现价{float(current):.2f}，向上突破{float(breakout):.2f}",
            "response_plan": "确认放量和板块联动后再决定是否追突破",
            "first_triggered": record.get("first_triggered", ""),
            "trigger_count": record.get("trigger_count", 1),
        })
    return results
