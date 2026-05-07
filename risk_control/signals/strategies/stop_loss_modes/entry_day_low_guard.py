"""入场保护止损模式。"""

import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.parent.parent.parent))
from shared.data_provider import get_stock_prices


def resolve_stop_price(*, code, market, buy_date, risk_rules, prices_dict, info, **kwargs):
    tick_size = resolve_price_tick(market, risk_rules)
    info["entry_date"] = buy_date or None
    info["price_tick"] = tick_size

    if not buy_date:
        return None

    entry_day_low = get_day_low(code, buy_date, prices_dict)
    if entry_day_low is None:
        return None

    info["entry_day_low"] = entry_day_low
    params = _resolve_stop_loss_params(risk_rules)
    buffer_ticks = int(params.get("buffer_ticks", risk_rules.get("entry_day_low_buffer_ticks", 5)) or 5)
    stop_price = entry_day_low - buffer_ticks * tick_size
    return round(stop_price, 3)


def _resolve_stop_loss_params(risk_rules):
    params = risk_rules.get("stop_loss_params", {})
    return params if isinstance(params, dict) else {}


def get_day_low(code, trade_date, prices_dict):
    df = prices_dict.get(code)
    if df is None or df.empty:
        df = get_stock_prices(code, trade_date, trade_date)
    if df is None or df.empty:
        return None

    local = df.copy()
    if "date" in local.columns:
        local["date"] = pd.to_datetime(local["date"]).dt.strftime("%Y%m%d")
        matched = local[local["date"] == str(trade_date)]
        if not matched.empty:
            return float(matched["low"].astype(float).iloc[-1])

    return None


def resolve_price_tick(market, risk_rules):
    params = _resolve_stop_loss_params(risk_rules)
    configured = params.get("price_tick", risk_rules.get("price_tick"))
    if configured is not None:
        return float(configured)

    if market in {"上海", "深圳", "沪港通", "深港通"}:
        return 0.01
    return 0.01
