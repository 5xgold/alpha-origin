"""观察列表信号注册表。"""

_SIGNAL_REGISTRY = {}


def register_watch_signal(name, enabled=True):
    def decorator(fn):
        _SIGNAL_REGISTRY[name] = {
            "fn": fn,
            "enabled": enabled,
        }
        return fn
    return decorator


def run_all_watch_signals(watchlist_df, *, state, **kwargs):
    results = []
    for name, entry in _SIGNAL_REGISTRY.items():
        if not entry["enabled"]:
            continue
        try:
            signals = entry["fn"](watchlist_df, state=state, **kwargs)
            results.extend(signals)
        except Exception as exc:
            print(f"  警告: 观察策略 {name} 执行失败: {exc}")
    return results
