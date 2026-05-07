"""策略插件自动加载 — 导入本目录下所有策略模块"""

from risk_control.signals.strategies import (  # noqa: F401
    entry_day_low_guard,
    stop_loss_modes,
    stop_loss_basic,
    take_profit_tiered,
    trailing_stop,
    dynamic_stop_upgrade,
    holding_period,
    add_position,
)
