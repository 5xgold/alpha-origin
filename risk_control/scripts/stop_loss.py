"""第二道防线：止损止盈 + 组合熔断"""

import sys
from pathlib import Path
import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.parent))
from risk_control.scripts.risk_calc import calc_atr, calc_portfolio_values, calc_drawdown
from risk_control.config import (
    ATR_PERIOD,
    STOP_LOSS_ATR_MULTIPLIER,
    TAKE_PROFIT_TIERS,
    TRAILING_STOP_ATR_MULTIPLIER,
    CIRCUIT_BREAKER,
    PORTFOLIO_LOOKBACK_DAYS,
)


def calc_stop_take_levels(portfolio_df, prices_dict):
    """计算每只持仓的止损/止盈/移动止损价位

    Args:
        portfolio_df: DataFrame[code, name, quantity, cost_price, current_price]
        prices_dict: {code: DataFrame[date, open, high, low, close, volume]}

    Returns:
        list[dict]: 每只股票的止损止盈信息
    """
    results = []

    for _, row in portfolio_df.iterrows():
        code = str(row["code"])
        name = str(row["name"])
        cost = float(row["cost_price"])
        current = float(row["current_price"])
        qty = float(row["quantity"])

        info = {
            "code": code,
            "name": name,
            "cost_price": cost,
            "current_price": current,
            "quantity": qty,
            "stop_loss": None,
            "take_profit_tiers": [],
            "trailing_stop": None,
            "signal": "hold",
            "pnl_pct": 0.0,
        }

        if cost > 0:
            info["pnl_pct"] = (current - cost) / cost

        if code not in prices_dict or prices_dict[code].empty:
            results.append(info)
            continue

        df = prices_dict[code]
        atr_series = calc_atr(df, period=ATR_PERIOD)
        if atr_series.empty:
            results.append(info)
            continue

        atr = float(atr_series.iloc[-1])

        # 止损价 = 成本 - N×ATR
        if cost > 0:
            info["stop_loss"] = round(cost - STOP_LOSS_ATR_MULTIPLIER * atr, 3)
        else:
            # 成本为0（如担保品划入），用当前价
            info["stop_loss"] = round(current - STOP_LOSS_ATR_MULTIPLIER * atr, 3)

        # 分批止盈
        base = cost if cost > 0 else current
        for pct, ratio in TAKE_PROFIT_TIERS:
            tp_price = round(base * (1 + pct), 3)
            info["take_profit_tiers"].append({
                "trigger_pct": pct,
                "price": tp_price,
                "sell_ratio": ratio,
                "triggered": current >= tp_price,
            })

        # 移动止损 = 近期最高价 - N×ATR
        recent_high = float(df["high"].astype(float).iloc[-ATR_PERIOD:].max())
        info["trailing_stop"] = round(recent_high - TRAILING_STOP_ATR_MULTIPLIER * atr, 3)

        # 信号判定
        if current <= info["stop_loss"]:
            info["signal"] = "stop_loss"
        elif any(t["triggered"] for t in info["take_profit_tiers"]):
            info["signal"] = "take_profit"
        elif info["trailing_stop"] and current <= info["trailing_stop"]:
            info["signal"] = "trailing_stop"
        else:
            info["signal"] = "hold"

        results.append(info)

    return results


def check_circuit_breaker(portfolio_df, prices_dict):
    """组合级熔断检查

    Args:
        portfolio_df: DataFrame[code, quantity, cost_price]
        prices_dict: {code: DataFrame[date, open, high, low, close, volume]}

    Returns:
        dict: {
            daily: {drawdown, threshold, triggered},
            weekly: {drawdown, threshold, triggered},
            monthly: {drawdown, threshold, triggered},
            action: str | None,
        }
    """
    pv = calc_portfolio_values(portfolio_df, prices_dict, lookback_days=PORTFOLIO_LOOKBACK_DAYS)

    result = {
        "daily": {"drawdown": 0.0, "threshold": CIRCUIT_BREAKER["daily"], "triggered": False},
        "weekly": {"drawdown": 0.0, "threshold": CIRCUIT_BREAKER["weekly"], "triggered": False},
        "monthly": {"drawdown": 0.0, "threshold": CIRCUIT_BREAKER["monthly"], "triggered": False},
        "action": None,
    }

    if pv.empty or len(pv) < 2:
        return result

    # 日回撤
    if len(pv) >= 2:
        daily_ret = (pv.iloc[-1] - pv.iloc[-2]) / pv.iloc[-2]
        result["daily"]["drawdown"] = float(daily_ret)
        if daily_ret <= -CIRCUIT_BREAKER["daily"]:
            result["daily"]["triggered"] = True

    # 周回撤（最近5个交易日）
    if len(pv) >= 6:
        weekly_ret = (pv.iloc[-1] - pv.iloc[-6]) / pv.iloc[-6]
        result["weekly"]["drawdown"] = float(weekly_ret)
        if weekly_ret <= -CIRCUIT_BREAKER["weekly"]:
            result["weekly"]["triggered"] = True

    # 月回撤（最近20个交易日）
    if len(pv) >= 21:
        monthly_ret = (pv.iloc[-1] - pv.iloc[-21]) / pv.iloc[-21]
        result["monthly"]["drawdown"] = float(monthly_ret)
        if monthly_ret <= -CIRCUIT_BREAKER["monthly"]:
            result["monthly"]["triggered"] = True

    # 最严重的触发决定动作
    if result["monthly"]["triggered"]:
        result["action"] = "liquidate"
    elif result["weekly"]["triggered"]:
        result["action"] = "reduce_50"
    elif result["daily"]["triggered"]:
        result["action"] = "warning"

    return result
