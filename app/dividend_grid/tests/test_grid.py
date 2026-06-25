"""股息网格核心逻辑测试 (全程 Decimal)"""

import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))
from dividend_grid.grid import build_grid, classify_action, to_decimal

D = Decimal


def test_outputs_are_decimal():
    """所有数值字段均为 Decimal, 不混入 float"""
    levels = build_grid(dividend="2.70", current_price="53.48",
                        risk_free_rate="0.0173")
    for lv in levels:
        assert isinstance(lv.yield_rate, Decimal)
        assert isinstance(lv.price, Decimal)
        assert isinstance(lv.pct_vs_current, Decimal)
        assert lv.ratio_vs_riskfree is None or isinstance(lv.ratio_vs_riskfree, Decimal)


def test_price_yield_relationship():
    """买入价 = 股息 / 目标股息率 (Decimal 精确相等)"""
    levels = build_grid(dividend="2.70", current_price="53.48",
                        risk_free_rate="0.0173", step="0.005",
                        low_yield="0.04", high_yield="0.09")
    for lv in levels:
        assert lv.price == D("2.70") / lv.yield_rate


def test_yield_ascending_price_descending():
    """股息率升序则价格降序"""
    levels = build_grid(dividend="2.70", current_price="53.48",
                        risk_free_rate="0.0173")
    yields = [lv.yield_rate for lv in levels]
    prices = [lv.price for lv in levels]
    assert yields == sorted(yields)
    assert prices == sorted(prices, reverse=True)


def test_current_price_row_inserted():
    """现价行被插入且只有一行"""
    levels = build_grid(dividend="2.70", current_price="53.48",
                        risk_free_rate="0.0173")
    current_rows = [lv for lv in levels if lv.action == "现价"]
    assert len(current_rows) == 1
    cur = current_rows[0]
    assert cur.price == D("53.48")
    assert cur.yield_rate == D("2.70") / D("53.48")


def test_all_grid_levels_above_riskfree():
    """所有有效加仓网格的股息率都高于现价股息率(从而远高于无风险利率)"""
    rf = D("0.0173")
    levels = build_grid(dividend="2.70", current_price="53.48", risk_free_rate=rf)
    current_yield = D("2.70") / D("53.48")
    for lv in levels:
        if lv.is_grid:
            assert lv.yield_rate > current_yield
            assert lv.yield_rate > rf
            assert lv.ratio_vs_riskfree > 1


def test_ratio_vs_riskfree():
    """性价比 = 股息率 / 无风险利率"""
    rf = D("0.0173")
    levels = build_grid(dividend="2.70", current_price="53.48", risk_free_rate=rf)
    for lv in levels:
        assert lv.ratio_vs_riskfree == lv.yield_rate / rf


def test_step_count():
    """档位数量 = (high-low)/step + 1 (+现价行)"""
    levels = build_grid(dividend="2.70", current_price="53.48", risk_free_rate="0.0173",
                        step="0.005", low_yield="0.04", high_yield="0.09")
    # (9-4)/0.5 = 10 步 → 11 档 + 1 现价行
    assert len(levels) == 12


def test_classify_action_zones():
    cy = D("0.05")  # 现价股息率 5%
    assert classify_action(D("0.039"), cy) == "减仓/止盈区"   # 低 1.1pp
    assert classify_action(D("0.045"), cy) == "不加仓"        # 低 0.5pp
    assert classify_action(D("0.055"), cy) == "加仓"          # 高 0.5pp
    assert classify_action(D("0.070"), cy) == "重点加仓"      # 高 2.0pp
    assert classify_action(D("0.085"), cy) == "极端/深跌加仓" # 高 3.5pp


def test_extreme_cheap_current_price():
    """现价股息率高于所有档位时, 现价行追加到末尾"""
    # 股息 5 元, 现价 50 → 现价股息率 10%, 高于 high_yield 9%
    levels = build_grid(dividend="5.0", current_price="50.0", risk_free_rate="0.0173",
                        low_yield="0.04", high_yield="0.09")
    assert levels[-1].action == "现价"


def test_riskfree_zero_ratio_none():
    """无风险利率为 0 时, 性价比为 None (不抛除零异常)"""
    levels = build_grid(dividend="2.70", current_price="53.48", risk_free_rate="0")
    assert all(lv.ratio_vs_riskfree is None for lv in levels)


def test_accepts_str_and_float_inputs():
    """str / float / Decimal 入参结果一致 (float 经 str() 转换)"""
    a = build_grid(dividend="2.70", current_price="53.48", risk_free_rate="0.0173")
    b = build_grid(dividend=2.70, current_price=53.48, risk_free_rate=0.0173)
    c = build_grid(dividend=D("2.70"), current_price=D("53.48"), risk_free_rate=D("0.0173"))
    assert [l.price for l in a] == [l.price for l in b] == [l.price for l in c]


@pytest.mark.parametrize("bad", [
    {"dividend": "0", "current_price": "50", "risk_free_rate": "0.0173"},
    {"dividend": "2.7", "current_price": "-1", "risk_free_rate": "0.0173"},
    {"dividend": "2.7", "current_price": "50", "risk_free_rate": "0.0173", "step": "0"},
    {"dividend": "2.7", "current_price": "50", "risk_free_rate": "0.0173",
     "low_yield": "0.09", "high_yield": "0.04"},
])
def test_invalid_params(bad):
    with pytest.raises(ValueError):
        build_grid(**bad)
