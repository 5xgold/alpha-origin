"""总权益风险预算止损模式。"""

from risk_control.config import EQUITY_RISK_BUDGET_MAX_LOSS_PCT


def _resolve_budget_pct(risk_rules):
    if not isinstance(risk_rules, dict):
        return EQUITY_RISK_BUDGET_MAX_LOSS_PCT
    value = risk_rules.get("max_loss_pct_of_equity", EQUITY_RISK_BUDGET_MAX_LOSS_PCT)
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return EQUITY_RISK_BUDGET_MAX_LOSS_PCT
    if pct <= 0:
        return EQUITY_RISK_BUDGET_MAX_LOSS_PCT
    return min(pct, 1.0)


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

    risk_budget_pct = _resolve_budget_pct(kwargs.get("risk_rules"))
    max_loss_pct = min(1.0, risk_budget_pct / position_weight)
    info["max_loss_pct_of_equity"] = round(risk_budget_pct, 6)
    info["position_weight"] = round(position_weight, 6)
    info["max_loss_pct"] = round(max_loss_pct, 6)
    return round(entry_price * (1 - max_loss_pct), 3)
