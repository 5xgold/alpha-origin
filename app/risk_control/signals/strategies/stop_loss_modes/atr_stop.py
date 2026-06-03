"""ATR 止损模式。"""


def resolve_stop_price(*, current, cost, atr, default_sl_mult, info, **kwargs):
    info["stop_loss_strategy"] = "atr"
    if cost > 0:
        return round(cost - default_sl_mult * atr, 3)
    return round(current - default_sl_mult * atr, 3)
