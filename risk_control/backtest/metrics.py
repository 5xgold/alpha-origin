"""回测后指标计算"""

import pandas as pd

from risk_control.scripts.risk_calc import calc_drawdown


def compute_metrics(result, prices_dict, initial_equity):
    """计算回测核心指标

    Args:
        result: BacktestResult
        prices_dict: 完整价格数据（用于计算 buy-and-hold 和信号验证）
        initial_equity: 初始总权益

    Returns:
        dict: 指标集合
    """
    if not result.daily_snapshots:
        return _empty_metrics()

    # 回测组合价值序列
    values = pd.Series(
        [s["portfolio_value"] for s in result.daily_snapshots],
        index=pd.to_datetime([s["date"] for s in result.daily_snapshots]),
    )

    # buy-and-hold 基线：用第一天的持仓不动，计算每日价值
    bh_values = _calc_buy_and_hold(result.daily_snapshots, prices_dict)

    # 最大回撤
    dd_rc = calc_drawdown(values)
    dd_bh = calc_drawdown(bh_values) if not bh_values.empty else {"max": 0.0}

    max_dd_rc = abs(dd_rc.get("max", 0.0))
    max_dd_bh = abs(dd_bh.get("max", 0.0))

    # 回撤减少比例
    dd_reduction = 0.0
    if max_dd_bh > 0:
        dd_reduction = (max_dd_bh - max_dd_rc) / max_dd_bh

    # 总收益
    final_value_rc = values.iloc[-1] if len(values) > 0 else initial_equity
    final_value_bh = bh_values.iloc[-1] if len(bh_values) > 0 else initial_equity
    return_rc = (final_value_rc - initial_equity) / initial_equity
    return_bh = (final_value_bh - initial_equity) / initial_equity

    # 信号准确率
    accuracy_5d, accuracy_20d, false_pos_5d, false_pos_20d = _calc_signal_accuracy(
        result.trades_executed, prices_dict
    )

    # 熔断触发次数
    cb_count = sum(
        1 for t in result.trades_executed
        if "circuit_breaker" in t.get("reason", "")
    )

    return {
        "max_drawdown_with_rc": max_dd_rc,
        "max_drawdown_buy_hold": max_dd_bh,
        "drawdown_reduction_pct": dd_reduction,
        "total_return_with_rc": return_rc,
        "total_return_buy_hold": return_bh,
        "return_impact": return_rc - return_bh,
        "signal_accuracy_5d": accuracy_5d,
        "signal_accuracy_20d": accuracy_20d,
        "false_positive_rate_5d": false_pos_5d,
        "false_positive_rate_20d": false_pos_20d,
        "circuit_breaker_triggers": cb_count,
        "total_signals_fired": len(result.signals_log),
        "total_trades_executed": len(result.trades_executed),
    }


def _calc_buy_and_hold(daily_snapshots, prices_dict):
    """计算 buy-and-hold 基线（第一天的持仓不动）"""
    if not daily_snapshots:
        return pd.Series(dtype=float)

    first_snapshot = daily_snapshots[0]
    initial_positions = first_snapshot.get("positions", {})
    initial_cash = first_snapshot.get("cash", 0.0)

    dates = [s["date"] for s in daily_snapshots]
    values = []

    for snapshot in daily_snapshots:
        day = snapshot["date"]
        total = initial_cash
        for code, pos_info in initial_positions.items():
            qty = pos_info["quantity"]
            # 从 prices_dict 获取当日收盘价
            price = _get_price_on_date(prices_dict, code, day)
            if price is None:
                price = pos_info.get("price", pos_info.get("market_value", 0) / max(qty, 1))
            total += qty * price
        values.append(total)

    return pd.Series(values, index=pd.to_datetime(dates))


def _get_price_on_date(prices_dict, code, date_str):
    """获取指定日期的收盘价"""
    df = prices_dict.get(code)
    if df is None or df.empty:
        return None
    dates = pd.to_datetime(df["date"])
    target = pd.Timestamp(date_str)
    match = df[dates == target]
    if not match.empty:
        return float(match.iloc[0]["close"])
    # 用最近的前一日
    before = df[dates <= target]
    if not before.empty:
        return float(before.iloc[-1]["close"])
    return None


def _calc_signal_accuracy(trades, prices_dict):
    """计算止损信号的准确率和误报率

    准确率：卖出后 N 日价格继续下跌的比例
    误报率：卖出后 N 日价格反弹超过卖出价的比例
    """
    stop_trades = [
        t for t in trades
        if t.get("reason", "") in ("stop_loss_basic", "trailing_stop", "dynamic_stop_upgrade")
        or "stop_loss" in t.get("reason", "")
    ]

    if not stop_trades:
        return 0.0, 0.0, 0.0, 0.0

    accurate_5d = 0
    accurate_20d = 0
    false_pos_5d = 0
    false_pos_20d = 0
    valid_5d = 0
    valid_20d = 0

    for trade in stop_trades:
        code = trade["code"]
        sell_price = trade["price"]
        sell_date = pd.Timestamp(trade["date"])

        df = prices_dict.get(code)
        if df is None or df.empty:
            continue

        dates = pd.to_datetime(df["date"])
        future = df[dates > sell_date].head(20)

        if len(future) >= 5:
            valid_5d += 1
            close_5d = float(future.iloc[4]["close"])
            if close_5d < sell_price:
                accurate_5d += 1
            elif close_5d > sell_price:
                false_pos_5d += 1

        if len(future) >= 20:
            valid_20d += 1
            close_20d = float(future.iloc[19]["close"])
            if close_20d < sell_price:
                accurate_20d += 1
            elif close_20d > sell_price:
                false_pos_20d += 1

    acc_5d = accurate_5d / valid_5d if valid_5d > 0 else 0.0
    acc_20d = accurate_20d / valid_20d if valid_20d > 0 else 0.0
    fp_5d = false_pos_5d / valid_5d if valid_5d > 0 else 0.0
    fp_20d = false_pos_20d / valid_20d if valid_20d > 0 else 0.0

    return acc_5d, acc_20d, fp_5d, fp_20d


def _empty_metrics():
    return {
        "max_drawdown_with_rc": 0.0,
        "max_drawdown_buy_hold": 0.0,
        "drawdown_reduction_pct": 0.0,
        "total_return_with_rc": 0.0,
        "total_return_buy_hold": 0.0,
        "return_impact": 0.0,
        "signal_accuracy_5d": 0.0,
        "signal_accuracy_20d": 0.0,
        "false_positive_rate_5d": 0.0,
        "false_positive_rate_20d": 0.0,
        "circuit_breaker_triggers": 0,
        "total_signals_fired": 0,
        "total_trades_executed": 0,
    }
