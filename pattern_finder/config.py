"""
Pattern-Finder Module Configuration

Imports shared configs and defines pattern-finder specific parameters.
"""

import sys
from pathlib import Path

# Add parent directory to path for shared imports
sys.path.append(str(Path(__file__).parent.parent))
from shared.config import CACHE_DIR as SHARED_CACHE_DIR, TS_TOKEN

# ─── 窗口参数 ────────────────────────────────────────────────────
LOOKBACK_DAYS = 60        # 特征观察窗口（过去多少个交易日）
FORWARD_DAYS = 20         # 未来预测窗口（评估后续表现）
TOP_K_SIMILAR = 20        # 召回相似案例数量

# ─── 成功案例定义 ─────────────────────────────────────────────────
SUCCESS_RETURN_THRESHOLD = 0.10     # 未来 FORWARD_DAYS 内涨幅阈值（10%）
SUCCESS_DRAWDOWN_LIMIT   = 0.08     # 期间最大回撤上限（8%）

# ─── 技术指标参数 ─────────────────────────────────────────────────
MA_PERIODS    = [5, 10, 20, 60]
EMA_PERIODS   = [12, 26]
MACD_FAST     = 12
MACD_SLOW     = 26
MACD_SIGNAL   = 9
RSI_PERIOD    = 14
KDJ_PERIOD    = 9
BOLL_PERIOD   = 20
BOLL_STD      = 2
ATR_PERIOD    = 14
OBV_ENABLED   = True

# ─── 相似度检索 ───────────────────────────────────────────────────
# 使用的特征维度
FEATURE_PRICE_COLS = [
    "norm_close",          # 归一化收盘价序列
    "norm_volume",         # 归一化成交量序列
]
FEATURE_INDICATOR_COLS = [
    "macd_hist",           # MACD 柱状图
    "rsi",                 # RSI
    "boll_pct",            # 价格在布林带的位置 (0~1)
    "ma20_dev",            # 收盘偏离 MA20 的百分比
    "vol_ratio",           # 成交量比（当日/20日均量）
]

# DTW 权重 vs 余弦相似度权重（加权融合）
DTW_WEIGHT    = 0.5
COSINE_WEIGHT = 0.5

# ─── 数据路径 ─────────────────────────────────────────────────────
# Use shared cache directory for consistency
LIBRARY_CACHE_DIR = str(Path(SHARED_CACHE_DIR) / "pattern_finder")
OUTPUT_DIR = str(Path(__file__).parent.parent / "output")

# Module-specific data directory
DATA_DIR = str(Path(__file__).parent / "data")
