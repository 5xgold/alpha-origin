"""加仓/金字塔策略 — 价格跌至支撑位时提示加仓机会"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent.parent))
from risk_control.signals.registry import register_signal
from risk_control.signals.state import record_signal, get_signal_history, get_trigger_days
from risk_control.signals.alert import escalate_level
from risk_control.scripts.risk_calc import calc_support_levels
from risk_control.config import (
    PYRAMID_ADD_RATIOS,
    PYRAMID_SUPPORT_METHODS,
    PYRAMID_MIN_DROP_PCT,
    FAMILIARITY_DIMENSIONS,
    FAMILIARITY_POSITION_TIERS,
    get_familiarity_level,
)


@register_signal("add_position", signal_type="buy")
def check(portfolio_df, prices_dict, *, state, total_equity=0, **kwargs):
    """检查是否有加仓机会（受仓位上限约束）"""
    if total_equity <= 0:
        return []

    signals = []

    for _, row in portfolio_df.iterrows():
        code = str(row["code"])
        name = str(row["name"])
        cost = float(row["cost_price"])
        current = float(row["current_price"])
        mv = float(row.get("market_value", 0))

        if cost <= 0 or current <= 0:
            continue

        # 必须跌够才考虑加仓
        drop_pct = (cost - current) / cost
        if drop_pct < PYRAMID_MIN_DROP_PCT:
            continue

        # 检查仓位上限约束
        fam_detail = row.get("familiarity_detail", {})
        if not isinstance(fam_detail, dict):
            fam_detail = {}
        true_count = sum(1 for d in FAMILIARITY_DIMENSIONS if fam_detail.get(d, False))
        fam_level = get_familiarity_level(true_count)
        position_limit = FAMILIARITY_POSITION_TIERS[fam_level]

        current_weight = mv / total_equity if total_equity > 0 else 0
        if current_weight >= position_limit:
            # 已达仓位上限，不建议加仓
            continue

        # 检查是否接近支撑位
        if code not in prices_dict or prices_dict[code].empty:
            continue

        supports = calc_support_levels(prices_dict[code], methods=PYRAMID_SUPPORT_METHODS)
        if not supports:
            continue

        # 找到最近的支撑位
        near_supports = []
        for s in supports:
            distance_pct = abs(current - s["price"]) / current
            if distance_pct <= 0.03:  # 距支撑位3%以内
                near_supports.append(s)

        if not near_supports:
            continue

        # 确定加仓比例（金字塔递减）
        history = get_signal_history(state, code, "add_position")
        add_count = 0
        if history:
            add_count = history.get("add_count", 0)

        if add_count >= len(PYRAMID_ADD_RATIOS):
            # 已达最大加仓次数
            continue

        add_ratio = PYRAMID_ADD_RATIOS[add_count]
        remaining_room = position_limit - current_weight
        support_str = "、".join(f"{s['method']}{s['price']:.3f}" for s in near_supports)

        record = record_signal(state, code, "add_position", extra={"add_count": add_count + 1})
        trigger_days = get_trigger_days(state, code, "add_position")
        level = escalate_level("watch", trigger_days)

        signals.append({
            "code": code,
            "name": name,
            "strategy": "add_position",
            "state_key": "add_position",
            "signal_type": "buy",
            "alert_level": level,
            "title": f"接近支撑位{support_str}，可考虑加仓",
            "detail": (
                f"跌幅{drop_pct:.1%}，接近{support_str}，"
                f"仓位{current_weight:.0%}/{position_limit:.0%}(余{remaining_room:.0%})"
            ),
            "response_plan": (
                f"第{add_count+1}次加仓，建议加仓比例{add_ratio:.0%}，"
                f"仓位上限{position_limit:.0%}"
            ),
            "first_triggered": record.get("first_triggered", ""),
            "trigger_count": record.get("trigger_count", 1),
        })

    return signals
