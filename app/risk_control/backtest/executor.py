"""信号 → 仓位变动的执行逻辑"""


# 信号优先级：数字越大越激进
_SIGNAL_PRIORITY = {
    "stop_loss": 3,
    "trailing_stop": 2,
    "take_profit": 1,
}


def execute_signals(signals, circuit_breaker, portfolio_state, next_day_prices, execution_date):
    """根据信号和熔断结果执行交易

    Args:
        signals: list[dict] — run_all_signals 的输出
        circuit_breaker: dict — check_circuit_breaker 的输出
        portfolio_state: dict — {positions: {code: {quantity, cost_price, name}}, cash: float}
        next_day_prices: dict — {code: open_price} 次日开盘价
        execution_date: str — 执行日期

    Returns:
        list[dict]: 已执行的交易列表
    """
    trades = []
    positions = portfolio_state["positions"]

    # 熔断优先：如果触发组合级熔断，先处理
    cb_action = circuit_breaker.get("action")
    if cb_action == "liquidate":
        for code in list(positions.keys()):
            trade = _sell_position(
                code, positions, next_day_prices, execution_date,
                ratio=1.0, reason="circuit_breaker_liquidate",
            )
            if trade:
                trades.append(trade)
                portfolio_state["cash"] += trade["proceeds"]
        return trades
    elif cb_action == "reduce_50":
        for code in list(positions.keys()):
            trade = _sell_position(
                code, positions, next_day_prices, execution_date,
                ratio=0.5, reason="circuit_breaker_reduce_50",
            )
            if trade:
                trades.append(trade)
                portfolio_state["cash"] += trade["proceeds"]
        return trades

    # 个股信号：同一股票多信号取最激进
    stock_actions = {}
    for sig in signals:
        code = sig.get("code", "")
        strategy = sig.get("strategy", "")
        signal_type = sig.get("signal_type", "")

        if signal_type != "sell":
            continue
        if code not in positions:
            continue

        # 确定动作类型
        action_type = _classify_signal(strategy)
        if action_type is None:
            continue

        priority = _SIGNAL_PRIORITY.get(action_type, 0)
        existing = stock_actions.get(code)
        if existing is None or priority > existing["priority"]:
            stock_actions[code] = {
                "action_type": action_type,
                "priority": priority,
                "strategy": strategy,
                "signal": sig,
            }

    # 执行个股信号
    for code, action in stock_actions.items():
        action_type = action["action_type"]
        strategy = action["strategy"]

        if action_type == "stop_loss" or action_type == "trailing_stop":
            ratio = 1.0
        elif action_type == "take_profit":
            ratio = _get_take_profit_ratio(action["signal"])
        else:
            ratio = 1.0

        trade = _sell_position(
            code, positions, next_day_prices, execution_date,
            ratio=ratio, reason=strategy,
        )
        if trade:
            trades.append(trade)
            portfolio_state["cash"] += trade["proceeds"]

    return trades


def _sell_position(code, positions, next_day_prices, execution_date, ratio, reason):
    """执行卖出"""
    if code not in positions:
        return None
    pos = positions[code]
    qty = pos["quantity"]
    if qty <= 0:
        return None

    price = next_day_prices.get(code)
    if price is None or price <= 0:
        return None

    sell_qty = int(qty * ratio)
    if sell_qty <= 0:
        sell_qty = qty  # 至少卖1股（避免浮点精度问题）

    proceeds = sell_qty * price
    pos["quantity"] -= sell_qty

    # 清除空仓
    if pos["quantity"] <= 0:
        del positions[code]

    return {
        "date": execution_date,
        "code": code,
        "name": pos.get("name", code),
        "action": "sell",
        "quantity": sell_qty,
        "price": price,
        "proceeds": proceeds,
        "reason": reason,
    }


def _classify_signal(strategy):
    """将策略名映射到动作类型"""
    if "stop_loss" in strategy:
        return "stop_loss"
    elif "trailing" in strategy:
        return "trailing_stop"
    elif "take_profit" in strategy or "tiered" in strategy:
        return "take_profit"
    elif "dynamic_stop" in strategy:
        return "stop_loss"
    return None


def _get_take_profit_ratio(signal):
    """从信号中提取止盈卖出比例，默认 1/3"""
    detail = signal.get("detail", "")
    # 信号 detail 中可能包含 sell_ratio 信息
    # 默认按第一档 1/3
    return 1 / 3
