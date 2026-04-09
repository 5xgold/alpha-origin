"""统一行情数据获取模块

数据源：
- A股行情/指数行情/行业分类/指数成分股 → baostock（不复权）
- 港股行情 → 东方财富 HTTP API（不复权）
- 申万行业指数收益率 → 东方财富 HTTP API
"""

import sys
import json
import atexit
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import baostock as bs
import requests

sys.path.append(str(Path(__file__).parent.parent))
from config import CACHE_DIR, CACHE_EXPIRY_DAYS, SECTOR_CACHE_DAYS

# ============================================================
# baostock 生命周期
# ============================================================
_bs_logged_in = False


def _ensure_bs_login():
    global _bs_logged_in
    if not _bs_logged_in:
        bs.login()
        _bs_logged_in = True
        atexit.register(bs.logout)


# ============================================================
# 内部工具函数
# ============================================================

def _to_bs_code(code_str):
    """A股代码 → baostock 格式 (sh.600519 / sz.000001)"""
    code_str = code_str.zfill(6)
    if code_str[0] in ('6', '9'):
        return f"sh.{code_str}"
    return f"sz.{code_str}"


def _to_bs_date(date_str):
    """'20250101' → '2025-01-01'"""
    d = str(date_str).replace("-", "")
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def _is_hk(code_str):
    """5位纯数字 → 港股"""
    return len(code_str) == 5 and code_str.isdigit()


def _cache_valid(cache_file, expiry_days):
    """检查缓存文件是否有效"""
    if not cache_file.exists():
        return False
    cache_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
    return datetime.now() - cache_time < timedelta(days=expiry_days)


# ============================================================
# ETF 行业分类（从 brinson.py 迁移）
# ============================================================

ETF_SECTOR_MAP = {
    "券商": "非银金融", "证券": "非银金融", "保险": "非银金融", "金融": "非银金融",
    "银行": "银行",
    "医药": "医药生物", "医疗": "医药生物", "生物": "医药生物", "创新药": "医药生物",
    "白酒": "食品饮料", "食品": "食品饮料", "消费": "食品饮料",
    "军工": "国防军工", "国防": "国防军工",
    "新能源": "电力设备", "光伏": "电力设备", "锂电": "电力设备", "储能": "电力设备",
    "电力": "公用事业",
    "芯片": "电子", "半导体": "电子", "电子": "电子",
    "计算机": "计算机", "软件": "计算机", "信息技术": "计算机", "云计算": "计算机",
    "互联网": "传媒", "传媒": "传媒", "游戏": "传媒",
    "通信": "通信", "5G": "通信",
    "地产": "房地产", "房地产": "房地产",
    "建筑": "建筑装饰", "建材": "建筑装饰",
    "钢铁": "钢铁",
    "煤炭": "煤炭",
    "有色": "有色金属", "稀土": "有色金属",
    "化工": "基础化工",
    "汽车": "汽车", "新能源车": "汽车",
    "家电": "家用电器",
    "农业": "农林牧渔", "养殖": "农林牧渔", "猪": "农林牧渔",
    "机械": "机械设备", "机器人": "机械设备",
    "交通": "交通运输", "物流": "交通运输", "航运": "交通运输",
    "纺织": "纺织服饰",
    "商贸": "商贸零售",
    "环保": "环保",
    "石油": "石油石化", "石化": "石油石化",
    "美容": "美容护理",
}

BROAD_ETF_KEYWORDS = [
    "沪深300", "中证500", "中证1000", "上证50", "创业板", "科创",
    "红利", "价值", "成长", "MSCI", "恒生", "纳斯达克", "标普",
]


def _classify_etf(name):
    """根据 ETF 名称推断行业"""
    if not name:
        return "指数"
    for kw in BROAD_ETF_KEYWORDS:
        if kw in name:
            return "指数"
    for kw, sector in ETF_SECTOR_MAP.items():
        if kw in name:
            return sector
    return "指数"


# ============================================================
# 公开 API：股票行情
# ============================================================

def get_stock_prices(code, start_date, end_date):
    """获取股票历史行情（带缓存）

    A股 → baostock, 港股 → 新浪 HTTP
    返回 DataFrame[date, open, close, high, low, volume]
    """
    code_str = str(code).strip()
    is_hk = _is_hk(code_str)
    if not is_hk:
        code_str = code_str.zfill(6)

    cache_file = Path(CACHE_DIR) / f"{code_str}_{start_date}_{end_date}_raw.csv"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if _cache_valid(cache_file, CACHE_EXPIRY_DAYS):
        return pd.read_csv(cache_file, parse_dates=['date'])

    try:
        if is_hk:
            df = _fetch_hk_prices(code_str, start_date, end_date)
        else:
            df = _fetch_a_stock_prices(code_str, start_date, end_date)

        df['date'] = pd.to_datetime(df['date'])
        df.to_csv(cache_file, index=False)
        return df
    except Exception as e:
        print(f"警告: 获取 {code_str} 行情失败: {e}")
        return pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])


def _fetch_a_stock_prices(code_str, start_date, end_date):
    """baostock 获取 A 股行情"""
    _ensure_bs_login()
    bs_code = _to_bs_code(code_str)
    rs = bs.query_history_k_data_plus(
        bs_code,
        "date,open,high,low,close,volume",
        start_date=_to_bs_date(start_date),
        end_date=_to_bs_date(end_date),
        frequency="d",
        adjustflag="3",  # 不复权
    )
    rows = []
    while (rs.error_code == '0') and rs.next():
        rows.append(rs.get_row_data())
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=["close"])
    return df


def _fetch_hk_prices(code_str, start_date, end_date):
    """东方财富 HTTP 获取港股行情（不复权）"""
    # 东方财富港股 secid 前缀为 116
    url = (
        "https://33.push2his.eastmoney.com/api/qt/stock/kline/get"
        f"?secid=116.{code_str}"
        "&fields1=f1,f2,f3,f4,f5,f6"
        "&fields2=f51,f52,f53,f54,f55,f56"
        "&klt=101"  # 日K
        "&fqt=0"    # 不复权
        "&end=20500000"
        "&lmt=1000000"
    )
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    klines = data.get("data", {}).get("klines", [])
    if not klines:
        return pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])

    records = []
    for line in klines:
        parts = line.split(",")
        records.append({
            'date': parts[0],
            'open': float(parts[1]),
            'close': float(parts[2]),
            'high': float(parts[3]),
            'low': float(parts[4]),
            'volume': int(parts[5]),
        })

    df = pd.DataFrame(records)
    df['date'] = pd.to_datetime(df['date'])

    start_dt = pd.to_datetime(start_date, format='%Y%m%d')
    end_dt = pd.to_datetime(end_date, format='%Y%m%d')
    df = df[(df['date'] >= start_dt) & (df['date'] <= end_dt)]
    return df.sort_values('date').reset_index(drop=True)


# ============================================================
# 公开 API：基准指数行情
# ============================================================

def get_benchmark_prices(benchmark_index, start_date, end_date):
    """获取基准指数行情（带缓存）→ baostock

    返回 DataFrame[date, open, close, high, low, volume]
    """
    cache_file = Path(CACHE_DIR) / f"benchmark_{benchmark_index}_{start_date}_{end_date}.csv"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if _cache_valid(cache_file, CACHE_EXPIRY_DAYS):
        return pd.read_csv(cache_file, parse_dates=['date'])

    _ensure_bs_login()

    # 尝试 sh/sz 两个前缀
    df = None
    for prefix in ['sh', 'sz']:
        bs_code = f"{prefix}.{benchmark_index}"
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume",
            start_date=_to_bs_date(start_date),
            end_date=_to_bs_date(end_date),
            frequency="d",
        )
        rows = []
        while (rs.error_code == '0') and rs.next():
            rows.append(rs.get_row_data())
        if rows:
            df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
            break

    if df is None or df.empty:
        print(f"错误: 获取基准指数 {benchmark_index} 失败")
        sys.exit(1)

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df['date'] = pd.to_datetime(df['date'])
    df = df.dropna(subset=["close"])

    if df.empty:
        print(f"错误: 指数 {benchmark_index} 在 {start_date}~{end_date} 无数据")
        sys.exit(1)

    df.to_csv(cache_file, index=False)
    return df


# ============================================================
# 公开 API：个股行业分类
# ============================================================

def get_stock_sector(code, name=""):
    """获取个股申万一级行业（带缓存）

    A股 → baostock industry, 港股 → "境外", ETF → 名称推断
    """
    code_str = str(code).strip()

    if _is_hk(code_str):
        return "境外"

    code_str = code_str.zfill(6)

    # ETF 判断
    etf_prefixes = ("51", "15", "16", "56", "58", "59")
    if code_str[:2] in etf_prefixes:
        return _classify_etf(name)

    # 查缓存
    cache_file = Path(CACHE_DIR) / "sectors" / f"{code_str}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if _cache_valid(cache_file, SECTOR_CACHE_DAYS):
        data = json.loads(cache_file.read_text())
        return data.get("sector", "其他")

    # baostock 获取行业
    try:
        _ensure_bs_login()
        rs = bs.query_stock_industry()
        sector = "其他"
        while (rs.error_code == '0') and rs.next():
            row = rs.get_row_data()
            # row: [updateDate, code, code_name, industry, industryClassification]
            if len(row) >= 4 and row[1].endswith(code_str):
                sector = row[3] if row[3] else "其他"
                break

        cache_file.write_text(json.dumps({"sector": sector, "code": code_str}, ensure_ascii=False))
        return sector
    except Exception as e:
        print(f"  警告: 获取 {code_str} 行业失败: {e}")
        return "其他"


# ============================================================
# 公开 API：申万行业指数收益率
# ============================================================

# 申万一级行业 → 东方财富板块代码
_SW_SECTOR_CODES = {
    "农林牧渔": "BK0474", "基础化工": "BK0479", "钢铁": "BK0478",
    "有色金属": "BK0480", "电子": "BK0459", "汽车": "BK0481",
    "家用电器": "BK0465", "食品饮料": "BK0477", "纺织服饰": "BK0471",
    "轻工制造": "BK0469", "医药生物": "BK0465", "公用事业": "BK0458",
    "交通运输": "BK0456", "房地产": "BK0451", "商贸零售": "BK0467",
    "社会服务": "BK0468", "银行": "BK0475", "非银金融": "BK0473",
    "综合": "BK0485", "建筑材料": "BK0463", "建筑装饰": "BK0464",
    "电力设备": "BK0459", "国防军工": "BK0461", "计算机": "BK0460",
    "传媒": "BK0457", "通信": "BK0462", "煤炭": "BK0476",
    "石油石化": "BK0482", "环保": "BK0484", "美容护理": "BK0483",
    "机械设备": "BK0466",
}


def get_sw_sector_returns(start_date, end_date):
    """获取申万一级行业指数收益率 → 东方财富 HTTP

    返回 {sector_name: {"return": float, "weight": 0}}
    """
    result = {}
    start_dt = pd.to_datetime(start_date, format='%Y%m%d')
    end_dt = pd.to_datetime(end_date, format='%Y%m%d')

    for sector_name, em_code in _SW_SECTOR_CODES.items():
        try:
            # 东方财富行情接口
            url = (
                "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                f"?secid=90.{em_code}"
                "&fields1=f1,f2,f3,f4,f5,f6"
                "&fields2=f51,f52,f53,f54,f55,f56"
                "&klt=101"  # 日K
                f"&beg={start_date}"
                f"&end={end_date}"
                "&fqt=1"
            )
            resp = requests.get(url, timeout=10)
            data = resp.json()
            klines = data.get("data", {}).get("klines", [])
            if klines and len(klines) >= 2:
                first = klines[0].split(",")
                last = klines[-1].split(",")
                first_close = float(first[2])
                last_close = float(last[2])
                ret = (last_close - first_close) / first_close
                result[sector_name] = {"return": ret, "weight": 0}
        except Exception:
            continue

    return result


# ============================================================
# 公开 API：指数成分股
# ============================================================

def get_index_constituents(benchmark_index):
    """获取指数成分股代码列表 → baostock

    返回 [code_str, ...]（6位代码，不含前缀）
    """
    _ensure_bs_login()

    # baostock 提供特定指数的成分股查询
    query_map = {
        "000300": bs.query_hs300_stocks,
        "000905": bs.query_zz500_stocks,
    }

    codes = []
    query_fn = query_map.get(benchmark_index)

    if query_fn:
        rs = query_fn()
        while (rs.error_code == '0') and rs.next():
            row = rs.get_row_data()
            # row[1] = "sh.600000" 格式
            if len(row) >= 2:
                codes.append(row[1].split(".")[-1])
        return codes

    # 通用方案：query_stock_basic 获取全量后无法按指数筛选
    # 对于不支持的指数，返回空列表，调用方会 fallback 等权
    print(f"  警告: baostock 不直接支持指数 {benchmark_index} 的成分股查询，将使用等权近似")
    return codes
