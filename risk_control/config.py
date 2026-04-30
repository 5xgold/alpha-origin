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
# 信号插件系统
# ═══════════════════════════════════════════

# 市场多空区间 — 多头手松、空头手紧
# 通过乘数调节止损/止盈参数，>1 放宽，<1 收紧
MARKET_REGIME_PROFILES = {
    "bull": {
        "label": "多头",
        "stop_loss_multiplier": 1.3,        # 止损放宽30%（ATR倍数 × 1.3）
        "take_profit_multiplier": 1.2,      # 止盈目标放大50%
        "trailing_stop_multiplier": 1.3,    # 移动止损放宽30%
        "circuit_breaker_multiplier": 1.3,  # 熔断阈值放宽30%
    },
    "bear": {
        "label": "空头",
        "stop_loss_multiplier": 0.7,        # 止损收紧30%
        "take_profit_multiplier": 0.7,      # 止盈目标缩小30%
        "trailing_stop_multiplier": 0.7,    # 移动止损收紧30%
        "circuit_breaker_multiplier": 0.7,  # 熔断阈值收紧30%
    },
    "neutral": {
        "label": "震荡",
        "stop_loss_multiplier": 1.0,
        "take_profit_multiplier": 1.0,
        "trailing_stop_multiplier": 1.0,
        "circuit_breaker_multiplier": 1.0,
    },
}

# 当前市场区间（手动设置，未来可接入自动判断）
CURRENT_MARKET_REGIME = "neutral"


def get_regime_params():
    """获取当前市场区间下的调节参数"""
    return MARKET_REGIME_PROFILES.get(CURRENT_MARKET_REGIME,
                                      MARKET_REGIME_PROFILES["neutral"])

# 动态止损升级阶段
DYNAMIC_STOP_PHASES = [
    {"min_profit": 0.05, "stop_at": "breakeven",       "label": "保本"},
    {"min_profit": 0.15, "stop_at": "cost_plus_8pct",  "label": "成本+8%"},
    {"min_profit": 0.25, "stop_at": "trailing_tight",  "label": "紧移动止损"},
]
DYNAMIC_TRAILING_TIGHT_MULTIPLIER = 1.0     # Phase 3 紧移动止损倍数

# 持仓周期管理
HOLDING_PERIOD_STAGNATION_DAYS = 60         # 资金停滞天数阈值
HOLDING_PERIOD_STAGNATION_MIN_GAIN = 0.05   # 停滞期最低收益要求
HOLDING_PERIOD_DANGER_DAYS = 90             # 长期亏损天数阈值
HOLDING_PERIOD_DANGER_MIN_GAIN = 0.0        # 长期亏损收益阈值
HOLDING_PERIOD_WEAK_TREND_DAYS = 30         # 趋势走弱天数阈值

# 加仓/金字塔策略
PYRAMID_ADD_RATIOS = [0.50, 0.25]           # 加仓比例递减
PYRAMID_SUPPORT_METHODS = ["ma20", "recent_low"]  # 支撑位计算方法
PYRAMID_MIN_DROP_PCT = 0.07                 # 距成本至少跌7%才考虑加仓

# 告警升级（信号持续天数 → 级别提升）
ALERT_ESCALATION_DAYS = {
    "watch_to_warning": 3,
    "warning_to_danger": 5,
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
