"""股息网格 — 数据自动获取层

输入 A 股代码, 自动获取:
- TTM 每股股息(过去 12 个月已登记的现金分红求和, 税前)
- 最新收盘价
- 股票名称
- 十年期国债收益率(外部源, 失败回落手动输入)

数据源: baostock(分红/行情/名称) + 中债/外部(国债收益率)
数值全程 decimal.Decimal。
"""

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

import baostock as bs

from dividend_grid.grid import to_decimal

# 默认十年期国债收益率(自动获取失败时的回落值, 2026-06 水平)
DEFAULT_TREASURY_YIELD_PCT = Decimal("1.73")
_TTM_DAYS = 365


@dataclass
class DividendComponent:
    """单笔分红"""
    regist_date: str       # 股权登记日
    cash_pre_tax: Decimal  # 每股税前现金股息


@dataclass
class StockSnapshot:
    """标的自动获取结果"""
    code: str                       # 规范化代码, 如 sh.601318
    name: str                       # 股票名称
    price: Decimal                  # 最新收盘价
    price_date: str                 # 收盘价日期
    ttm_dividend: Decimal           # TTM 每股股息(税前, 过去 365 天已登记)
    annual_dividend: Decimal        # 最近完整年度每股股息(税前)
    annual_year: str                # 该年度标识(如 "2025"), 无则为 ""
    components: list[DividendComponent] = field(default_factory=list)        # TTM 明细
    annual_components: list[DividendComponent] = field(default_factory=list)  # 年度明细
    warnings: list[str] = field(default_factory=list)


def normalize_a_code(code: str) -> str:
    """把用户输入的 A 股代码规范成 baostock 格式 sh.xxxxxx / sz.xxxxxx

    支持: 601318 / 600519 / 000001 / sh.601318 / SZ000001 / 601318.SH 等
    """
    raw = code.strip().lower().replace(" ", "")
    if not raw:
        raise ValueError("股票代码不能为空")

    # 已带前缀 sh./sz.
    if raw.startswith(("sh.", "sz.")):
        prefix, digits = raw[:2], raw[3:]
    # 形如 601318.sh
    elif raw.endswith((".sh", ".ss")):
        prefix, digits = "sh", raw.split(".")[0]
    elif raw.endswith(".sz"):
        prefix, digits = "sz", raw.split(".")[0]
    # 形如 sh601318
    elif raw.startswith("sh") and raw[2:].isdigit():
        prefix, digits = "sh", raw[2:]
    elif raw.startswith("sz") and raw[2:].isdigit():
        prefix, digits = "sz", raw[2:]
    else:
        digits = raw
        # 纯数字: 6 开头沪市, 0/3 开头深市
        if not digits.isdigit() or len(digits) != 6:
            raise ValueError(f"无法识别的 A 股代码: {code}")
        prefix = "sh" if digits[0] == "6" else "sz"

    if not digits.isdigit() or len(digits) != 6:
        raise ValueError(f"无法识别的 A 股代码: {code}")
    return f"{prefix}.{digits}"


# ============================================================
# baostock 会话(轻量, 工具独立运行, 不依赖 shared.data_provider)
# ============================================================
class _BsSession:
    """with 块内登录/登出 baostock"""
    def __enter__(self):
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
        return self

    def __exit__(self, *exc):
        bs.logout()
        return False


def _rs_to_rows(rs):
    """把 baostock ResultData 读成 list[list]"""
    if rs.error_code != "0":
        raise RuntimeError(f"baostock 查询失败: {rs.error_msg}")
    rows = []
    while rs.next():
        rows.append(rs.get_row_data())
    return rs.fields, rows


def fetch_ttm_dividends(bs_code: str, today: dt.date | None = None) -> list[DividendComponent]:
    """获取过去 12 个月已登记的每股税前现金股息明细(去重)

    baostock 同一笔分红可能返回多行(预案/实施), 以(登记日, 税前金额)去重。
    只统计登记日在 [today-365, today] 内的分红。
    """
    today = today or dt.date.today()
    cutoff = today - dt.timedelta(days=_TTM_DAYS)

    seen = set()
    comps: list[DividendComponent] = []
    # 跨年查询: TTM 窗口可能横跨两个自然年
    for year in (today.year, today.year - 1):
        _, rows = _rs_to_rows(
            bs.query_dividend_data(code=bs_code, year=str(year), yearType="report")
        )
        for r in rows:
            regist = r[5]                  # dividRegistDate
            cash_pre = r[9]                # dividCashPsBeforeTax
            if not regist or not cash_pre:
                continue
            try:
                rd = dt.date.fromisoformat(regist)
            except ValueError:
                continue
            if not (cutoff <= rd <= today):
                continue
            amount = to_decimal(cash_pre)
            if amount <= 0:
                continue
            key = (regist, str(amount))
            if key in seen:
                continue
            seen.add(key)
            comps.append(DividendComponent(regist_date=regist, cash_pre_tax=amount))

    comps.sort(key=lambda c: c.regist_date)
    return comps


def _dedup_year_dividends(bs_code: str, year: int) -> list[DividendComponent]:
    """取某归属年度(report)的现金分红明细, 按(登记日,金额)去重

    yearType=report 时 year 表示分红所属报告年度, 一个年度可能有中期+末期多笔。
    仅保留有登记日的实施记录(去掉纯预案行)。
    """
    _, rows = _rs_to_rows(
        bs.query_dividend_data(code=bs_code, year=str(year), yearType="report")
    )
    seen = set()
    comps: list[DividendComponent] = []
    for r in rows:
        regist, cash_pre = r[5], r[9]
        if not regist or not cash_pre:
            continue
        amount = to_decimal(cash_pre)
        if amount <= 0:
            continue
        key = (regist, str(amount))
        if key in seen:
            continue
        seen.add(key)
        comps.append(DividendComponent(regist_date=regist, cash_pre_tax=amount))
    comps.sort(key=lambda c: c.regist_date)
    return comps


def fetch_annual_dividend(
    bs_code: str, today: dt.date | None = None
) -> tuple[Decimal, str, list[DividendComponent]]:
    """获取最近一个"完整年度"的每股税前股息合计

    完整年度 = 该归属年度的全部分红均已登记(登记日 ≤ today)。从去年往前找:
    优先取上一自然年; 若上一自然年仍有未登记分红(罕见), 再往前推一年。

    Returns:
        (annual_dividend, year_label, components); 找不到时返回 (0, "", [])
    """
    today = today or dt.date.today()
    # 从去年开始往前找最近一个所有分红都已登记的完整年度
    for year in (today.year - 1, today.year - 2, today.year - 3):
        comps = _dedup_year_dividends(bs_code, year)
        if not comps:
            continue
        # 该年度所有分红登记日都已过 → 视为完整年度
        all_registered = all(
            dt.date.fromisoformat(c.regist_date) <= today for c in comps
        )
        if all_registered:
            total = sum((c.cash_pre_tax for c in comps), Decimal("0"))
            return total, str(year), comps
    return Decimal("0"), "", []


def fetch_latest_price(bs_code: str, today: dt.date | None = None) -> tuple[Decimal, str]:
    """获取最新收盘价及其日期(不复权, 取真实价)"""
    today = today or dt.date.today()
    start = (today - dt.timedelta(days=15)).isoformat()
    _, rows = _rs_to_rows(
        bs.query_history_k_data_plus(
            bs_code, "date,close", start_date=start, end_date=today.isoformat(),
            frequency="d", adjustflag="3",  # 3=不复权
        )
    )
    valid = [r for r in rows if r[1]]
    if not valid:
        raise RuntimeError(f"未取到 {bs_code} 的近期收盘价")
    last = valid[-1]
    return to_decimal(last[1]), last[0]


def fetch_stock_name(bs_code: str) -> str:
    """获取股票名称, 失败返回代码本身"""
    try:
        _, rows = _rs_to_rows(bs.query_stock_basic(code=bs_code))
        if rows and rows[0][1]:
            return rows[0][1]
    except RuntimeError:
        pass
    return bs_code


def fetch_treasury_yield_pct() -> tuple[Decimal, bool]:
    """获取十年期国债收益率(百分数)

    Returns:
        (yield_pct, ok): ok=True 表示自动获取成功; False 表示回落到默认值
    """
    try:
        import requests
        # 中债登/Akshare 公开接口不稳定, 这里用 stooip 兜底; 失败即回落
        resp = requests.get(
            "https://www.chinabond.com.cn/cb/cn/yjfx/zzsj/gzqx/index.shtml",
            timeout=5,
        )
        # 该页面为 HTML, 解析成本高且易变; 留接口位, 默认回落
        raise RuntimeError("国债收益率自动源未配置稳定解析")
    except Exception:
        return DEFAULT_TREASURY_YIELD_PCT, False


def fetch_snapshot(code: str, today: dt.date | None = None) -> StockSnapshot:
    """统一入口: 输入 A 股代码, 返回快照(名称/价格/TTM 股息)

    国债收益率不在此处获取(交由调用方, 便于手动覆盖)。
    """
    bs_code = normalize_a_code(code)
    warnings: list[str] = []
    with _BsSession():
        name = fetch_stock_name(bs_code)
        price, price_date = fetch_latest_price(bs_code, today)
        comps = fetch_ttm_dividends(bs_code, today)
        annual, annual_year, annual_comps = fetch_annual_dividend(bs_code, today)

    ttm = sum((c.cash_pre_tax for c in comps), Decimal("0"))
    if not comps:
        warnings.append("过去 12 个月未查到现金分红, TTM 股息为 0(可能未分红或数据缺失), 请手动核对")
    if annual <= 0:
        warnings.append("未找到最近完整年度的分红记录, 年度口径不可用")
    return StockSnapshot(
        code=bs_code, name=name, price=price, price_date=price_date,
        ttm_dividend=ttm, annual_dividend=annual, annual_year=annual_year,
        components=comps, annual_components=annual_comps, warnings=warnings,
    )
