"""动态止损升级 — 盈利阶段自动提升止损价，只升不降"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent.parent))
from risk_control.signals.registry import register_signal
from risk_control.signals.state import record_signal, get_signal_history, get_trigger_days
from risk_control.signals.alert import escalate_level
from risk_control.scripts.risk_calc import calc_atr
from risk_control.config import (
    ATR_PERIOD,
    DYNAMIC_STOP_PHASES,
    DYNAMIC_TRAILING_TIGHT_MULTIPLIER,
)


def _calc_phase_stop(phase_cfg, cost, current, atr, recent_high):
    """根据阶段配置计算止损价"""
    stop_at = phase_cfg["stop_at"]
    if stop_at == "breakeven":
        return cost
    elif stop_at == "cost_plus_8pct":
        return round(cost * 1.08, 3)
    elif stop_at == "trailing_tight":
        return round(recent_high - DYNAMIC_TRAILING_TIGHT_MULTIPLIER * atr, 3)
    return None


@register_signal("dynamic_stop_upgrade", signal_type="sell")
def check(portfolio_df, prices_dict, *, state, sl_levels=None, **kwargs):
    """检查每只持仓的动态止损升级状态"""
    signals = []

    for _, row in portfolio_df.iterrows():
        code = str(row["code"])
        name = str(row["name"])
        cost = float(row["cost_price"])
        current = float(row["current_price"])

        if cost <= 0 or current <= 0:
            continue

        pnl_pct = (current - cost) / cost

        # 需要行情数据计算 ATR
        if code not in prices_dict or prices_dict[code].empty:
            continue

        df = prices_dict[code]
        atr_series = calc_atr(df, period=ATR_PERIOD)
        if atr_series.empty:
            continue

        atr = float(atr_series.iloc[-1])
        recent_high = float(df["high"].astype(float).iloc[-ATR_PERIOD:].max())

        # 确定当前应处于哪个阶段
        current_phase = -1
        for i, phase in enumerate(DYNAMIC_STOP_PHASES):
            if pnl_pct >= phase["min_profit"]:
                current_phase = i

        if current_phase < 0:
            # 盈利不足，不触发动态升级
            continue

        # 读取历史阶段（只升不降）
        history = get_signal_history(state, code, "dynamic_stop_upgrade")
        prev_phase = -1
        prev_stop = 0.0
        if history:
            prev_phase = history.get("phase", -1)
            prev_stop = history.get("stop_price", 0.0)

        # 取最高阶段
        effective_phase = max(current_phase, prev_phase)
        phase_cfg = DYNAMIC_STOP_PHASES[effective_phase]

        # 计算新止损价
        new_stop = _calc_phase_stop(phase_cfg, cost, current, atr, recent_high)
        if new_stop is None:
            continue

        # 止损只升不降
        effective_stop = max(new_stop, prev_stop)

        # 记录状态
        extra = {"phase": effective_phase, "stop_price": effective_stop}
        record = record_signal(state, code, "dynamic_stop_upgrade", extra=extra)
        trigger_days = get_trigger_days(state, code, "dynamic_stop_upgrade")

        # 判断是否触发止损
        if current <= effective_stop:
            base_level = "warning"
            level = escalate_level(base_level, trigger_days)
            signals.append({
                "code": code,
                "name": name,
                "strategy": "dynamic_stop_upgrade",
                "state_key": "dynamic_stop_upgrade",
                "signal_type": "sell",
                "alert_level": level,
                "title": f"触及动态止损{effective_stop:.3f}({phase_cfg['label']})",
                "detail": (
                    f"盈利{pnl_pct:.1%}，Phase {effective_phase+1}({phase_cfg['label']})，"
                    f"止损价{effective_stop:.3f}，现价{current:.3f}"
                ),
                "response_plan": "建议按止损策略执行（止损策略待用户自定义）",
                "first_triggered": record.get("first_triggered", ""),
                "trigger_count": record.get("trigger_count", 1),
            })
        else:
            # 阶段升级通知（仅在阶段变化时）
            if effective_phase > prev_phase:
                base_level = "watch"
                signals.append({
                    "code": code,
                    "name": name,
                    "strategy": "dynamic_stop_upgrade",
                    "state_key": "dynamic_stop_upgrade",
                    "signal_type": "alert",
                    "alert_level": base_level,
                    "title": f"止损升级至{phase_cfg['label']}({effective_stop:.3f})",
                    "detail": (
                        f"盈利{pnl_pct:.1%}，止损从{prev_stop:.3f}升至{effective_stop:.3f}"
                    ),
                    "response_plan": "止损价上移，无需操作",
                    "first_triggered": record.get("first_triggered", ""),
                    "trigger_count": record.get("trigger_count", 1),
                })

    return signals
