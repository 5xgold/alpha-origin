"""止损子策略注册表。"""

from risk_control.signals.strategies.stop_loss_modes.atr_stop import resolve_stop_price as resolve_atr_stop
from risk_control.signals.strategies.stop_loss_modes.entry_day_low_guard import (
    resolve_stop_price as resolve_entry_day_low_guard_stop,
)


_STOP_LOSS_MODE_REGISTRY = {
    "atr": resolve_atr_stop,
    "entry_day_low_guard": resolve_entry_day_low_guard_stop,
}


def resolve_stop_loss_price(strategy, **kwargs):
    fn = _STOP_LOSS_MODE_REGISTRY.get(strategy)
    if fn is None:
        fn = _STOP_LOSS_MODE_REGISTRY["atr"]
    return fn(**kwargs)


def list_stop_loss_strategies():
    return sorted(_STOP_LOSS_MODE_REGISTRY.keys())
