"""
特征工程模块
输入：单只股票的 OHLCV DataFrame（index 为日期）
输出：包含所有技术指标和归一化序列的 DataFrame，以及滑动窗口切片
"""

import numpy as np
import pandas as pd
from typing import List, Tuple, Dict, Optional
import warnings
warnings.filterwarnings("ignore")

# ─── 技术指标计算 ─────────────────────────────────────────────────

def calc_ma(close: pd.Series, periods: List[int]) -> pd.DataFrame:
    """计算多周期简单移动均线"""
    result = {}
    for p in periods:
        result[f"ma{p}"] = close.rolling(p).mean()
    return pd.DataFrame(result, index=close.index)


def calc_ema(close: pd.Series, periods: List[int]) -> pd.DataFrame:
    """计算多周期指数移动均线"""
    result = {}
    for p in periods:
        result[f"ema{p}"] = close.ewm(span=p, adjust=False).mean()
    return pd.DataFrame(result, index=close.index)


def calc_macd(close: pd.Series, fast=12, slow=26, signal=9) -> pd.DataFrame:
    """计算 MACD 指标"""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif      = ema_fast - ema_slow
    dea      = dif.ewm(span=signal, adjust=False).mean()
    hist     = (dif - dea) * 2
    return pd.DataFrame({
        "macd_dif":  dif,
        "macd_dea":  dea,
        "macd_hist": hist,
    }, index=close.index)


def calc_rsi(close: pd.Series, period=14) -> pd.Series:
    """计算 RSI"""
    delta = close.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs   = avg_gain / (avg_loss + 1e-9)
    rsi  = 100 - (100 / (1 + rs))
    rsi.name = "rsi"
    return rsi


def calc_kdj(high: pd.Series, low: pd.Series, close: pd.Series,
             period=9) -> pd.DataFrame:
    """计算 KDJ 指标"""
    low_min  = low.rolling(period).min()
    high_max = high.rolling(period).max()
    rsv = (close - low_min) / (high_max - low_min + 1e-9) * 100
    k = rsv.ewm(alpha=1/3, adjust=False).mean()
    d = k.ewm(alpha=1/3, adjust=False).mean()
    j = 3 * k - 2 * d
    return pd.DataFrame({"kdj_k": k, "kdj_d": d, "kdj_j": j},
                        index=close.index)


def calc_bollinger(close: pd.Series, period=20, std_mult=2) -> pd.DataFrame:
    """计算布林带，并输出价格在带中位置（0=下轨，1=上轨）"""
    mid  = close.rolling(period).mean()
    std  = close.rolling(period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    boll_pct = (close - lower) / (upper - lower + 1e-9)
    return pd.DataFrame({
        "boll_mid":   mid,
        "boll_upper": upper,
        "boll_lower": lower,
        "boll_pct":   boll_pct.clip(0, 1),  # 超出范围截断到 0~1
    }, index=close.index)


def calc_atr(high: pd.Series, low: pd.Series,
             close: pd.Series, period=14) -> pd.Series:
    """计算 ATR（平均真实波幅）"""
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()
    atr.name = "atr"
    return atr


def calc_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """计算 OBV（能量潮）"""
    direction = np.sign(close.diff().fillna(0))
    obv = (direction * volume).cumsum()
    obv.name = "obv"
    return obv


def calc_derived_features(df: pd.DataFrame) -> pd.DataFrame:
    """计算派生量价特征"""
    close  = df["close"]
    volume = df["volume"]

    # 偏离均线程度
    df["ma20_dev"] = (close / close.rolling(20).mean() - 1).fillna(0)
    df["ma60_dev"] = (close / close.rolling(60).mean() - 1).fillna(0)

    # 成交量比
    df["vol_ratio"]  = volume / (volume.rolling(20).mean() + 1e-9)
    df["vol_ratio5"] = volume / (volume.rolling(5).mean()  + 1e-9)

    # 日涨跌幅
    df["pct_chg"]    = close.pct_change().fillna(0)

    # 振幅
    df["amplitude"]  = (df["high"] - df["low"]) / (df["close"].shift(1) + 1e-9)

    # 距 N 日高低点距离
    df["dist_high20"] = close / close.rolling(20).max() - 1
    df["dist_low20"]  = close / close.rolling(20).min() - 1
    df["dist_high60"] = close / close.rolling(60).max() - 1
    df["dist_low60"]  = close / close.rolling(60).min() - 1

    return df


# ─── 归一化 ──────────────────────────────────────────────────────

def normalize_price_series(close: pd.Series) -> pd.Series:
    """
    把价格序列归一化：以序列首日收盘价为基准 = 1.0
    消除绝对价格差异，只保留形态信息
    """
    base = close.iloc[0]
    if base == 0:
        return close * 0
    return close / base - 1.0   # 转为相对首日的收益率序列


def normalize_volume_series(volume: pd.Series) -> pd.Series:
    """把成交量序列归一化：Z-score 标准化"""
    mu  = volume.mean()
    std = volume.std()
    if std < 1e-9:
        return volume * 0
    return (volume - mu) / std


# ─── 主接口：生成单只股票完整特征 ────────────────────────────────

def build_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    输入 df：必须包含列 [open, high, low, close, volume]，index 为日期
    输出：新增所有技术指标列的 DataFrame
    """
    df = df.copy()
    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"]

    # MA / EMA
    for col, vals in calc_ma(close, [5, 10, 20, 60]).items():
        df[col] = vals
    for col, vals in calc_ema(close, [12, 26]).items():
        df[col] = vals

    # MACD
    for col, vals in calc_macd(close).items():
        df[col] = vals

    # RSI
    df["rsi"] = calc_rsi(close)

    # KDJ
    for col, vals in calc_kdj(high, low, close).items():
        df[col] = vals

    # 布林带
    for col, vals in calc_bollinger(close).items():
        df[col] = vals

    # ATR
    df["atr"] = calc_atr(high, low, close)

    # OBV
    df["obv"] = calc_obv(close, volume)

    # 派生特征
    df = calc_derived_features(df)

    return df


# ─── 滑动窗口切片 ─────────────────────────────────────────────────

def create_windows(
    df: pd.DataFrame,
    lookback: int = 60,
    forward:  int = 20,
    step:     int = 5,
) -> List[Dict]:
    """
    对单只股票的特征 DataFrame 做滑动窗口切片
    
    返回：每个窗口的字典，包含：
        - feature_df:   观察窗口的特征 DataFrame (lookback 行)
        - future_close: 未来 forward 日的收盘价序列
        - label:        1=成功 / 0=失败（根据 SUCCESS_RETURN_THRESHOLD 判定）
        - meta:         股票代码、开始日期、结束日期
    """
    from config.settings import (
        SUCCESS_RETURN_THRESHOLD,
        SUCCESS_DRAWDOWN_LIMIT,
    )

    windows = []
    n = len(df)
    required = lookback + forward

    if n < required:
        return windows

    for start in range(0, n - required + 1, step):
        end    = start + lookback
        feat   = df.iloc[start:end].copy()
        future = df.iloc[end:end + forward]["close"]

        # ── 打标签 ──────────────────────────────────────────
        entry_price    = df.iloc[end - 1]["close"]
        future_returns = future / entry_price - 1
        max_return     = future_returns.max()
        min_return     = future_returns.min()

        # 最大回撤（从入场价算起的最大浮亏）
        max_drawdown = abs(min_return) if min_return < 0 else 0

        label = int(
            max_return     >= SUCCESS_RETURN_THRESHOLD and
            max_drawdown   <= SUCCESS_DRAWDOWN_LIMIT
        )

        windows.append({
            "feature_df":   feat,
            "future_close": future,
            "label":        label,
            "entry_price":  entry_price,
            "meta": {
                "start_date": df.index[start],
                "end_date":   df.index[end - 1],
            }
        })

    return windows


# ─── 提取单窗口的向量特征 ─────────────────────────────────────────

def extract_vector(window_df: pd.DataFrame) -> np.ndarray:
    """
    把一个窗口的 DataFrame 压缩为 1D 特征向量，用于余弦相似度检索
    
    取以下序列展平后拼接：
    - 归一化价格序列（lookback 维）
    - 归一化成交量序列（lookback 维）
    - MACD 柱（lookback 维）
    - RSI / 100（lookback 维）
    - 布林带百分比位置（lookback 维）
    - MA20 偏离度（lookback 维）
    """
    close  = window_df["close"]
    volume = window_df["volume"]

    price_norm = normalize_price_series(close).values
    vol_norm   = normalize_volume_series(volume).values

    macd_hist  = window_df["macd_hist"].fillna(0).values
    rsi_norm   = (window_df["rsi"].fillna(50) / 100).values
    boll_pct   = window_df["boll_pct"].fillna(0.5).values
    ma20_dev   = window_df["ma20_dev"].fillna(0).values

    vec = np.concatenate([
        price_norm,
        vol_norm,
        macd_hist,
        rsi_norm,
        boll_pct,
        ma20_dev,
    ]).astype(np.float32)

    # 把 NaN/Inf 替换为 0，避免检索出错
    vec = np.nan_to_num(vec, nan=0.0, posinf=0.0, neginf=0.0)
    return vec


if __name__ == "__main__":
    # 快速测试
    np.random.seed(42)
    n = 200
    dates  = pd.date_range("2022-01-01", periods=n, freq="B")
    close  = pd.Series(100 * np.cumprod(1 + np.random.randn(n) * 0.01),
                       index=dates)
    high   = close * (1 + np.abs(np.random.randn(n) * 0.005))
    low    = close * (1 - np.abs(np.random.randn(n) * 0.005))
    open_  = close.shift(1).fillna(close[0])
    volume = pd.Series(np.random.randint(1e6, 1e7, n), index=dates)

    df = pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume
    })

    df_feat = build_indicators(df)
    print(f"[特征列数] {len(df_feat.columns)} 列")

    wins = create_windows(df_feat, lookback=60, forward=20)
    print(f"[窗口数量] {len(wins)} 个")
    print(f"[成功案例] {sum(w['label'] for w in wins)} 个")

    vec = extract_vector(wins[0]["feature_df"])
    print(f"[向量维度] {vec.shape}")
