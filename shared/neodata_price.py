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

PROXY_PORT = os.getenv("AUTH_GATEWAY_PORT", "19000")
BASE_URL = f"http://localhost:{PROXY_PORT}/proxy/api"
REMOTE_URL = "https://jprx.m.qq.com/aizone/skillserver/v1/proxy/teamrouter_neodata/query"
CACHE_DIR = Path(os.getenv("QUANT_CACHE_DIR", "/Users/5xgold/PythonProjects/data/cache"))
CACHE_EXPIRY_DAYS = 1

_PREFIX_MAP = {
    "60": "sh",
    "68": "sh",
    "00": "sz",
    "30": "sz",
    "15": "sz",
    "16": "sz",
    "08": "sz",
}
_HK_CODE_MAP = {}

_EMPTY_DF_COLS = ["date", "open", "high", "low", "close", "volume"]
_QUOTE_TYPE_KEYWORDS = (
    "股票实时行情",
    "股票数据库",
    "基金实时行情",
    "ETF实时行情",
    "基金数据库",
)


def _code_prefix(code: str) -> str:
    c = str(code).strip().lstrip("sz").lstrip("sh")
    for k, v in _PREFIX_MAP.items():
        if c.startswith(k):
            return v
    return "sh"


def _cache_valid(path: Path, expiry_days: int) -> bool:
    if not path.exists():
        return False
    age_days = (datetime.datetime.now() - datetime.datetime.fromtimestamp(path.stat().st_mtime)).days
    return age_days < expiry_days


def _call_neodata(query: str) -> dict:
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


def _parse_price_from_content(content: str) -> dict | None:
    price = re.search(r"最新价格:\s*([\d.]+)", content)
    prev_close = re.search(r"昨日收盘价格:\s*([\d.]+)", content)
    open_px = re.search(r"今日开盘价格:\s*([\d.]+)", content)
    high = re.search(r"最高价?:\s*([\d.]+)", content)
    low = re.search(r"最低价?:\s*([\d.]+)", content)
    change_pct = re.search(r"当日涨跌幅:\s*([\-+\d.]+)%", content)
    volume_str = re.search(r"成交数量\(手\):\s*([\d,]+)", content)
    pe = re.search(r"市盈率\(TTM\):\s*([\d.]+)", content)
    pb = re.search(r"市净率:\s*([\d.]+)", content)

    if not price:
        return None

    p = float(price.group(1))
    pc = float(prev_close.group(1)) if prev_close else p
    o = float(open_px.group(1)) if open_px else p
    h = float(high.group(1)) if high else p
    l = float(low.group(1)) if low else p
    v = int(volume_str.group(1).replace(",", "")) * 100 if volume_str else 0

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
    pe_ttm = re.search(r"市盈率TTM[：:]\s*([\d.]+)", content)
    pct = re.search(r"历史百分位(?:\s*[（(]\s*[%％]\s*[）)])?[：:]\s*([\d.]+)", content)
    if not pe_ttm:
        return None
    return {
        "pe_ttm": float(pe_ttm.group(1)),
        "pe_percentile": float(pct.group(1)) if pct else None,
    }


def get_neodata_prices(code: str, start_date: str, end_date: str, adjust: str = "") -> dict:
    code = str(code).strip()
    try:
        result = _call_neodata(f"{code} 今日行情")
    except Exception as e:
        print(f"  [NeoData] API 调用失败 {code}: {e}")
        return {}

    data = result.get("data", {})
    api_data = data.get("apiData", {})
    recall_list = api_data.get("apiRecall", [])
    entity_list = api_data.get("entity", [])

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
        if entity_list:
            stock_info["name"] = entity_list[0].get("name") or code
            stock_info["raw"] = ""

    stock_info["code"] = code
    stock_info["date"] = end_date

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
            if any(keyword in t for keyword in _QUOTE_TYPE_KEYWORDS):
                info.update(_parse_price_from_content(c) or {})
            if "大盘指数估值" in t:
                info.update(_parse_index_pe(c) or {})
        result[name] = info
    return result
