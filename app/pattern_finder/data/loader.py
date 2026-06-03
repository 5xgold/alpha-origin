"""
数据适配层：统一数据加载接口
使用 shared.data_provider 作为主数据源，支持 demo 和 CSV 模式
统一输出 DataFrame[open, high, low, close, volume]，index 为日期
"""

import os
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from typing import Optional

# Add parent directory to path for shared imports
sys.path.append(str(Path(__file__).parent.parent.parent))
from shared.data_provider import get_stock_prices


# ─── 模拟数据（测试用，无需 API） ─────────────────────────────────

def make_demo_data(n: int = 400, seed: int = 42,
                   start: str = "2022-01-01") -> pd.DataFrame:
    """生成随机 OHLCV 数据，用于快速测试"""
    np.random.seed(seed)
    dates  = pd.date_range(start, periods=n, freq="B")
    close  = pd.Series(50 * np.cumprod(1 + np.random.randn(n) * 0.012),
                       index=dates)
    high   = close * (1 + np.abs(np.random.randn(n) * 0.006))
    low    = close * (1 - np.abs(np.random.randn(n) * 0.006))
    open_  = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(np.random.randint(int(1e6), int(8e6), n),
                       index=dates)
    df = pd.DataFrame({
        "open": open_, "high": high, "low": low,
        "close": close, "volume": volume,
    })
    return df


# ─── 统一数据加载（使用 shared.data_provider）─────────────────────

def load_stock_data(code: str, start_date: str, end_date: str, adjust: str = "qfq") -> pd.DataFrame:
    """
    统一数据加载接口，使用 shared.data_provider

    Args:
        code: 股票代码，支持格式：
              - A股：600519 / sh600519 / 000001 / sz000001
              - 港股：00700 / 09988
        start_date: 开始日期，格式：YYYY-MM-DD 或 YYYYMMDD
        end_date: 结束日期，格式：YYYY-MM-DD 或 YYYYMMDD
        adjust: 复权方式（默认前复权，推荐用于量化回测）
                - "qfq" 前复权（默认）
                - "hfq" 后复权
                - "" 不复权

    Returns:
        DataFrame with columns: open, high, low, close, volume
        Index: date (datetime)

    Features:
        - 自动多数据源 fallback (baostock → FutuOpenD → Eastmoney)
        - 共享缓存机制
        - 支持 A 股和港股
        - 默认前复权（技术指标计算、形态识别、收益率计算准确）
    """
    # Normalize date format
    start = start_date.replace("-", "")
    end = end_date.replace("-", "")
    start_fmt = f"{start[:4]}-{start[4:6]}-{start[6:]}"
    end_fmt = f"{end[:4]}-{end[4:6]}-{end[6:]}"

    # Load data via shared provider with adjust parameter
    df = get_stock_prices(code, start_fmt, end_fmt, adjust=adjust)

    # Ensure required columns
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(df.columns):
        missing = required - set(df.columns)
        raise ValueError(f"数据缺少必要列：{missing}")

    # Ensure date index
    if df.index.name != "date":
        if "date" in df.columns:
            df = df.set_index("date")
        else:
            raise ValueError("数据缺少 date 列或索引")

    return df[["open", "high", "low", "close", "volume"]].sort_index()


# ─── Tushare 接入（备用数据源）─────────────────────────────────────

def load_tushare(ts_code: str, start_date: str, end_date: str,
                 token: Optional[str] = None, adjust: str = "qfq") -> pd.DataFrame:
    """
    通过 Tushare 拉取日线数据（备用数据源）

    需要：pip install tushare
    ts_code 格式：000001.SZ / 600519.SH

    Args:
        adjust: 复权方式
                - "qfq" 前复权（默认）
                - "hfq" 后复权
                - "" 不复权

    注意：推荐使用 load_stock_data()，它会自动使用 shared.data_provider 的多数据源 fallback
    """
    try:
        import tushare as ts
        token = token or os.getenv("TUSHARE_TOKEN", "")
        if not token:
            raise ValueError("需要设置 TUSHARE_TOKEN 环境变量")

        ts.set_token(token)
        pro = ts.pro_api()

        # Tushare 使用不同的 API 获取复权数据
        if adjust == "qfq":
            # 前复权：使用 pro_bar
            df = ts.pro_bar(
                ts_code=ts_code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adj='qfq',  # 前复权
            )
        elif adjust == "hfq":
            # 后复权：使用 pro_bar
            df = ts.pro_bar(
                ts_code=ts_code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
                adj='hfq',  # 后复权
            )
        else:
            # 不复权：使用 daily
            df = pro.daily(
                ts_code=ts_code,
                start_date=start_date.replace("-", ""),
                end_date=end_date.replace("-", ""),
            )

        if df is None or df.empty:
            raise ValueError(f"未获取到数据：{ts_code}")

        df = df.sort_values("trade_date")
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.set_index("trade_date")
        df = df.rename(columns={"vol": "volume"})
        df = df[["open", "high", "low", "close", "volume"]]
        return df
    except ImportError:
        raise RuntimeError("请先安装 tushare：pip install tushare")
    except Exception as e:
        raise RuntimeError(f"Tushare 数据拉取失败：{e}")


# ─── akshare 接入（备用数据源）─────────────────────────────────────

def load_akshare(symbol: str, start_date: str, end_date: str, adjust: str = "qfq") -> pd.DataFrame:
    """
    通过 akshare 拉取 A 股日线数据（备用数据源，免费无需 Token）

    需要：pip install akshare
    symbol 格式：sh600519 / sz000001 / 600519 / 000001

    Args:
        adjust: 复权方式
                - "qfq" 前复权（默认）
                - "hfq" 后复权
                - "" 不复权

    注意：推荐使用 load_stock_data()，它会自动使用 shared.data_provider 的多数据源 fallback
    """
    try:
        import akshare as ak

        # Normalize symbol format for akshare
        if not symbol.startswith(('sh', 'sz')):
            # Auto-detect exchange
            code = symbol.zfill(6)
            if code[0] in ('6', '9'):
                symbol = f"sh{code}"
            else:
                symbol = f"sz{code}"

        # 转换复权参数
        adjust_map = {
            "qfq": "qfq",   # 前复权
            "hfq": "hfq",   # 后复权
            "": "",         # 不复权
        }
        adjust_param = adjust_map.get(adjust, "qfq")

        df = ak.stock_zh_a_hist(
            symbol    = symbol,
            period    = "daily",
            start_date= start_date.replace("-", ""),
            end_date  = end_date.replace("-", ""),
            adjust    = adjust_param,
        )

        if df.empty:
            raise ValueError(f"未获取到数据：{symbol}")

        df = df.rename(columns={
            "日期": "date", "开盘": "open", "最高": "high",
            "最低": "low",  "收盘": "close", "成交量": "volume",
        })
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df = df[["open", "high", "low", "close", "volume"]]
        df = df.sort_index()
        return df
    except ImportError:
        raise RuntimeError("请先安装 akshare：pip install akshare")
    except Exception as e:
        raise RuntimeError(f"akshare 数据拉取失败：{e}")


# ─── 本地 CSV 接入 ────────────────────────────────────────────────

def load_csv(filepath: str) -> pd.DataFrame:
    """
    从本地 CSV 加载数据
    必须包含列：date / open / high / low / close / volume
    date 列格式：YYYY-MM-DD
    """
    df = pd.read_csv(filepath, parse_dates=["date"])
    df = df.set_index("date").sort_index()
    required = {"open", "high", "low", "close", "volume"}
    missing  = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV 缺少必要列：{missing}")
    return df[["open", "high", "low", "close", "volume"]]
