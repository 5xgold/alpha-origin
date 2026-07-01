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
