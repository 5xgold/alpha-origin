"""第三道防线：异常检测"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from risk_control.scripts.risk_calc import calc_realized_vol, calc_volume_ratio, calc_correlation_matrix
from risk_control.config import (
    VOL_SPIKE_THRESHOLD,
    LIQUIDITY_VOL_RATIO,
    CORRELATION_THRESHOLD,
    ALERT_ACTIONS,
)


def _check_vol_spike(prices_dict, window=20):
    """波动率突变检测：近5日波动率 / 近20日波动率 > 阈值"""
    signals = []
    for code, df in prices_dict.items():
        if len(df) < window + 5:
            continue

        vol_long = calc_realized_vol(df, window=window)
        vol_short = calc_realized_vol(df, window=5)

        if vol_long > 0:
            ratio = vol_short / vol_long
            if ratio > VOL_SPIKE_THRESHOLD:
                signals.append({
                    "type": "vol_spike",
                    "code": code,
                    "value": round(ratio, 2),
                    "threshold": VOL_SPIKE_THRESHOLD,
                    "detail": f"短期波动率 {vol_short:.1f}% / 长期 {vol_long:.1f}%",
                })
    return signals


def _check_liquidity(prices_dict):
    """流动性枯竭检测：量比 < 阈值"""
    signals = []
    for code, df in prices_dict.items():
        if len(df) < 5:
            continue

        vol_ratio = calc_volume_ratio(df, window=20)
        if vol_ratio < LIQUIDITY_VOL_RATIO:
            signals.append({
                "type": "liquidity_dry",
                "code": code,
                "value": round(vol_ratio, 2),
                "threshold": LIQUIDITY_VOL_RATIO,
                "detail": f"量比 {vol_ratio:.2f}",
            })
    return signals


def _check_correlation(prices_dict):
    """相关性过高检测：持仓间相关性 > 阈值"""
    signals = []
    corr = calc_correlation_matrix(prices_dict, window=60)
    if corr.empty:
        return signals

    checked = set()
    for i in corr.columns:
        for j in corr.columns:
            if i >= j:
                continue
            pair = (i, j)
            if pair in checked:
                continue
            checked.add(pair)

            val = corr.loc[i, j]
            if val > CORRELATION_THRESHOLD:
                signals.append({
                    "type": "high_correlation",
                    "code": f"{i}/{j}",
                    "value": round(val, 3),
                    "threshold": CORRELATION_THRESHOLD,
                    "detail": f"{i} 与 {j} 相关性 {val:.3f}",
                })
    return signals


def _check_external_shock():
    """外部冲击检测 — Phase 1 占位，Phase 2 接新闻 API"""
    return []


def detect_anomalies(portfolio_df, prices_dict):
    """异常信号检测

    Args:
        portfolio_df: DataFrame[code, name, ...]
        prices_dict: {code: DataFrame[date, open, high, low, close, volume]}

    Returns:
        dict: {
            signals: [{"type", "code", "value", "threshold", "detail"}],
            alert_count: int,
            action: str,
        }
    """
    # 只检测持仓中的股票
    held_codes = set(str(c) for c in portfolio_df["code"])
    held_prices = {k: v for k, v in prices_dict.items() if k in held_codes}

    signals = []
    signals.extend(_check_vol_spike(held_prices))
    signals.extend(_check_liquidity(held_prices))
    signals.extend(_check_correlation(held_prices))
    signals.extend(_check_external_shock())

    signal_count = len(signals)
    alert_count = len({sig["type"] for sig in signals})

    # 根据信号类别数决定动作，避免相关性成对膨胀导致动作过度升级
    action = "safe"
    for threshold, act in sorted(ALERT_ACTIONS.items()):
        if alert_count >= threshold:
            action = act

    return {
        "signals": signals,
        "signal_count": signal_count,
        "alert_count": alert_count,
        "action": action,
    }
