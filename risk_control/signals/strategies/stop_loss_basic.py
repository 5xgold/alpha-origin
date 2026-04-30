"""基础止损信号 — 包装现有 stop_loss.py 的止损判定"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent.parent))
from risk_control.signals.registry import register_signal
from risk_control.signals.state import record_signal, get_trigger_days
from risk_control.signals.alert import escalate_level


@register_signal("stop_loss_basic", signal_type="sell")
def check(portfolio_df, prices_dict, *, state, sl_levels=None, **kwargs):
    """从 sl_levels 中提取止损触发信号"""
    if not sl_levels:
        return []

    signals = []
    for sl in sl_levels:
        if sl["signal"] != "stop_loss":
            continue

        code = sl["code"]
        atr = sl.get("atr") or 0
        history = record_signal(state, code, "stop_loss_basic")
        trigger_days = get_trigger_days(state, code, "stop_loss_basic")
        sl_mult = sl.get("stop_loss_atr_multiplier", 2.0)

        base_level = "danger"
        level = escalate_level(base_level, trigger_days)

        signals.append({
            "code": code,
            "name": sl["name"],
            "strategy": "stop_loss_basic",
            "state_key": "stop_loss_basic",
            "signal_type": "sell",
            "alert_level": level,
            "title": f"已触及止损价{sl['stop_loss']:.3f}",
            "detail": (
                f"成本{sl['cost_price']:.3f} - {sl_mult:.1f}×ATR{atr:.3f} = "
                f"止损{sl['stop_loss']:.3f}，现价{sl['current_price']:.3f}"
            ),
            "response_plan": (
                f"建议止损卖出（止损策略待用户自定义）"
            ),
            "first_triggered": history.get("first_triggered", ""),
            "trigger_count": history.get("trigger_count", 1),
        })

    return signals
