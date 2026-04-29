"""风控模块配置"""

# ═══════════════════════════════════════════
# 第一道防线：仓位管理
# ═══════════════════════════════════════════

MAX_SINGLE_SECTOR_WEIGHT = 0.30     # 单行业最大仓位

# 个股仓位上限 — 基于熟悉程度评估
# 四维度: business_model / shareholder_friendly / valuation_low / trend_up
# 通过维度数量映射到仓位上限等级
FAMILIARITY_DIMENSIONS = [
    "business_model",        # 商业模式是否优秀
    "shareholder_friendly",  # 对中小投资者态度是否友好
    "valuation_low",         # 当前估值是否处于历史低位/合理区间
    "trend_up",              # 技术趋势是否处于底部或上升趋势
]

FAMILIARITY_POSITION_TIERS = {
    "low":       0.12,   # 0-1 维度通过 → 不熟悉，保守持仓
    "medium":    0.15,   # 2 维度通过 → 一般了解
    "high":      0.18,   # 3 维度通过 → 较熟悉
    "very_high": 0.22,   # 4 维度全通过 → 非常熟悉
}

FAMILIARITY_LEVEL_LABELS = {
    "low": "低", "medium": "中", "high": "高", "very_high": "极高",
}


def get_familiarity_level(true_count: int) -> str:
    """维度通过数 → 熟悉程度等级"""
    if true_count <= 1:
        return "low"
    elif true_count == 2:
        return "medium"
    elif true_count == 3:
        return "high"
    return "very_high"

# 波动率-仓位对照表：(波动率上限%, 建议最大仓位)
# A股没有 VIX，用沪深300的20日实现波动率年化值替代
VOLATILITY_POSITION_BANDS = [
    (15, 0.80),     # 低波 → 最多80%仓位
    (25, 0.60),     # 中波 → 最多60%
    (35, 0.40),     # 高波 → 最多40%
    (999, 0.20),    # 极端 → 最多20%
]

# ═══════════════════════════════════════════
# 第二道防线：止损止盈
# ═══════════════════════════════════════════

ATR_PERIOD = 14
STOP_LOSS_ATR_MULTIPLIER = 2.0          # 止损 = 成本 - 2×ATR
TAKE_PROFIT_TIERS = [                   # 分批止盈
    (0.15, 1 / 3),     # 盈利15%时卖出1/3
    (0.30, 1 / 3),     # 盈利30%时再卖1/3
]
TRAILING_STOP_ATR_MULTIPLIER = 1.5      # 移动止损 = 最高价 - 1.5×ATR

# 组合级熔断
CIRCUIT_BREAKER = {
    "daily": 0.03,      # 日亏3%
    "weekly": 0.05,     # 周亏5%
    "monthly": 0.08,    # 月亏8%
}

# ═══════════════════════════════════════════
# 第三道防线：异常检测
# ═══════════════════════════════════════════

VOL_SPIKE_THRESHOLD = 2.5               # 波动率突变倍数
LIQUIDITY_VOL_RATIO = 0.3               # 量比 < 0.3 视为流动性枯竭
CORRELATION_THRESHOLD = 0.85            # 相关性过高阈值

# 信号数 → 动作
ALERT_ACTIONS = {
    1: "warning",       # 预警
    2: "reduce_50",     # 减仓50%
    3: "liquidate",     # 清仓
}

# ═══════════════════════════════════════════
# 扩展预留
# ═══════════════════════════════════════════

DATA_FREQ = "daily"             # "daily" | "intraday" (Phase 2)
MONITOR_INTERVAL = None         # None=单次运行 | 300=每5分钟 (Phase 2)

# 市场指数（用于波动率计算）
# 支持单指数 str 或多指数 list[dict]，多指数会合成加权波动率
MARKET_INDEX = [
    {"index": "000001", "weight": 0.5},    # 上证指数
    {"index": "000300", "weight": 0.3},    # 沪深300
    {"index": "HK.800000", "weight": 0.2}, # 恒生指数
]

# 组合净值回溯天数
PORTFOLIO_LOOKBACK_DAYS = 60
