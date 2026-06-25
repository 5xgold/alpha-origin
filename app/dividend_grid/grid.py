"""股息网格计算 — 核心逻辑

给定每股股息、当前价、无风险利率，按固定股息率步进生成买入/减仓网格。

核心关系:
    买入价 = 每股股息 / 目标股息率
    股息率越高 → 买入价越低 → 越跌越买
所有网格档位的股息率均要求 > 无风险利率(十年期国债), 保证相对无风险资产有正的息差安全垫。

数值计算全程使用 decimal.Decimal, 避免 float 二进制浮点误差。
"""

from dataclasses import dataclass
from decimal import Decimal


def to_decimal(x) -> Decimal:
    """统一转 Decimal: 从 float 转时先经 str(), 避免带入 float 误差"""
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


# 常用阈值常量 (Decimal)
_ZERO = Decimal("0")
_ONE = Decimal("1")
_EPS = Decimal("1e-9")
_GAP_TRIM = Decimal("0.010")      # 减仓区: 低于现价股息率 ≥1.0pp
_DEPTH_EXTREME = Decimal("0.030")  # 极端加仓: 高于现价股息率 ≥3.0pp
_DEPTH_HEAVY = Decimal("0.015")    # 重点加仓: ≥1.5pp


@dataclass
class GridLevel:
    """单个网格档位 (数值均为 Decimal)"""
    yield_rate: Decimal       # 目标股息率 (小数, 如 0.055)
    price: Decimal            # 对应买入价
    pct_vs_current: Decimal   # 相对现价涨跌幅 (小数)
    ratio_vs_riskfree: Decimal  # 股息率 / 无风险利率 (倍数)
    action: str               # 区间动作标签
    is_grid: bool             # 是否为有效加仓网格 (yield_rate > 现价股息率)


def classify_action(yield_rate: Decimal, current_yield: Decimal) -> str:
    """根据档位股息率相对现价股息率的位置, 给出动作标签"""
    yield_rate = to_decimal(yield_rate)
    current_yield = to_decimal(current_yield)
    if yield_rate < current_yield:
        # 股息率更低 = 价格更高 = 上涨区
        gap = current_yield - yield_rate
        if gap >= _GAP_TRIM:
            return "减仓/止盈区"
        return "不加仓"
    if abs(yield_rate - current_yield) < _EPS:
        return "现价"
    # 股息率更高 = 价格更低 = 加仓区, 越深越重
    depth = yield_rate - current_yield
    if depth >= _DEPTH_EXTREME:
        return "极端/深跌加仓"
    if depth >= _DEPTH_HEAVY:
        return "重点加仓"
    return "加仓"


def build_grid(
    dividend,
    current_price,
    risk_free_rate,
    step="0.005",
    low_yield="0.040",
    high_yield="0.090",
) -> list[GridLevel]:
    """生成股息网格

    Args:
        dividend: 每股税前股息 (元), 接受 Decimal/str/数值
        current_price: 当前股价 (元)
        risk_free_rate: 无风险利率 (小数, 如十年期国债 0.0173)
        step: 股息率步进 (小数, 默认 0.5%)
        low_yield: 网格最低股息率 (对应最高价, 默认 4.0%)
        high_yield: 网格最高股息率 (对应最低价, 默认 9.0%)

    Returns:
        按股息率升序(价格降序)排列的档位列表; 现价会作为一行插入到对应位置

    Raises:
        ValueError: 参数非法 (非正数 / 步进无效 / 区间方向反了)
    """
    dividend = to_decimal(dividend)
    current_price = to_decimal(current_price)
    risk_free_rate = to_decimal(risk_free_rate)
    step = to_decimal(step)
    low_yield = to_decimal(low_yield)
    high_yield = to_decimal(high_yield)

    if dividend <= _ZERO:
        raise ValueError(f"每股股息必须为正: {dividend}")
    if current_price <= _ZERO:
        raise ValueError(f"当前价必须为正: {current_price}")
    if risk_free_rate < _ZERO:
        raise ValueError(f"无风险利率不能为负: {risk_free_rate}")
    if step <= _ZERO:
        raise ValueError(f"步进必须为正: {step}")
    if low_yield >= high_yield:
        raise ValueError(f"low_yield({low_yield}) 必须小于 high_yield({high_yield})")

    current_yield = dividend / current_price

    # 生成股息率刻度 (用整数步 × 步长避免累加误差)
    n_steps = int((high_yield - low_yield) / step)
    yields = [low_yield + Decimal(i) * step for i in range(n_steps + 1)]

    levels: list[GridLevel] = []
    current_inserted = False
    for y in yields:
        # 在跨过现价股息率时, 先插入现价行
        if not current_inserted and y > current_yield:
            levels.append(_make_level(dividend, current_yield, current_price,
                                       risk_free_rate, current_yield, is_current=True))
            current_inserted = True
        levels.append(_make_level(dividend, y, current_price,
                                   risk_free_rate, current_yield))

    if not current_inserted:
        # 现价股息率高于所有档位 (极便宜), 追加到末尾
        levels.append(_make_level(dividend, current_yield, current_price,
                                   risk_free_rate, current_yield, is_current=True))

    return levels


def _make_level(dividend, yield_rate, current_price, risk_free_rate,
                current_yield, is_current=False):
    price = dividend / yield_rate
    pct = price / current_price - _ONE
    ratio = yield_rate / risk_free_rate if risk_free_rate > _ZERO else None
    action = "现价" if is_current else classify_action(yield_rate, current_yield)
    is_grid = yield_rate > current_yield + _EPS
    return GridLevel(
        yield_rate=yield_rate,
        price=price,
        pct_vs_current=pct,
        ratio_vs_riskfree=ratio,
        action=action,
        is_grid=is_grid,
    )
