"""信号插件系统 — 对外接口"""

from risk_control.signals.registry import run_all_signals, list_signals, enable_signal, disable_signal
from risk_control.signals.state import (
    load_state,
    save_state,
    clear_stale_signals,
    clear_inactive_signal_records,
)
from risk_control.signals.alert import classify_alerts, format_alert_section

# 导入所有策略（触发 @register_signal 装饰器注册）
import risk_control.signals.strategies  # noqa: F401

__all__ = [
    "run_all_signals",
    "list_signals",
    "enable_signal",
    "disable_signal",
    "load_state",
    "save_state",
    "clear_stale_signals",
    "clear_inactive_signal_records",
    "classify_alerts",
    "format_alert_section",
]
