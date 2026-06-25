"""股息网格计算器 — CLI

按固定股息率步进生成买入/减仓网格, 所有档位股息率 > 十年期无风险利率。

Usage:
    python app/dividend_grid/cli.py --name 中国平安 --dividend 2.70 --price 53.48 --rf 1.73
    python app/dividend_grid/cli.py --name 中国平安 --dividend 2.70 --price 53.48 \
        --rf 1.73 --step 0.5 --low 4.0 --high 9.0
"""

import sys
import argparse
from decimal import Decimal
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))
from dividend_grid.grid import build_grid, to_decimal

_HUNDRED = Decimal("100")
_Q2 = Decimal("0.01")   # 两位小数
_Q1 = Decimal("0.1")    # 一位小数


def _pct(x, q=_Q2):
    """小数 → 百分数, 量化到指定精度"""
    return (to_decimal(x) * _HUNDRED).quantize(q)


def _fmt_table(name, dividend, price, rf, levels):
    dividend, price, rf = to_decimal(dividend), to_decimal(price), to_decimal(rf)
    current_yield = dividend / price
    lines = []
    lines.append(f"标的: {name}")
    lines.append(f"每股股息(税前): {dividend.quantize(Decimal('0.0001'))} 元 "
                 f"| 现价: {price.quantize(_Q2)} 元 "
                 f"| 当前股息率: {_pct(current_yield)}%")
    if rf > 0:
        ratio = (current_yield / rf).quantize(_Q2)
        lines.append(f"无风险利率(十年期国债): {_pct(rf)}% | 当前性价比: {ratio}x")
    lines.append("")
    header = f"{'目标股息率':>8} | {'买入价':>8} | {'较现价':>8} | {'性价比':>7} | 动作"
    lines.append(header)
    lines.append("-" * len(header))
    for lv in levels:
        mark = " ◀现价" if lv.action == "现价" else ""
        ratio = f"{lv.ratio_vs_riskfree.quantize(_Q2)}x" if lv.ratio_vs_riskfree is not None else "  ∞"
        pct = _pct(lv.pct_vs_current, _Q1)
        lines.append(
            f"{_pct(lv.yield_rate, _Q1):>6}% | {lv.price.quantize(_Q2):>8} | "
            f"{pct:>+7}% | {ratio:>7} | {lv.action}{mark}"
        )
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="股息网格计算器")
    p.add_argument("--name", default="标的", help="标的名称")
    p.add_argument("--dividend", type=str, required=True, help="每股税前股息(元)")
    p.add_argument("--price", type=str, required=True, help="当前股价(元)")
    p.add_argument("--rf", type=str, required=True,
                   help="无风险利率(百分数, 如十年期国债 1.73 表示 1.73%%)")
    p.add_argument("--step", type=str, default="0.5", help="股息率步进(百分数, 默认 0.5)")
    p.add_argument("--low", type=str, default="4.0", help="最低股息率(百分数, 默认 4.0)")
    p.add_argument("--high", type=str, default="9.0", help="最高股息率(百分数, 默认 9.0)")
    args = p.parse_args()

    # 全程 Decimal: 字符串入参 → Decimal, 百分数 ÷ 100 转小数
    dividend = to_decimal(args.dividend)
    price = to_decimal(args.price)
    rf = to_decimal(args.rf) / _HUNDRED

    levels = build_grid(
        dividend=dividend,
        current_price=price,
        risk_free_rate=rf,
        step=to_decimal(args.step) / _HUNDRED,
        low_yield=to_decimal(args.low) / _HUNDRED,
        high_yield=to_decimal(args.high) / _HUNDRED,
    )
    print(_fmt_table(args.name, dividend, price, rf, levels))


if __name__ == "__main__":
    main()
