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
    get_regime_params,
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
    regime = get_regime_params()
    sl_mult = STOP_LOSS_ATR_MULTIPLIER * regime["stop_loss_multiplier"]
    trail_mult = TRAILING_STOP_ATR_MULTIPLIER * regime["trailing_stop_multiplier"]
    tp_mult = regime["take_profit_multiplier"]

    for _, row in portfolio_df.iterrows():
        code = str(row["code"])
        name = str(row["name"])
        cost = float(row["cost_price"])
        current = float(row["current_price"])
        qty = float(row["quantity"])
        risk_rules = row.get("risk_rules", {})
        if not isinstance(risk_rules, dict):
            risk_rules = {}

        row_sl_mult = float(risk_rules.get("stop_loss_atr_multiplier", sl_mult) or sl_mult)
        row_trail_mult = float(risk_rules.get("trailing_stop_atr_multiplier", trail_mult) or trail_mult)
        row_tp_tiers = _resolve_take_profit_tiers(risk_rules, cost if cost > 0 else current, tp_mult)

        info = {
            "code": code,
            "name": name,
            "cost_price": cost,
            "current_price": current,
            "quantity": qty,
            "stop_loss": None,
            "atr": None,
            "recent_high": None,
            "take_profit_tiers": [],
            "trailing_stop": None,
            "signal": "hold",
            "pnl_pct": 0.0,
            "stop_loss_atr_multiplier": row_sl_mult,
            "trailing_stop_atr_multiplier": row_trail_mult,
            "take_profit_multiplier": tp_mult,
            "risk_rules": risk_rules,
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
        info["atr"] = atr

        # 止损价 = 成本 - N×ATR（受市场区间调节）
        if cost > 0:
            info["stop_loss"] = round(cost - row_sl_mult * atr, 3)
        else:
            info["stop_loss"] = round(current - row_sl_mult * atr, 3)

        # 分批止盈（止盈目标受市场区间调节）
        for pct, ratio in row_tp_tiers:
            tp_price = round((cost if cost > 0 else current) * (1 + pct), 3)
            info["take_profit_tiers"].append({
                "trigger_pct": pct,
                "price": tp_price,
                "sell_ratio": ratio,
                "triggered": current >= tp_price,
            })

        # 移动止损 = 近期最高价 - N×ATR（受市场区间调节）
        recent_high = float(df["high"].astype(float).iloc[-ATR_PERIOD:].max())
        info["recent_high"] = recent_high
        info["trailing_stop"] = round(recent_high - row_trail_mult * atr, 3)

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


def _resolve_take_profit_tiers(risk_rules, base_price, tp_mult):
    custom = risk_rules.get("take_profit_tiers", [])
    if isinstance(custom, list) and custom:
        tiers = []
        for item in custom:
            if not isinstance(item, dict):
                continue
            trigger_pct = item.get("trigger_pct")
            sell_ratio = item.get("sell_ratio")
            if trigger_pct is None or sell_ratio is None:
                continue
            tiers.append((float(trigger_pct), float(sell_ratio)))
        if tiers:
            return tiers

    return [(pct * tp_mult, ratio) for pct, ratio in TAKE_PROFIT_TIERS]


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
    regime = get_regime_params()
    cb_mult = regime["circuit_breaker_multiplier"]

    result = {
        "daily": {"drawdown": 0.0, "threshold": CIRCUIT_BREAKER["daily"] * cb_mult, "triggered": False},
        "weekly": {"drawdown": 0.0, "threshold": CIRCUIT_BREAKER["weekly"] * cb_mult, "triggered": False},
        "monthly": {"drawdown": 0.0, "threshold": CIRCUIT_BREAKER["monthly"] * cb_mult, "triggered": False},
        "action": None,
    }

    if pv.empty or len(pv) < 2:
        return result

    # 日回撤：最近 2 个交易日的当前回撤
    if len(pv) >= 2:
        daily_dd = calc_drawdown(pv.iloc[-2:])["current"]
        result["daily"]["drawdown"] = float(daily_dd)
        if daily_dd <= -result["daily"]["threshold"]:
            result["daily"]["triggered"] = True

    # 周回撤：最近 5 个交易日窗口的当前回撤
    if len(pv) >= 6:
        weekly_dd = calc_drawdown(pv.iloc[-6:])["current"]
        result["weekly"]["drawdown"] = float(weekly_dd)
        if weekly_dd <= -result["weekly"]["threshold"]:
            result["weekly"]["triggered"] = True

    # 月回撤：最近 20 个交易日窗口的当前回撤
    if len(pv) >= 21:
        monthly_dd = calc_drawdown(pv.iloc[-21:])["current"]
        result["monthly"]["drawdown"] = float(monthly_dd)
        if monthly_dd <= -result["monthly"]["threshold"]:
            result["monthly"]["triggered"] = True

    # 最严重的触发决定动作
    if result["monthly"]["triggered"]:
        result["action"] = "liquidate"
    elif result["weekly"]["triggered"]:
        result["action"] = "reduce_50"
    elif result["daily"]["triggered"]:
        result["action"] = "warning"

    return result
