"""统一行情数据获取模块

数据源：
- A股行情 → baostock（不复权）
- 港股行情 → FutuOpenD（不复权）
- 指数行情/行业分类/指数成分股 → baostock
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
from config import CACHE_DIR, CACHE_EXPIRY_DAYS, SECTOR_CACHE_DAYS, FUTU_HOST, FUTU_PORT, TS_TOKEN

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
    # 5开头的 ETF 属于上海交易所 (510xxx, 512xxx, 513xxx, 515xxx, 516xxx, 518xxx, 520xxx, 560xxx, 561xxx, 562xxx, 563xxx)
    if code_str[0] == '5':
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
# 多数据源架构
# ============================================================

_EMPTY_PRICE_DF = pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])

# 数据源注册表：market → [(name, fetcher_fn), ...]
# 按优先级排列，第一个成功即返回
_SOURCE_REGISTRY = {
    'a_stock': [
        ('baostock', '_fetch_a_stock_prices'),
    ],
    'hk_stock': [
        ('futu', '_fetch_hk_futu'),
    ],
}


def _fetch_with_fallback(sources, code_str, start_date, end_date):
    """按优先级尝试多个数据源，第一个成功即返回"""
    last_error = None
    for name, fn_name in sources:
        try:
            fn = globals()[fn_name]
            df = fn(code_str, start_date, end_date)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            last_error = e
            print(f"  数据源 {name} 获取 {code_str} 失败: {e}")
            continue
    if last_error:
        raise last_error
    return _EMPTY_PRICE_DF.copy()


# ============================================================
# 公开 API：股票行情
# ============================================================

def get_stock_prices(code, start_date, end_date):
    """获取股票历史行情（带缓存、多数据源 fallback）

    A股 → baostock, 港股 → FutuOpenD → Yahoo Finance → 东方财富
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
        market = 'hk_stock' if is_hk else 'a_stock'
        sources = _SOURCE_REGISTRY[market]
        df = _fetch_with_fallback(sources, code_str, start_date, end_date)

        df['date'] = pd.to_datetime(df['date'])
        df.to_csv(cache_file, index=False)
        return df
    except Exception as e:
        print(f"警告: 获取 {code_str} 行情失败（所有数据源）: {e}")
        return _EMPTY_PRICE_DF.copy()


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


def _fetch_hk_futu(code_str, start_date, end_date):
    """FutuOpenD 获取港股行情（不复权）"""
    from futu import OpenQuoteContext, KLType, AuType

    ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    try:
        ret, df, _ = ctx.request_history_kline(
            f'HK.{code_str}',
            ktype=KLType.K_DAY,
            autype=AuType.NONE,
            start=_to_bs_date(start_date),
            end=_to_bs_date(end_date),
        )
        if ret != 0 or df is None or df.empty:
            return _EMPTY_PRICE_DF.copy()

        df = df.rename(columns={'time_key': 'date'})
        df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
        df['date'] = pd.to_datetime(df['date'])
        return df.sort_values('date').reset_index(drop=True)
    finally:
        ctx.close()


def _fetch_hk_index_futu(futu_code, start_date, end_date):
    """FutuOpenD 获取港股指数行情（如 HK.800000 恒生指数）

    Args:
        futu_code: 完整 Futu 代码（如 'HK.800000'）
        start_date: 开始日期 (YYYYMMDD)
        end_date: 结束日期 (YYYYMMDD)

    Returns:
        DataFrame[date, open, high, low, close, volume]
    """
    cache_file = Path(CACHE_DIR) / f"benchmark_{futu_code.replace('.', '_')}_{start_date}_{end_date}.csv"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if _cache_valid(cache_file, CACHE_EXPIRY_DAYS):
        return pd.read_csv(cache_file, parse_dates=['date'])

    from futu import OpenQuoteContext, KLType, AuType

    ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    try:
        ret, df, _ = ctx.request_history_kline(
            futu_code,
            ktype=KLType.K_DAY,
            autype=AuType.NONE,
            start=_to_bs_date(start_date),
            end=_to_bs_date(end_date),
        )
        if ret != 0 or df is None or df.empty:
            print(f"错误: FutuOpenD 获取 {futu_code} 失败 (ret={ret})")
            return _EMPTY_PRICE_DF.copy()

        df = df.rename(columns={'time_key': 'date'})
        df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
        df['date'] = pd.to_datetime(df['date'])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['close']).sort_values('date').reset_index(drop=True)

        df.to_csv(cache_file, index=False)
        return df
    finally:
        ctx.close()




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


def get_composite_benchmark_prices(benchmark_components, start_date, end_date):
    """获取复合基准的合成价格序列

    逐个获取各成分指数价格，对齐交易日历后加权合成。

    Args:
        benchmark_components: parse_benchmark_config() 返回的列表
        start_date: 开始日期 (YYYYMMDD)
        end_date: 结束日期 (YYYYMMDD)

    Returns:
        DataFrame[date, close]（归一化合成价格，与 get_benchmark_prices 兼容）
    """
    import numpy as np

    price_series = {}  # {index: Series(date→close)}

    for comp in benchmark_components:
        idx = comp["index"]
        source = comp["source"]

        if source == "futu":
            df = _fetch_hk_index_futu(idx, start_date, end_date)
        else:
            df = get_benchmark_prices(idx, start_date, end_date)

        if df is None or df.empty:
            print(f"错误: 无法获取成分指数 {idx} 的数据")
            sys.exit(1)

        s = df.set_index('date')['close'].astype(float)
        price_series[idx] = s

    # 对齐交易日历：取并集 + forward-fill 处理 A/H 不同假期
    all_dates = sorted(set().union(*(s.index for s in price_series.values())))
    aligned = pd.DataFrame(index=pd.DatetimeIndex(all_dates))

    for idx, s in price_series.items():
        aligned[idx] = s
    aligned = aligned.ffill().bfill()

    # 归一化到 1.0 后加权合成
    composite = pd.Series(0.0, index=aligned.index)
    for comp in benchmark_components:
        idx = comp["index"]
        weight = comp["weight"]
        normalized = aligned[idx] / aligned[idx].iloc[0]
        composite += weight * normalized

    # 转换为与 get_benchmark_prices 兼容的 DataFrame 格式
    result = pd.DataFrame({
        'date': composite.index,
        'close': composite.values,
    }).reset_index(drop=True)

    # 用第一个成分的首日 close 作为基数，使合成价格有实际量纲
    first_idx = benchmark_components[0]["index"]
    base_price = float(price_series[first_idx].iloc[0])
    result['close'] = result['close'] * base_price

    return result

# 国标行业（baostock）→ 申万一级行业映射
_GB_TO_SW = {
    # 农林牧渔
    "A01农业": "农林牧渔", "A02林业": "农林牧渔", "A03牧业": "农林牧渔",
    "A04渔业": "农林牧渔", "A05农、林、牧、渔服务业": "农林牧渔",
    # 基础化工
    "C25石油加工、炼焦和核燃料加工业": "基础化工",
    "C26化学原料和化学制品制造业": "基础化工",
    "C28化学纤维制造业": "基础化工",
    "C29橡胶和塑料制品业": "基础化工",
    "C30非金属矿物制品业": "建筑材料",
    # 钢铁
    "C31黑色金属冶炼和压延加工业": "钢铁",
    # 有色金属
    "C32有色金属冶炼和压延加工业": "有色金属",
    # 机械设备
    "C34通用设备制造业": "机械设备",
    "C35专用设备制造业": "机械设备",
    # 电力设备
    "C38电气机械和器材制造业": "电力设备",
    # 电子
    "C39计算机、通信和其他电子设备制造业": "电子",
    "C40仪器仪表制造业": "电子",
    # 汽车
    "C36汽车制造业": "汽车",
    "C37铁路、船舶、航空航天和其他运输设备制造业": "国防军工",
    # 家用电器
    "C33金属制品业": "家用电器",
    # 食品饮料
    "C13农副食品加工业": "食品饮料", "C14食品制造业": "食品饮料",
    "C15酒、饮料和精制茶制造业": "食品饮料",
    # 纺织服饰
    "C17纺织业": "纺织服饰", "C18纺织服装、服饰业": "纺织服饰",
    "C19皮革、毛皮、羽毛及其制品和制鞋业": "纺织服饰",
    # 轻工制造
    "C20木材加工和木、竹、藤、棕、草制品业": "轻工制造",
    "C21家具制造业": "轻工制造", "C22造纸和纸制品业": "轻工制造",
    "C23印刷和记录媒介复制业": "轻工制造",
    "C24文教、工美、体育和娱乐用品制造业": "轻工制造",
    # 医药生物
    "C27医药制造业": "医药生物",
    # 公用事业
    "D44电力、热力生产和供应业": "公用事业",
    "D45燃气生产和供应业": "公用事业",
    "D46水的生产和供应业": "公用事业",
    # 交通运输
    "G53铁路运输业": "交通运输", "G54道路运输业": "交通运输",
    "G55水上运输业": "交通运输", "G56航空运输业": "交通运输",
    "G57管道运输业": "交通运输", "G58装卸搬运和运输代理业": "交通运输",
    "G59仓储业": "交通运输", "G60邮政业": "交通运输",
    # 房地产
    "K70房地产业": "房地产",
    # 商贸零售
    "F51批发业": "商贸零售", "F52零售业": "商贸零售",
    # 社会服务
    "H61住宿业": "社会服务", "H62餐饮业": "社会服务",
    "O77生态保护和环境治理业": "环保",
    "N78公共设施管理业": "社会服务",
    "R86新闻和出版业": "传媒", "R87广播、电视、电影和影视录音制作业": "传媒",
    "R88文化艺术业": "传媒", "R89体育": "社会服务",
    "R90娱乐业": "传媒",
    # 银行
    "J66货币金融服务": "银行",
    # 非银金融
    "J67资本市场服务": "非银金融", "J68保险业": "非银金融",
    "J69其他金融业": "非银金融",
    # 计算机
    "I63电信、广播电视和卫星传输服务": "通信",
    "I64互联网和相关服务": "计算机",
    "I65软件和信息技术服务业": "计算机",
    # 建筑装饰
    "E47房屋建筑业": "建筑装饰", "E48土木工程建筑业": "建筑装饰",
    "E49建筑安装业": "建筑装饰", "E50建筑装饰和其他建筑业": "建筑装饰",
    # 煤炭
    "B06煤炭开采和洗选业": "煤炭",
    # 石油石化
    "B07石油和天然气开采业": "石油石化",
    "B08黑色金属矿采选业": "钢铁", "B09有色金属矿采选业": "有色金属",
    "B10非金属矿采选业": "建筑材料", "B11开采辅助活动": "石油石化",
    # 综合
    "S90综合": "综合",
    # 其他制造
    "C41其他制造业": "轻工制造",
    "C42废弃资源综合利用业": "环保",
    "C43金属制品、机械和设备修理业": "机械设备",
    # 美容护理
    "C16烟草制品业": "食品饮料",
}


def _map_gb_to_sw(gb_sector):
    """将国标行业分类映射到申万一级行业"""
    if not gb_sector or gb_sector == "其他":
        return "其他"

    # 精确匹配
    if gb_sector in _GB_TO_SW:
        return _GB_TO_SW[gb_sector]

    # 前缀匹配（baostock 返回的可能带或不带编号）
    for gb_key, sw_name in _GB_TO_SW.items():
        if gb_sector in gb_key or gb_key in gb_sector:
            return sw_name

    return "其他"


def get_stock_sector(code, name=""):
    """获取个股申万一级行业（带缓存）

    A股 → baostock industry → 映射到申万, 港股 → "境外", ETF → 名称推断
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
        sector = data.get("sector", "其他")
        # 如果缓存的是国标行业名，重新映射
        if any(c.isdigit() for c in sector[:3]):
            sector = _map_gb_to_sw(sector)
            data["sector"] = sector
            cache_file.write_text(json.dumps(data, ensure_ascii=False))
        return sector

    # baostock 获取行业（返回国标分类）
    try:
        _ensure_bs_login()
        rs = bs.query_stock_industry()
        gb_sector = "其他"
        while (rs.error_code == '0') and rs.next():
            row = rs.get_row_data()
            # row: [updateDate, code, code_name, industry, industryClassification]
            if len(row) >= 4 and row[1].endswith(code_str):
                gb_sector = row[3] if row[3] else "其他"
                break

        sector = _map_gb_to_sw(gb_sector)
        cache_file.write_text(json.dumps({"sector": sector, "code": code_str, "gb_sector": gb_sector}, ensure_ascii=False))
        return sector
    except Exception as e:
        print(f"  警告: 获取 {code_str} 行业失败: {e}")
        return "其他"


# ============================================================
# 公开 API：申万行业指数收益率
# ============================================================

# 申万2021版一级行业 → Tushare ts_code
_SW_L1_TUSHARE = {
    "农林牧渔": "801010.SI", "基础化工": "801030.SI", "钢铁": "801040.SI",
    "有色金属": "801050.SI", "电子": "801080.SI", "家用电器": "801110.SI",
    "食品饮料": "801120.SI", "纺织服饰": "801130.SI", "轻工制造": "801140.SI",
    "医药生物": "801150.SI", "公用事业": "801160.SI", "交通运输": "801170.SI",
    "房地产": "801180.SI", "商贸零售": "801200.SI", "社会服务": "801210.SI",
    "综合": "801230.SI", "建筑材料": "801710.SI", "建筑装饰": "801720.SI",
    "电力设备": "801730.SI", "国防军工": "801740.SI", "计算机": "801750.SI",
    "传媒": "801760.SI", "通信": "801770.SI", "银行": "801780.SI",
    "非银金融": "801790.SI", "汽车": "801880.SI", "机械设备": "801890.SI",
    "煤炭": "801950.SI", "石油石化": "801960.SI", "环保": "801970.SI",
    "美容护理": "801980.SI",
}

# 申万一级行业 → 东方财富板块代码（fallback）
_SW_SECTOR_CODES_EM = {
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


def _get_sw_sector_returns_tushare(start_date, end_date):
    """Tushare sw_daily 获取申万一级行业收益率（主源）

    用 trade_date 批量拉取，只需 2 次 API 调用（起始日 + 结束日）
    用 close 自算收益率，不依赖接口 pct_change
    """
    import tushare as ts

    if not TS_TOKEN:
        raise ValueError("TS_TOKEN 未配置")

    ts.set_token(TS_TOKEN)
    pro = ts.pro_api()

    start_fmt = start_date.replace("-", "")
    end_fmt = end_date.replace("-", "")

    # 拉取起始日和结束日附近的全量行业数据
    # sw_daily(trade_date=xxx) 返回当天所有申万行业（含一二三级）
    df_start = pro.sw_daily(trade_date=start_fmt)
    df_end = pro.sw_daily(trade_date=end_fmt)

    # 如果精确日期没数据（非交易日），向前/后搜索最近交易日
    if df_start is None or df_start.empty:
        # 向后找 5 天
        from datetime import datetime, timedelta
        dt = datetime.strptime(start_fmt, '%Y%m%d')
        for i in range(1, 6):
            d = (dt + timedelta(days=i)).strftime('%Y%m%d')
            df_start = pro.sw_daily(trade_date=d)
            if df_start is not None and not df_start.empty:
                break

    if df_end is None or df_end.empty:
        # 向前找 5 天
        from datetime import datetime, timedelta
        dt = datetime.strptime(end_fmt, '%Y%m%d')
        for i in range(1, 6):
            d = (dt - timedelta(days=i)).strftime('%Y%m%d')
            df_end = pro.sw_daily(trade_date=d)
            if df_end is not None and not df_end.empty:
                break

    if df_start is None or df_start.empty or df_end is None or df_end.empty:
        raise ValueError("无法获取起始/结束日的申万行业数据")

    # 筛选一级行业：ts_code 在 _SW_L1_TUSHARE 中
    l1_codes = set(_SW_L1_TUSHARE.values())
    # 反向映射 ts_code → sector_name
    code_to_name = {v: k for k, v in _SW_L1_TUSHARE.items()}

    start_map = {}  # ts_code → close
    for _, row in df_start.iterrows():
        if row['ts_code'] in l1_codes:
            start_map[row['ts_code']] = float(row['close'])

    result = {}
    for _, row in df_end.iterrows():
        ts_code = row['ts_code']
        if ts_code in l1_codes and ts_code in start_map:
            sector_name = code_to_name[ts_code]
            end_close = float(row['close'])
            start_close = start_map[ts_code]
            ret = (end_close - start_close) / start_close
            result[sector_name] = {"return": ret, "weight": 0}

    return result


def _get_sw_sector_returns_eastmoney(start_date, end_date):
    """东方财富 HTTP 获取申万一级行业收益率（fallback）"""
    result = {}
    start_fmt = start_date.replace("-", "")
    end_fmt = end_date.replace("-", "")

    for sector_name, em_code in _SW_SECTOR_CODES_EM.items():
        try:
            url = (
                "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                f"?secid=90.{em_code}"
                "&fields1=f1,f2,f3,f4,f5,f6"
                "&fields2=f51,f52,f53,f54,f55,f56"
                "&klt=101"
                f"&beg={start_fmt}"
                f"&end={end_fmt}"
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


def get_sw_sector_returns(start_date, end_date):
    """获取申万一级行业指数收益率

    数据源优先级: Tushare sw_daily → 东方财富 HTTP
    返回 {sector_name: {"return": float, "weight": 0}}
    """
    # 尝试 Tushare
    try:
        result = _get_sw_sector_returns_tushare(start_date, end_date)
        if len(result) >= 20:  # 至少拿到 20 个行业才算成功
            print(f"  申万行业数据: Tushare ({len(result)} 个行业)")
            return result
        print(f"  Tushare 仅返回 {len(result)} 个行业，尝试东方财富...")
    except Exception as e:
        print(f"  Tushare 获取申万行业失败: {e}，尝试东方财富...")

    # fallback 东方财富
    result = _get_sw_sector_returns_eastmoney(start_date, end_date)
    if result:
        print(f"  申万行业数据: 东方财富 ({len(result)} 个行业)")
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
