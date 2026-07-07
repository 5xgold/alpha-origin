"""数据层测试 — 代码规范化(纯函数, 不依赖网络)"""

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parent.parent.parent))
from dividend_grid.datasource import normalize_a_code


@pytest.mark.parametrize("raw,expected", [
    ("601318", "sh.601318"),
    ("600519", "sh.600519"),
    ("000001", "sz.000001"),
    ("300750", "sz.300750"),
    ("sh.601318", "sh.601318"),
    ("SZ.000001", "sz.000001"),
    ("601318.SH", "sh.601318"),
    ("601318.SS", "sh.601318"),
    ("000001.SZ", "sz.000001"),
    ("sh601318", "sh.601318"),
    ("sz000001", "sz.000001"),
    ("  601318  ", "sh.601318"),
])
def test_normalize_valid(raw, expected):
    assert normalize_a_code(raw) == expected


@pytest.mark.parametrize("bad", [
    "", "   ", "60131", "6013188", "abcdef", "601318.XX", "12345a",
])
def test_normalize_invalid(bad):
    with pytest.raises(ValueError):
        normalize_a_code(bad)


# ---------- 报告年度判定(纯函数, 不依赖网络) ----------
from dividend_grid.datasource import _infer_report_year


@pytest.mark.parametrize("announce,exp_year,exp_kind", [
    ("2025-03-26", "2024", "年报"),   # 3月公告 → 上一年度年报
    ("2024-03-26", "2023", "年报"),
    ("2025-04-30", "2024", "年报"),   # 4月边界内 → 上一年度年报
    ("2025-05-01", "2025", "中期"),   # 5月边界 → 当年中期
    ("2025-08-27", "2025", "中期"),   # 8月 → 当年中期
    ("2025-12-30", "2025", "中期"),   # 年底中期公告
])
def test_infer_report_year(announce, exp_year, exp_kind):
    assert _infer_report_year(announce) == (exp_year, exp_kind)


@pytest.mark.parametrize("bad", ["", "not-a-date", None, "2025-13-01"])
def test_infer_report_year_invalid(bad):
    assert _infer_report_year(bad) == ("", "")


# ---------- 完整年报年度归集(纯函数) ----------
import datetime as dt
from decimal import Decimal
from dividend_grid.datasource import _select_complete_annual, DividendComponent


def _c(regist, amount, year, kind):
    return DividendComponent(regist_date=regist, cash_pre_tax=Decimal(amount),
                             report_year=year, kind=kind)


def test_select_annual_招行场景_跳过仅中期年度():
    """招行: 2024年度年报2.0(完整) + 2025年度仅中期1.013(年报未出) → 取2024=2.0"""
    today = dt.date(2026, 7, 3)
    comps = [
        _c("2024-07-10", "1.972", "2023", "年报"),
        _c("2025-07-10", "2", "2024", "年报"),
        _c("2026-01-15", "1.013", "2025", "中期"),  # 2025年报末期未公告 → 不完整
    ]
    total, year, yc = _select_complete_annual(comps, today)
    assert year == "2024"
    assert total == Decimal("2")
    assert len(yc) == 1


def test_select_annual_平安场景_中期加末期():
    """平安: 2025年度 中期0.95 + 年报末期1.75 → 取2025=2.70"""
    today = dt.date(2026, 6, 25)
    comps = [
        _c("2025-06-27", "1.62", "2024", "年报"),
        _c("2025-10-23", "0.95", "2025", "中期"),
        _c("2026-06-09", "1.75", "2025", "年报"),
    ]
    total, year, yc = _select_complete_annual(comps, today)
    assert year == "2025"
    assert total == Decimal("2.70")
    assert len(yc) == 2


def test_select_annual_未登记不算完整():
    """年报末期已公告但登记日在未来 → 该年度不完整, 回退上一年度"""
    today = dt.date(2026, 6, 1)
    comps = [
        _c("2025-07-10", "2", "2024", "年报"),
        _c("2026-07-10", "1.003", "2025", "年报"),  # 登记日在 today 之后
    ]
    total, year, yc = _select_complete_annual(comps, today)
    assert year == "2024"
    assert total == Decimal("2")


def test_select_annual_无年报数据():
    """全部只有中期 → 无完整年度"""
    today = dt.date(2026, 7, 1)
    comps = [_c("2026-01-15", "1.013", "2025", "中期")]
    total, year, yc = _select_complete_annual(comps, today)
    assert year == ""
    assert total == Decimal("0")
