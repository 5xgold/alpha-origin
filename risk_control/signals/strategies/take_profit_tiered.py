"""分批止盈信号 — 包装现有 stop_loss.py 的止盈判定"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent.parent))
from risk_control.signals.registry import register_signal
from risk_control.signals.state import record_signal, get_trigger_days
from risk_control.signals.alert import escalate_level


@register_signal("take_profit_tiered", signal_type="sell")
def check(portfolio_df, prices_dict, *, state, sl_levels=None, **kwargs):
    """从 sl_levels 中提取止盈触发信号"""
    if not sl_levels:
        return []

    signals = []
    for sl in sl_levels:
        if sl["signal"] != "take_profit":
            continue

        code = sl["code"]
        triggered_tiers = [t for t in sl["take_profit_tiers"] if t["triggered"]]
        if not triggered_tiers:
            continue

        # 记录已触发的档位
        triggered_pcts = [t["trigger_pct"] for t in triggered_tiers]
        history_extra = {"tiers_triggered": triggered_pcts}

        history = record_signal(state, code, "take_profit_tiered", extra=history_extra)
        trigger_days = get_trigger_days(state, code, "take_profit_tiered")

        base_level = "warning"
        level = escalate_level(base_level, trigger_days)

        tiers_str = "、".join(
            f"+{t['trigger_pct']:.0%}卖{int(t['sell_ratio']*100)}%"
            for t in triggered_tiers
        )
        base = sl["cost_price"] if sl["cost_price"] > 0 else sl["current_price"]

        signals.append({
            "code": code,
            "name": sl["name"],
            "strategy": "take_profit_tiered",
            "state_key": "take_profit_tiered",
            "signal_type": "sell",
            "alert_level": level,
            "title": f"盈利{sl['pnl_pct']:.1%}，触发分批止盈",
            "detail": f"触发: {tiers_str}，基准价{base:.3f}",
            "response_plan": f"建议按档位分批止盈",
            "first_triggered": history.get("first_triggered", ""),
            "trigger_count": history.get("trigger_count", 1),
        })

    return signals
