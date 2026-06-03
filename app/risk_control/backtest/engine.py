"""核心模拟循环 — 逐日回放风控信号并执行交易"""

import sys
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.parent))

from risk_control.scripts.stop_loss import calc_stop_take_levels, check_circuit_breaker
from risk_control.scripts.risk_calc import calc_realized_vol
from risk_control.signals.registry import run_all_signals
import risk_control.signals.state as state_module
from risk_control.backtest.executor import execute_signals
import risk_control.config as cfg


@dataclass
class BacktestResult:
    daily_snapshots: list = field(default_factory=list)
    trades_executed: list = field(default_factory=list)
    signals_log: list = field(default_factory=list)
    params_used: dict = field(default_factory=dict)
    start_date: str = ""
    end_date: str = ""


def run_backtest(
    portfolio_df,
    prices_dict,
    start_date,
    end_date,
    total_equity,
    params_override=None,
    market_regime="neutral",
):
    """逐日模拟风控系统，记录信号触发和交易执行

    Args:
        portfolio_df: 初始持仓 DataFrame[code, name, market, quantity, cost_price, ...]
        prices_dict: {code: DataFrame[date, open, high, low, close, volume]}
        start_date: 回测起始日 YYYYMMDD 或 YYYY-MM-DD
        end_date: 回测结束日
        total_equity: 初始总权益（现金 + 持仓市值）
        params_override: 覆盖 config 参数的 dict（由 params.py 在外层处理）
        market_regime: "bull" | "bear" | "neutral"

    Returns:
        BacktestResult
    """
    # 设置市场区间
    original_regime = cfg.CURRENT_MARKET_REGIME
    cfg.CURRENT_MARKET_REGIME = market_regime

    # 保存原始 _today_str 以便恢复
    original_today_str = state_module._today_str

    try:
        result = _run_simulation(
            portfolio_df, prices_dict, start_date, end_date, total_equity
        )
    finally:
        cfg.CURRENT_MARKET_REGIME = original_regime
        state_module._today_str = original_today_str

    # 记录使用的参数
    result.params_used = {
        "STOP_LOSS_ATR_MULTIPLIER": cfg.STOP_LOSS_ATR_MULTIPLIER,
        "TRAILING_STOP_ATR_MULTIPLIER": cfg.TRAILING_STOP_ATR_MULTIPLIER,
        "ATR_PERIOD": cfg.ATR_PERIOD,
        "TAKE_PROFIT_TIERS": cfg.TAKE_PROFIT_TIERS,
        "CIRCUIT_BREAKER": cfg.CIRCUIT_BREAKER,
        "market_regime": market_regime,
    }
    return result


def _run_simulation(portfolio_df, prices_dict, start_date, end_date, total_equity):
    """内部模拟循环"""
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)

    # 推导交易日序列：所有股票日期的并集
    trading_days = _get_trading_days(prices_dict, start, end)
    if not trading_days:
        return BacktestResult(start_date=str(start.date()), end_date=str(end.date()))

    # 初始化组合状态
    portfolio_state = _init_portfolio_state(portfolio_df, total_equity, prices_dict, start)
    signal_state = {"signals": {}, "holdings_first_seen": {}}

    result = BacktestResult(
        start_date=str(start.date()),
        end_date=str(end.date()),
    )

    # 预热期：start 之前的数据用于 ATR 计算，不记录结果
    warmup_end_idx = 0
    for i, day in enumerate(trading_days):
        if day >= start:
            warmup_end_idx = i
            break

    pending_signals = []  # 上一日触发的信号，待次日执行

    for i, day in enumerate(trading_days):
        day_str = str(day.date())

        # 注入模拟日期
        state_module._today_str = lambda d=day_str: d

        # 切片价格到当日（防止未来数据泄露）
        prices_up_to_today = _slice_prices(prices_dict, day)

        # 执行前一日的待执行信号（次日开盘价执行）
        if pending_signals and i >= warmup_end_idx:
            next_day_open = _get_open_prices(prices_dict, day)
            trades = execute_signals(
                signals=pending_signals[0],
                circuit_breaker=pending_signals[1],
                portfolio_state=portfolio_state,
                next_day_prices=next_day_open,
                execution_date=day_str,
            )
            result.trades_executed.extend(trades)
            pending_signals = []

        # 如果没有持仓了，跳过后续计算
        if not portfolio_state["positions"]:
            if i >= warmup_end_idx:
                result.daily_snapshots.append(_snapshot(day_str, portfolio_state, prices_dict, day))
            continue

        # 构建当日 portfolio_df（反映当前持仓）
        current_pdf = _build_current_portfolio_df(portfolio_state, prices_up_to_today)
        if current_pdf.empty:
            if i >= warmup_end_idx:
                result.daily_snapshots.append(_snapshot(day_str, portfolio_state, prices_dict, day))
            continue

        # 计算止损止盈
        sl_levels = calc_stop_take_levels(current_pdf, prices_up_to_today, total_equity)

        # 计算熔断
        cb = check_circuit_breaker(current_pdf, prices_up_to_today)

        # 计算市场波动率
        market_vol = _calc_market_vol(prices_up_to_today)

        # 运行信号系统
        signals = run_all_signals(
            current_pdf, prices_up_to_today,
            state=signal_state,
            total_equity=total_equity,
            market_vol=market_vol,
            sl_levels=sl_levels,
        )

        # 记录信号（仅回测期内）
        if i >= warmup_end_idx:
            for sig in signals:
                result.signals_log.append({**sig, "date": day_str})

        # 暂存信号，次日执行
        if signals or (cb.get("action") is not None):
            pending_signals = [signals, cb]

        # 记录每日快照
        if i >= warmup_end_idx:
            result.daily_snapshots.append(_snapshot(day_str, portfolio_state, prices_dict, day))

    return result


def _get_trading_days(prices_dict, start, end):
    """从 prices_dict 推导交易日序列，包含预热期"""
    all_dates = set()
    for df in prices_dict.values():
        if df.empty:
            continue
        dates = pd.to_datetime(df["date"])
        all_dates.update(dates.tolist())

    if not all_dates:
        return []

    # 包含预热期（start 前 ATR_PERIOD*2 天）
    warmup_start = start - pd.Timedelta(days=cfg.ATR_PERIOD * 3)
    sorted_dates = sorted(d for d in all_dates if warmup_start <= d <= end)
    return sorted_dates


def _init_portfolio_state(portfolio_df, total_equity, prices_dict, start_date):
    """初始化组合状态"""
    positions = {}
    total_market_value = 0.0

    for _, row in portfolio_df.iterrows():
        code = str(row["code"])
        qty = float(row["quantity"])
        cost = float(row["cost_price"])
        positions[code] = {
            "quantity": qty,
            "cost_price": cost,
            "name": str(row.get("name", code)),
            "market": str(row.get("market", "")),
            "trade_plan": row.get("trade_plan", {}),
            "risk_rules": row.get("risk_rules", {}),
        }
        total_market_value += qty * cost

    cash = max(0.0, total_equity - total_market_value)
    return {"positions": positions, "cash": cash}


def _slice_prices(prices_dict, up_to_date):
    """切片价格数据到指定日期"""
    sliced = {}
    for code, df in prices_dict.items():
        if df.empty:
            sliced[code] = df
            continue
        dates = pd.to_datetime(df["date"])
        mask = dates <= up_to_date
        sliced[code] = df[mask].copy()
    return sliced


def _get_open_prices(prices_dict, day):
    """获取指定日期的开盘价"""
    opens = {}
    for code, df in prices_dict.items():
        if df.empty:
            continue
        dates = pd.to_datetime(df["date"])
        day_data = df[dates == day]
        if not day_data.empty:
            opens[code] = float(day_data.iloc[0]["open"])
    return opens


def _build_current_portfolio_df(portfolio_state, prices_up_to_today):
    """从当前组合状态构建 portfolio_df"""
    rows = []
    for code, pos in portfolio_state["positions"].items():
        current_price = _get_latest_close(prices_up_to_today, code)
        if current_price is None:
            current_price = pos["cost_price"]
        rows.append({
            "code": code,
            "name": pos.get("name", code),
            "market": pos.get("market", ""),
            "quantity": pos["quantity"],
            "cost_price": pos["cost_price"],
            "current_price": current_price,
            "market_value": pos["quantity"] * current_price,
            "trade_plan": pos.get("trade_plan", {}),
            "risk_rules": pos.get("risk_rules", {}),
        })
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def _get_latest_close(prices_dict, code):
    """获取最新收盘价"""
    df = prices_dict.get(code)
    if df is None or df.empty:
        return None
    return float(df.iloc[-1]["close"])


def _calc_market_vol(prices_up_to_today):
    """计算市场波动率（简化：用第一个指数或第一只股票）"""
    for code in ("000001", "000300"):
        if code in prices_up_to_today and not prices_up_to_today[code].empty:
            return calc_realized_vol(prices_up_to_today[code])
    # fallback: 用第一只有数据的股票
    for df in prices_up_to_today.values():
        if not df.empty and len(df) > 5:
            return calc_realized_vol(df)
    return 20.0  # 默认中等波动


def _snapshot(day_str, portfolio_state, prices_dict, day):
    """生成每日快照"""
    total_value = portfolio_state["cash"]
    positions_snapshot = {}

    for code, pos in portfolio_state["positions"].items():
        df = prices_dict.get(code)
        price = pos["cost_price"]
        if df is not None and not df.empty:
            dates = pd.to_datetime(df["date"])
            day_data = df[dates == day]
            if not day_data.empty:
                price = float(day_data.iloc[0]["close"])
            else:
                # 用最近的收盘价
                before = df[dates <= day]
                if not before.empty:
                    price = float(before.iloc[-1]["close"])

        mv = pos["quantity"] * price
        total_value += mv
        positions_snapshot[code] = {
            "quantity": pos["quantity"],
            "price": price,
            "market_value": mv,
        }

    return {
        "date": day_str,
        "portfolio_value": total_value,
        "cash": portfolio_state["cash"],
        "positions": positions_snapshot,
    }
