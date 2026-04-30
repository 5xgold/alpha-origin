"""信号策略注册表 — 装饰器 + dict 模式"""

_SIGNAL_REGISTRY = {}


def register_signal(name, signal_type="sell", enabled=True):
    """注册信号策略函数

    Args:
        name: 策略名称（唯一标识）
        signal_type: "buy" | "sell" | "alert"
        enabled: 是否默认启用

    Usage:
        @register_signal("my_strategy", signal_type="sell")
        def check(portfolio_df, prices_dict, *, state, **kwargs):
            return [...]
    """
    def decorator(fn):
        _SIGNAL_REGISTRY[name] = {
            "fn": fn,
            "enabled": enabled,
            "type": signal_type,
        }
        return fn
    return decorator


def run_all_signals(portfolio_df, prices_dict, *, state, **kwargs):
    """运行所有已启用的策略，返回信号列表

    Args:
        portfolio_df: DataFrame[code, name, quantity, cost_price, current_price, ...]
        prices_dict: {code: DataFrame[date, open, high, low, close, volume]}
        state: dict — 从 risk_state.json 加载的状态（可读写）
        **kwargs: 传递给策略的额外参数（total_equity, market_vol, sl_levels 等）

    Returns:
        list[dict]: 统一格式的信号列表
    """
    results = []
    for name, entry in _SIGNAL_REGISTRY.items():
        if not entry["enabled"]:
            continue
        try:
            signals = entry["fn"](portfolio_df, prices_dict, state=state, **kwargs)
            results.extend(signals)
        except Exception as e:
            print(f"  警告: 策略 {name} 执行失败: {e}")
    return results


def enable_signal(name):
    """启用指定策略"""
    if name in _SIGNAL_REGISTRY:
        _SIGNAL_REGISTRY[name]["enabled"] = True


def disable_signal(name):
    """禁用指定策略"""
    if name in _SIGNAL_REGISTRY:
        _SIGNAL_REGISTRY[name]["enabled"] = False


def list_signals():
    """列出所有已注册策略

    Returns:
        list[dict]: [{"name", "type", "enabled"}, ...]
    """
    return [
        {"name": name, "type": entry["type"], "enabled": entry["enabled"]}
        for name, entry in _SIGNAL_REGISTRY.items()
    ]
