#!/usr/bin/env python3
"""NeoData 行情获取 — 替代 baostock 的 A 股数据源

依赖: requests (Python 标准库已内置)
数据源: NeoData 金融数据搜索 API → 通过 QClaw 网关 localhost:19000

用法:
    from neodata_price import get_neodata_prices
    df = get_neodata_prices("601216", "2026-04-20", "2026-04-28", adjust="")
    df = get_neodata_prices("159792", "2026-04-20", "2026-04-28", adjust="")
    df = get_neodata_prices("00696", "2026-04-20", "2026-04-28", adjust="")
"""
import os
import sys
import re
import uuid
import json
import subprocess
import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("需要安装 requests: pip install requests", file=sys.stderr)
    sys.exit(1)

# ============================================================
# 配置
# ============================================================
PROXY_PORT = os.getenv("AUTH_GATEWAY_PORT", "19000")
BASE_URL = f"http://localhost:{PROXY_PORT}/proxy/api"
REMOTE_URL = "https://jprx.m.qq.com/aizone/skillserver/v1/proxy/teamrouter_neodata/query"
CACHE_DIR = Path(os.getenv("QUANT_CACHE_DIR", "/Users/5xgold/PythonProjects/data/cache"))
CACHE_EXPIRY_DAYS = 1  # 缓存有效期（天）

# 代码前缀映射（ portfolio.toml → 新浪格式）
_PREFIX_MAP = {
    "60": "sh",   # 上交所
    "68": "sh",   # 科创板
    "00": "sz",   # 深交所主板/中小板
    "30": "sz",   # 创业板
    "15": "sz",   # 深交所ETF
    "16": "sz",   # 深交所ETF
    "08": "sz",   # 三板
}
# 港股特殊处理
_HK_CODE_MAP = {}  # 00696 等在 NeoData 直接用数字代码

_EMPTY_DF_COLS = ["date", "open", "high", "low", "close", "volume"]
_QUOTE_TYPE_KEYWORDS = (
    "股票实时行情",
    "股票数据库",
    "基金实时行情",
    "ETF实时行情",
    "基金数据库",
)


def _code_prefix(code: str) -> str:
    """返回新浪格式前缀 sh/sz"""
    c = str(code).strip().lstrip("sz").lstrip("sh")
    for k, v in _PREFIX_MAP.items():
        if c.startswith(k):
            return v
    return "sh"  # 默认沪市


def _cache_valid(path: Path, expiry_days: int) -> bool:
    if not path.exists():
        return False
    age_days = (datetime.datetime.now() - datetime.datetime.fromtimestamp(path.stat().st_mtime)).days
    return age_days < expiry_days


# ============================================================
# 核心：调用 NeoData API
# ============================================================
def _call_neodata(query: str) -> dict:
    """发送请求到 NeoData，返回原始 JSON dict"""
    payload = {
        "channel": "neodata",
        "sub_channel": "qclaw",
        "query": query,
        "request_id": uuid.uuid4().hex,
        "data_type": "api",
        "se_params": {},
        "extra_params": {},
    }
    headers = {
        "Content-Type": "application/json",
        "Remote-URL": REMOTE_URL,
    }
    resp = requests.post(BASE_URL, headers=headers, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


# ============================================================
# 解析 NeoData 响应
# ============================================================
def _parse_price_from_content(content: str) -> dict | None:
    """从 NeoData 股票实时行情 content 文本中提取字段"""
    # 匹配 "最新价格:5.55元"
    price = re.search(r"最新价格:\s*([\d.]+)", content)
    prev_close = re.search(r"昨日收盘价格:\s*([\d.]+)", content)
    open_px = re.search(r"今日开盘价格:\s*([\d.]+)", content)
    high = re.search(r"最高价?:\s*([\d.]+)", content)
    low = re.search(r"最低价?:\s*([\d.]+)", content)
    change_pct = re.search(r"当日涨跌幅:\s*([\-+\d.]+)%", content)
    volume_str = re.search(r"成交数量\(手\):\s*([\d,]+)", content)
    amount_str = re.search(r"成交金额\(万元\):\s*([\d,.]+)", content)
    pe = re.search(r"市盈率\(TTM\):\s*([\d.]+)", content)
    pb = re.search(r"市净率:\s*([\d.]+)", content)

    if not price:
        return None

    p = float(price.group(1))
    pc = float(prev_close.group(1)) if prev_close else p
    o = float(open_px.group(1)) if open_px else p
    h = float(high.group(1)) if high else p
    l = float(low.group(1)) if low else p
    v = int(volume_str.group(1).replace(",", "")) * 100 if volume_str else 0  # 手→股

    return {
        "price": p,
        "prev_close": pc,
        "open": o,
        "high": h,
        "low": l,
        "volume": v,
        "change_pct": float(change_pct.group(1)) if change_pct else 0.0,
        "pe": float(pe.group(1)) if pe else None,
        "pb": float(pb.group(1)) if pb else None,
    }


def _extract_security_name(content: str) -> str | None:
    """从 NeoData 文本中提取证券名称，兼容股票/ETF/基金格式。"""
    patterns = (
        r"([^\s(（:：]{2,32})\s*[（(]代码[:：]",
        r"名称[:：]\s*([^\s(（:：]{2,32})",
    )
    for pattern in patterns:
        match = re.search(pattern, content)
        if match:
            return match.group(1).strip()
    return None


def _parse_index_pe(content: str) -> dict | None:
    """从 NeoData 大盘指数估值 content 中提取 PE 分位"""
    pe_ttm = re.search(r"市盈率TTM[：:]\s*([\d.]+)", content)
    pct = re.search(r"历史百分位(?:\s*[（(]\s*[%％]\s*[）)])?[：:]\s*([\d.]+)", content)
    if not pe_ttm:
        return None
    return {
        "pe_ttm": float(pe_ttm.group(1)),
        "pe_percentile": float(pct.group(1)) if pct else None,
    }


# ============================================================
# 公开接口
# ============================================================
def get_neodata_prices(code: str, start_date: str, end_date: str, adjust: str = "") -> dict:
    """获取单只股票/ETF/指数的近期行情（单日）

    Args:
        code: 股票代码（6位纯数字）
        start_date: 开始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        adjust: 复权方式（本接口只支持不复权，返回 raw）

    Returns:
        dict {
            "code": str,
            "name": str,
            "date": str,           # 最新行情日期
            "open/high/low/close": float,
            "volume": int,
            "change_pct": float,
            "pe": float | None,
            "pb": float | None,
            "raw": str,            # 原始 content
            "df": DataFrame | None,
        }
        失败时返回空 dict {}
    """
    code = str(code).strip()
    is_hk = code.startswith("00") and len(code) == 5  # 简单判断：5位港股

    # 查询名称
    try:
        result = _call_neodata(f"{code} 今日行情")
    except Exception as e:
        print(f"  [NeoData] API 调用失败 {code}: {e}")
        return {}

    data = result.get("data", {})
    api_data = data.get("apiData", {})
    recall_list = api_data.get("apiRecall", [])
    entity_list = api_data.get("entity", [])

    # 提取股票实时行情
    stock_info = {}
    for item in recall_list:
        t = item.get("type", "")
        c = item.get("content", "")
        if any(keyword in t for keyword in _QUOTE_TYPE_KEYWORDS):
            stock_info = _parse_price_from_content(c) or {}
            if stock_info:
                stock_info["raw"] = c
                stock_info["name"] = _extract_security_name(c) or code
                break

    if not stock_info:
        # 降级：尝试从 entity 提取
        if entity_list:
            stock_info["name"] = entity_list[0].get("name") or code
            stock_info["raw"] = ""

    stock_info["code"] = code
    stock_info["date"] = end_date  # 简化：用请求的结束日期

    # 同时拉取指数 PE 分位（大盘指数）
    index_pe_info = {}
    for item in recall_list:
        t = item.get("type", "")
        c = item.get("content", "")
        if "大盘指数估值" in t:
            idx = _parse_index_pe(c)
            if idx:
                index_pe_info.update(idx)

    return {"stock": stock_info, "index_pe": index_pe_info}


def get_index_quotes_neodata(index_names: list[str]) -> dict[str, dict]:
    """批量获取指数行情和 PE 分位

    Args:
        index_names: 如 ["上证指数", "深证成指", "创业板指", "沪深300", "中证500"]
    Returns:
        dict[index_name] -> {price, change_pct, pe_ttm, pe_percentile}
    """
    result = {}
    for name in index_names:
        try:
            resp = _call_neodata(f"{name} 今日行情")
        except Exception as e:
            print(f"  [NeoData] 指数 {name} 失败: {e}")
            result[name] = {}
            continue

        data = resp.get("data", {})
        recall = data.get("apiData", {}).get("apiRecall", [])
        info = {}
        for item in recall:
            t = item.get("type", "")
            c = item.get("content", "")
            if "股票实时行情" in t:
                p = _parse_price_from_content(c)
                if p:
                    info.update({k: v for k, v in p.items() if k != "raw"})
            elif "大盘指数估值" in t:
                pe = _parse_index_pe(c)
                if pe:
                    info.update(pe)
        result[name] = info
    return result


def get_batch_prices(codes: list[str], date: str) -> dict[str, dict]:
    """批量获取多只股票当日行情

    Args:
        codes: 股票代码列表
        date: 行情日期 YYYY-MM-DD
    Returns:
        dict[code] -> stock_info dict
    """
    all_info = {}
    for code in codes:
        info = get_neodata_prices(code, date, date)
        if info.get("stock"):
            all_info[code] = info["stock"]
        else:
            all_info[code] = {}
    return all_info


# ============================================================
# 单元测试
# ============================================================
if __name__ == "__main__":
    print("=== NeoData 行情测试 ===")

    codes = ["601216", "003816", "00696", "159792", "159842"]
    indices = ["上证指数", "深证成指", "创业板指", "沪深300", "中证500"]

    print("\n--- 持仓行情 ---")
    prices = get_batch_prices(codes, "2026-04-28")
    for code, info in prices.items():
        if info:
            print(f"  {code} {info.get('name','')}: "
                  f"现价={info.get('price')} "
                  f"涨跌幅={info.get('change_pct')}% "
                  f"PE={info.get('pe')} "
                  f"成交量={info.get('volume',0)//100:.0f}手")
        else:
            print(f"  {code}: 获取失败")

    print("\n--- 指数行情 ---")
    idx_data = get_index_quotes_neodata(indices)
    for name, info in idx_data.items():
        if info:
            print(f"  {name}: "
                  f"点位={info.get('price')} "
                  f"涨跌幅={info.get('change_pct')}% "
                  f"PE={info.get('pe_ttm')} "
                  f"分位={info.get('pe_percentile')}%")
        else:
            print(f"  {name}: 获取失败")
