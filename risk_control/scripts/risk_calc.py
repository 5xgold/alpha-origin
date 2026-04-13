"""风控底层计算 — 频率无关

所有函数接受 DataFrame[date, open, high, low, close, volume]，
日线和日内的区别仅在数据源层。
"""

import numpy as np
import pandas as pd


def calc_atr(prices_df, period=14):
    """计算 ATR (Average True Range)

    Args:
        prices_df: DataFrame with columns [high, low, close]
        period: ATR 周期

    Returns:
        Series: ATR 值
    """
    high = prices_df["high"].astype(float)
    low = prices_df["low"].astype(float)
    close = prices_df["close"].astype(float)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    return tr.rolling(window=period, min_periods=1).mean()


def calc_realized_vol(prices_df, window=20):
    """计算年化实现波动率

    Args:
        prices_df: DataFrame with column [close]
        window: 滚动窗口

    Returns:
        float: 年化波动率（百分比，如 22.3 表示 22.3%）
    """
    close = prices_df["close"].astype(float)
    if len(close) < 2:
        return 0.0

    log_returns = np.log(close / close.shift(1)).dropna()
    if len(log_returns) < window:
        daily_vol = log_returns.std()
    else:
        daily_vol = log_returns.iloc[-window:].std()

    return float(daily_vol * np.sqrt(252) * 100)


def calc_correlation_matrix(prices_dict, window=60):
    """计算持仓间相关性矩阵

    Args:
        prices_dict: {code: DataFrame with [close]}
        window: 滚动窗口

    Returns:
        DataFrame: 相关性矩阵
    """
    returns_dict = {}
    for code, df in prices_dict.items():
        close = df["close"].astype(float)
        ret = close.pct_change().dropna()
        if len(ret) >= window:
            ret = ret.iloc[-window:]
        returns_dict[code] = ret

    if len(returns_dict) < 2:
        return pd.DataFrame()

    returns_df = pd.DataFrame(returns_dict)
    returns_df = returns_df.dropna()

    if len(returns_df) < 10:
        return pd.DataFrame()

    return returns_df.corr()


def calc_drawdown(values_series):
    """计算回撤

    Args:
        values_series: Series of portfolio values (index=date)

    Returns:
        dict: {current: float, max: float, max_date: str, peak_date: str}
    """
    if values_series.empty or len(values_series) < 2:
        return {"current": 0.0, "max": 0.0, "max_date": "", "peak_date": ""}

    values = values_series.astype(float)
    cummax = values.cummax()
    drawdown = (values - cummax) / cummax

    current_dd = float(drawdown.iloc[-1])
    max_dd = float(drawdown.min())
    max_dd_idx = drawdown.idxmin()

    # 找到最大回撤对应的峰值日期
    peak_date = cummax[:max_dd_idx].idxmax() if max_dd < 0 else ""

    return {
        "current": current_dd,
        "max": max_dd,
        "max_date": str(max_dd_idx) if max_dd < 0 else "",
        "peak_date": str(peak_date),
    }


def calc_volume_ratio(prices_df, window=20):
    """计算量比（当前成交量 / 近N日均量）

    Args:
        prices_df: DataFrame with column [volume]
        window: 均量窗口

    Returns:
        float: 量比
    """
    volume = prices_df["volume"].astype(float)
    if len(volume) < 2:
        return 1.0

    avg_vol = volume.iloc[-window - 1:-1].mean() if len(volume) > window else volume.iloc[:-1].mean()
    current_vol = volume.iloc[-1]

    if avg_vol <= 0:
        return 1.0

    return float(current_vol / avg_vol)


def calc_portfolio_values(portfolio_df, prices_dict, lookback_days=60):
    """根据持仓和历史价格计算组合近似净值序列

    假设持仓不变，用最近 N 日收盘价计算每日市值。
    A股/港股交易日历不同，用前向填充对齐缺失价格。

    Args:
        portfolio_df: DataFrame[code, quantity, cost_price]
        prices_dict: {code: DataFrame[date, close, ...]}
        lookback_days: 回溯天数

    Returns:
        Series: index=date, values=portfolio_value
    """
    # 构建每只股票的收盘价 Series，统一到同一个日期轴
    price_series = {}
    for _, row in portfolio_df.iterrows():
        code = str(row["code"])
        qty = float(row["quantity"])
        if code not in prices_dict or qty <= 0:
            continue

        df = prices_dict[code]
        close = df.set_index("date")["close"].astype(float)
        close = close.iloc[-lookback_days:] if len(close) > lookback_days else close
        price_series[code] = close

    if not price_series:
        return pd.Series(dtype=float)

    # 合并所有日期，前向填充缺失价格（不同市场交易日历不同）
    all_prices = pd.DataFrame(price_series)
    all_prices = all_prices.sort_index().ffill()

    # 只保留所有股票都有数据的日期（首次出现后）
    all_prices = all_prices.dropna()

    if all_prices.empty:
        return pd.Series(dtype=float)

    # 计算每日组合市值
    daily_value = pd.Series(0.0, index=all_prices.index)
    for _, row in portfolio_df.iterrows():
        code = str(row["code"])
        qty = float(row["quantity"])
        if code in all_prices.columns:
            daily_value += all_prices[code] * qty

    return daily_value
