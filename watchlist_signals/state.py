"""观察列表信号状态。"""

import json
from datetime import date, datetime
from pathlib import Path


STATE_FILE = Path(__file__).parent.parent / "data" / "cache" / "watchlist_state.json"

_EMPTY_STATE = {
    "_meta": {"version": 1, "last_updated": ""},
    "signals": {},
}


def load_state():
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
    state["_meta"]["last_updated"] = datetime.now().isoformat(timespec="seconds")
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def record_signal(state, code, strategy, extra=None):
    today = date.today().isoformat()
    code = str(code)
    signals = state.setdefault("signals", {})
    stock = signals.setdefault(code, {})
    if strategy not in stock:
        stock[strategy] = {
            "first_triggered": today,
            "last_triggered": today,
            "trigger_count": 1,
        }
    else:
        stock[strategy]["last_triggered"] = today
        stock[strategy]["trigger_count"] = stock[strategy].get("trigger_count", 0) + 1
    if extra:
        stock[strategy].update(extra)
    return stock[strategy]


def get_trigger_days(state, code, strategy):
    record = state.get("signals", {}).get(str(code), {}).get(strategy)
    if not record or not record.get("first_triggered"):
        return 0
    first_date = date.fromisoformat(record["first_triggered"])
    return (date.today() - first_date).days


def clear_stale_signals(state, active_codes):
    active = {str(code) for code in active_codes}
    signals = state.get("signals", {})
    stale = [code for code in signals if code not in active]
    for code in stale:
        del signals[code]


def clear_inactive_signal_records(state, active_signal_keys):
    active_by_code = {}
    for code, strategy in active_signal_keys:
        active_by_code.setdefault(str(code), set()).add(strategy)
    for code, records in list(state.get("signals", {}).items()):
        active_strategies = active_by_code.get(str(code), set())
        stale = [name for name in records if name not in active_strategies]
        for name in stale:
            del records[name]


def _new_state():
    return json.loads(json.dumps(_EMPTY_STATE))
