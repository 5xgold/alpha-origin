"""轻量状态追踪 — data/cache/risk_state.json"""

import json
from pathlib import Path
from datetime import datetime, date

STATE_FILE = Path(__file__).parent.parent.parent / "data" / "cache" / "risk_state.json"

_EMPTY_STATE = {
    "_meta": {"version": 1, "last_updated": ""},
    "signals": {},
    "holdings_first_seen": {},
}


def load_state():
    """加载状态文件，不存在或损坏时返回空状态"""
    if not STATE_FILE.exists():
        return _new_state()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if "_meta" not in data:
            return _new_state()
        return data
    except (json.JSONDecodeError, KeyError):
        return _new_state()


def save_state(state):
    """保存状态到文件"""
    state["_meta"]["last_updated"] = datetime.now().isoformat(timespec="seconds")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def record_signal(state, code, strategy, extra=None):
    """记录一次信号触发

    Args:
        state: 状态 dict（会被原地修改）
        code: 股票代码
        strategy: 策略名称
        extra: 额外数据（如 tiers_triggered, phase 等）

    Returns:
        dict: 该信号的历史记录
    """
    today = _today_str()
    signals = state.setdefault("signals", {})
    stock = signals.setdefault(code, {})

    if strategy not in stock:
        stock[strategy] = {
            "first_triggered": today,
            "last_triggered": today,
            "trigger_count": 1,
        }
    else:
        record = stock[strategy]
        record["last_triggered"] = today
        record["trigger_count"] = record.get("trigger_count", 0) + 1

    if extra:
        stock[strategy].update(extra)

    return stock[strategy]


def get_signal_history(state, code, strategy):
    """获取某只股票某策略的历史记录

    Returns:
        dict | None
    """
    return state.get("signals", {}).get(code, {}).get(strategy)


def is_first_trigger(state, code, strategy):
    """判断是否首次触发"""
    history = get_signal_history(state, code, strategy)
    if history is None:
        return True
    return history.get("last_triggered", "") != _today_str()


def get_trigger_days(state, code, strategy):
    """计算信号持续天数（从首次触发到今天）"""
    history = get_signal_history(state, code, strategy)
    if history is None:
        return 0
    first = history.get("first_triggered", "")
    if not first:
        return 0
    try:
        first_date = date.fromisoformat(first)
        return (date.today() - first_date).days
    except ValueError:
        return 0


def record_holding_first_seen(state, code, date_str=None):
    """记录持仓首次出现日期（仅在不存在时写入）"""
    seen = state.setdefault("holdings_first_seen", {})
    if code not in seen:
        seen[code] = date_str or _today_str()


def get_holding_days(state, code):
    """计算持仓天数"""
    seen = state.get("holdings_first_seen", {}).get(code)
    if not seen:
        return 0
    try:
        first_date = date.fromisoformat(seen)
        return (date.today() - first_date).days
    except ValueError:
        return 0


def clear_stale_signals(state, active_codes):
    """清理已不在持仓中的股票的信号记录"""
    active = set(str(c) for c in active_codes)
    for section in ("signals", "holdings_first_seen"):
        data = state.get(section, {})
        stale = [k for k in data if k not in active and k != "_meta"]
        for k in stale:
            del data[k]


def clear_inactive_signal_records(state, active_signal_keys):
    """清理本次未触发的信号记录，避免间歇性信号被误判为持续触发"""
    active_by_code = {}
    for code, strategy in active_signal_keys:
        active_by_code.setdefault(str(code), set()).add(strategy)

    signals = state.get("signals", {})
    for code, records in list(signals.items()):
        if not isinstance(records, dict):
            continue
        active_strategies = active_by_code.get(str(code), set())
        stale_strategies = [name for name in records if name not in active_strategies]
        for name in stale_strategies:
            del records[name]


def _new_state():
    return json.loads(json.dumps(_EMPTY_STATE))


def _today_str():
    return date.today().isoformat()
