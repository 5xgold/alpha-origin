"""持仓周期管理 — 资金停滞 / 长期亏损 / 趋势走弱检测"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent.parent))
from risk_control.signals.registry import register_signal
from risk_control.signals.state import (
    record_signal, record_holding_first_seen,
    get_holding_days, get_trigger_days,
)
from risk_control.signals.alert import escalate_level
from risk_control.scripts.risk_calc import calc_ma
from risk_control.config import (
    HOLDING_PERIOD_STAGNATION_DAYS,
    HOLDING_PERIOD_STAGNATION_MIN_GAIN,
    HOLDING_PERIOD_DANGER_DAYS,
    HOLDING_PERIOD_DANGER_MIN_GAIN,
    HOLDING_PERIOD_WEAK_TREND_DAYS,
)


@register_signal("holding_period", signal_type="alert")
def check(portfolio_df, prices_dict, *, state, **kwargs):
    """检查持仓周期相关风险"""
    signals = []

    for _, row in portfolio_df.iterrows():
        code = str(row["code"])
        name = str(row["name"])
        cost = float(row["cost_price"])
        current = float(row["current_price"])

        if cost <= 0:
            continue

        pnl_pct = (current - cost) / cost

        # 记录首次出现（仅在不存在时写入）
        record_holding_first_seen(state, code)
        holding_days = get_holding_days(state, code)

        if holding_days <= 0:
            continue

        # 检查1: 长期亏损（danger）
        if (holding_days >= HOLDING_PERIOD_DANGER_DAYS
                and pnl_pct < HOLDING_PERIOD_DANGER_MIN_GAIN):
            record = record_signal(state, code, "holding_period_danger")
            trigger_days = get_trigger_days(state, code, "holding_period_danger")
            level = escalate_level("danger", trigger_days)

            signals.append({
                "code": code,
                "name": name,
                "strategy": "holding_period",
                "state_key": "holding_period_danger",
                "signal_type": "alert",
                "alert_level": level,
                "title": f"持仓{holding_days}天亏损{pnl_pct:.1%}",
                "detail": (
                    f"持仓超{HOLDING_PERIOD_DANGER_DAYS}天且收益{pnl_pct:.1%}"
                    f" < {HOLDING_PERIOD_DANGER_MIN_GAIN:.0%}，长期亏损"
                ),
                "response_plan": "建议止损，释放资金。分2批减仓，首批50%明日开盘执行",
                "first_triggered": record.get("first_triggered", ""),
                "trigger_count": record.get("trigger_count", 1),
            })
            continue  # danger 已覆盖，不再检查 stagnation

        # 检查2: 资金停滞（warning）
        if (holding_days >= HOLDING_PERIOD_STAGNATION_DAYS
                and pnl_pct < HOLDING_PERIOD_STAGNATION_MIN_GAIN):
            record = record_signal(state, code, "holding_period_stagnation")
            trigger_days = get_trigger_days(state, code, "holding_period_stagnation")
            level = escalate_level("warning", trigger_days)

            signals.append({
                "code": code,
                "name": name,
                "strategy": "holding_period",
                "state_key": "holding_period_stagnation",
                "signal_type": "alert",
                "alert_level": level,
                "title": f"持仓{holding_days}天收益{pnl_pct:.1%}，资金停滞",
                "detail": (
                    f"持仓超{HOLDING_PERIOD_STAGNATION_DAYS}天且收益{pnl_pct:.1%}"
                    f" < {HOLDING_PERIOD_STAGNATION_MIN_GAIN:.0%}"
                ),
                "response_plan": "考虑换仓或设定观察期限",
                "first_triggered": record.get("first_triggered", ""),
                "trigger_count": record.get("trigger_count", 1),
            })
            continue

        # 检查3: 趋势走弱（watch）
        if holding_days >= HOLDING_PERIOD_WEAK_TREND_DAYS:
            if code in prices_dict and not prices_dict[code].empty:
                ma20 = calc_ma(prices_dict[code], period=20)
                if ma20 is not None and current < ma20:
                    record = record_signal(state, code, "holding_period_weak")
                    trigger_days = get_trigger_days(state, code, "holding_period_weak")
                    level = escalate_level("watch", trigger_days)

                    signals.append({
                        "code": code,
                        "name": name,
                        "strategy": "holding_period",
                        "state_key": "holding_period_weak",
                        "signal_type": "alert",
                        "alert_level": level,
                        "title": f"持仓{holding_days}天，价格低于MA20",
                        "detail": (
                            f"现价{current:.3f} < MA20({ma20:.3f})，趋势走弱"
                        ),
                        "response_plan": "关注趋势变化，若持续走弱考虑减仓",
                        "first_triggered": record.get("first_triggered", ""),
                        "trigger_count": record.get("trigger_count", 1),
                    })

    return signals
