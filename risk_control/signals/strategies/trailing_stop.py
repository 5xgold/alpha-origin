"""移动止损信号 — 包装现有 stop_loss.py 的移动止损判定"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent.parent))
from risk_control.signals.registry import register_signal
from risk_control.signals.state import record_signal, get_trigger_days
from risk_control.signals.alert import escalate_level


@register_signal("trailing_stop", signal_type="sell")
def check(portfolio_df, prices_dict, *, state, sl_levels=None, **kwargs):
    """从 sl_levels 中提取移动止损触发信号"""
    if not sl_levels:
        return []

    signals = []
    for sl in sl_levels:
        if sl["signal"] != "trailing_stop":
            continue

        code = sl["code"]
        atr = sl.get("atr") or 0
        rh = sl.get("recent_high") or 0

        history = record_signal(state, code, "trailing_stop")
        trigger_days = get_trigger_days(state, code, "trailing_stop")
        trail_mult = sl.get("trailing_stop_atr_multiplier", 1.5)

        base_level = "warning"
        level = escalate_level(base_level, trigger_days)

        signals.append({
            "code": code,
            "name": sl["name"],
            "strategy": "trailing_stop",
            "state_key": "trailing_stop",
            "signal_type": "sell",
            "alert_level": level,
            "title": f"触及移动止损{sl['trailing_stop']:.3f}",
            "detail": (
                f"近14日最高{rh:.3f} - {trail_mult:.1f}×ATR{atr:.3f} = "
                f"触发价{sl['trailing_stop']:.3f}，现价{sl['current_price']:.3f}"
            ),
            "response_plan": f"建议减仓1/3，观察是否企稳",
            "first_triggered": history.get("first_triggered", ""),
            "trigger_count": history.get("trigger_count", 1),
        })

    return signals
