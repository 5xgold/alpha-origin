"""总权益风险预算止损模式。"""


def resolve_stop_price(*, current, cost, quantity, total_equity, info, **kwargs):
    info["stop_loss_strategy"] = "equity_risk_budget"

    if total_equity is None or float(total_equity) <= 0:
        return None

    qty = float(quantity or 0)
    if qty <= 0:
        return None

    entry_price = float(cost) if float(cost) > 0 else float(current)
    position_cost = entry_price * qty
    if position_cost <= 0:
        return None

    position_weight = position_cost / float(total_equity)
    if position_weight <= 0:
        return None

    max_loss_pct = min(1.0, 0.02 / position_weight)
    info["position_weight"] = round(position_weight, 6)
    info["max_loss_pct"] = round(max_loss_pct, 6)
    return round(entry_price * (1 - max_loss_pct), 3)
