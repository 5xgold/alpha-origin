"""Brinson 归因分析模块（BHB 模型）

将超额收益拆解为：
- 资产配置效应（Allocation）：行业轮动能力
- 个股选择效应（Selection）：行业内选股能力
- 交互效应（Interaction）：配置与选股的联合效应
"""

import sys
import json
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import akshare as ak

sys.path.append(str(Path(__file__).parent.parent))
from config import CACHE_DIR, SECTOR_CACHE_DAYS, BENCHMARK_INDEX

# ETF 行业映射（名称关键词 → 申万一级行业）
ETF_SECTOR_MAP = {
    # 行业ETF
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

# 宽基ETF关键词
BROAD_ETF_KEYWORDS = [
    "沪深300", "中证500", "中证1000", "上证50", "创业板", "科创",
    "红利", "价值", "成长", "MSCI", "恒生", "纳斯达克", "标普",
]


def get_stock_sector(code, name=""):
    """获取个股申万一级行业（带缓存）

    Args:
        code: 股票代码
        name: 股票名称（用于 ETF 行业推断）

    Returns:
        行业名称字符串
    """
    code_str = str(code).strip()
    is_hk = len(code_str) == 5 and code_str.isdigit()

    # 港股 → 境外
    if is_hk:
        return "境外"

    code_str = code_str.zfill(6)

    # ETF 判断（代码以 51/15/16/56/58/59 开头）
    etf_prefixes = ("51", "15", "16", "56", "58", "59")
    if code_str[:2] in etf_prefixes:
        return _classify_etf(name)

    # 查缓存
    cache_file = Path(CACHE_DIR) / "sectors" / f"{code_str}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        cache_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - cache_time < timedelta(days=SECTOR_CACHE_DAYS):
            data = json.loads(cache_file.read_text())
            return data.get("sector", "其他")

    # 调用 akshare 获取行业
    try:
        info = ak.stock_individual_info_em(symbol=code_str)
        # info 是 DataFrame，有 item 和 value 两列
        sector = "其他"
        for _, row in info.iterrows():
            if "行业" in str(row.get("item", "")):
                sector = str(row["value"])
                break

        cache_file.write_text(json.dumps({"sector": sector, "code": code_str}, ensure_ascii=False))
        return sector
    except Exception as e:
        print(f"  警告: 获取 {code_str} 行业失败: {e}")
        return "其他"


def _classify_etf(name):
    """根据 ETF 名称推断行业"""
    if not name:
        return "指数"

    # 先检查是否宽基
    for kw in BROAD_ETF_KEYWORDS:
        if kw in name:
            return "指数"

    # 行业 ETF 匹配
    for kw, sector in ETF_SECTOR_MAP.items():
        if kw in name:
            return sector

    return "指数"


def classify_portfolio_sectors(snapshots, start_date, end_date, stock_prices_cache=None):
    """分类组合持仓的行业，计算行业权重和收益

    Args:
        snapshots: {date: {code: {quantity, avg_cost, name, ...}, '_cash': float}}
        start_date: 分析开始日期
        end_date: 分析结束日期
        stock_prices_cache: 可选的股票价格缓存 {code: Series(date→close)}

    Returns:
        dict: {sector: {weight: float, return: float, codes: [str]}}
    """
    # 取结束日的持仓快照（或最近的一个交易日）
    sorted_dates = sorted(snapshots.keys())
    end_dt = pd.to_datetime(end_date)
    start_dt = pd.to_datetime(start_date)

    # 找到分析区间内最后一个有持仓的日期
    snap_date = None
    for d in reversed(sorted_dates):
        if pd.to_datetime(d) <= end_dt:
            snap_date = d
            break

    if snap_date is None:
        return {}

    # 找到分析区间内第一个有持仓的日期
    start_snap_date = None
    for d in sorted_dates:
        if pd.to_datetime(d) >= start_dt:
            start_snap_date = d
            break

    if start_snap_date is None:
        return {}

    end_snap = snapshots[snap_date]
    start_snap = snapshots[start_snap_date]

    # 收集所有持仓股票的行业
    sector_data = {}  # {sector: {codes: [], start_values: {}, end_values: {}}}

    # 获取每只股票的行业和市值
    total_start_value = 0
    total_end_value = 0

    all_codes = set()
    for code in end_snap:
        if code == 'cash' or code.startswith('_'):
            continue
        all_codes.add(code)
    for code in start_snap:
        if code == 'cash' or code.startswith('_'):
            continue
        all_codes.add(code)

    for code in all_codes:
        name = ""
        if code in end_snap and isinstance(end_snap[code], dict):
            name = end_snap[code].get('name', '')
        elif code in start_snap and isinstance(start_snap[code], dict):
            name = start_snap[code].get('name', '')

        sector = get_stock_sector(code, name)

        if sector not in sector_data:
            sector_data[sector] = {"codes": [], "start_value": 0, "end_value": 0}

        if code not in sector_data[sector]["codes"]:
            sector_data[sector]["codes"].append(code)

        # 计算期初市值
        if code in start_snap and isinstance(start_snap[code], dict):
            qty = start_snap[code].get('quantity', 0)
            cost = start_snap[code].get('avg_cost', 0)
            sv = qty * cost
            sector_data[sector]["start_value"] += sv
            total_start_value += sv

        # 计算期末市值（用实际价格）
        if code in end_snap and isinstance(end_snap[code], dict):
            qty = end_snap[code].get('quantity', 0)
            # 尝试从价格缓存获取期末价格
            end_price = None
            if stock_prices_cache and code in stock_prices_cache:
                prices = stock_prices_cache[code]
                if snap_date in prices.index:
                    end_price = prices.loc[snap_date]
            if end_price is None:
                end_price = end_snap[code].get('avg_cost', 0)
            ev = qty * end_price
            sector_data[sector]["end_value"] += ev
            total_end_value += ev

    if total_start_value == 0:
        return {}

    # 计算各行业权重和收益
    result = {}
    for sector, data in sector_data.items():
        weight = data["start_value"] / total_start_value if total_start_value > 0 else 0
        if data["start_value"] > 0:
            ret = (data["end_value"] - data["start_value"]) / data["start_value"]
        else:
            ret = 0
        result[sector] = {
            "weight": weight,
            "return": ret,
            "codes": data["codes"],
        }

    return result


def get_benchmark_sector_data(benchmark_index, start_date, end_date):
    """获取基准的行业权重和收益

    使用申万一级行业指数收益率作为各行业基准收益。
    基准行业权重从成分股按行业聚合。

    Args:
        benchmark_index: 基准指数代码
        start_date: 开始日期 (str YYYYMMDD)
        end_date: 结束日期 (str YYYYMMDD)

    Returns:
        dict: {sector: {weight: float, return: float}}
    """
    cache_file = Path(CACHE_DIR) / f"benchmark_sectors_{benchmark_index}_{start_date}_{end_date}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        cache_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - cache_time < timedelta(days=SECTOR_CACHE_DAYS):
            return json.loads(cache_file.read_text())

    # 获取申万一级行业指数列表和收益
    sector_result = {}
    try:
        sector_result = _get_sw_sector_returns(start_date, end_date)
    except Exception as e:
        print(f"  警告: 获取申万行业指数失败: {e}")

    # 获取基准成分股行业权重
    sector_weights = _get_benchmark_sector_weights(benchmark_index)

    if not sector_weights:
        # fallback: 等权分配
        if sector_result:
            equal_weight = 1.0 / len(sector_result)
            for sector in sector_result:
                sector_result[sector]["weight"] = equal_weight
        cache_file.write_text(json.dumps(sector_result, ensure_ascii=False))
        return sector_result

    # 合并权重和收益
    all_sectors = set(list(sector_weights.keys()) + list(sector_result.keys()))
    merged = {}
    for sector in all_sectors:
        merged[sector] = {
            "weight": sector_weights.get(sector, 0),
            "return": sector_result.get(sector, {}).get("return", 0),
        }

    # 归一化权重
    total_w = sum(v["weight"] for v in merged.values())
    if total_w > 0:
        for sector in merged:
            merged[sector]["weight"] /= total_w

    cache_file.write_text(json.dumps(merged, ensure_ascii=False))
    return merged


def _get_sw_sector_returns(start_date, end_date):
    """获取申万一级行业指数收益率"""
    result = {}
    try:
        # 尝试获取中证全指成分股行业权重（可能不可用）
        ak.index_stock_cons_weight_csindex(symbol="000985")
    except Exception:
        pass

    try:
        # 使用申万行业指数日线数据
        sw_index = ak.sw_index_first_info()

        for _, row in sw_index.iterrows():
            sector_name = str(row.get("行业名称", ""))
            sector_code = str(row.get("行业代码", ""))
            if not sector_code or not sector_name:
                continue

            try:
                hist = ak.sw_index_daily(symbol=sector_code, start_date=start_date, end_date=end_date)
                if hist is not None and len(hist) >= 2:
                    # 计算区间收益率
                    first_close = float(hist.iloc[0]["收盘"])
                    last_close = float(hist.iloc[-1]["收盘"])
                    ret = (last_close - first_close) / first_close
                    result[sector_name] = {"return": ret, "weight": 0}
            except Exception:
                continue

        return result
    except Exception as e:
        print(f"  警告: 获取申万行业指数列表失败: {e}")
        return result


def _get_benchmark_sector_weights(benchmark_index):
    """获取基准指数的行业权重分布"""
    cache_file = Path(CACHE_DIR) / f"benchmark_weights_{benchmark_index}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        cache_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - cache_time < timedelta(days=SECTOR_CACHE_DAYS):
            return json.loads(cache_file.read_text())

    sector_weights = {}
    try:
        # 尝试获取指数成分股
        cons = ak.index_stock_cons(symbol=benchmark_index)
        if cons is not None and not cons.empty:
            code_col = None
            for col in cons.columns:
                if '代码' in str(col) or 'code' in str(col).lower():
                    code_col = col
                    break
            if code_col is None:
                code_col = cons.columns[0]

            # 对每个成分股查行业，按行业聚合（等权近似）
            total = len(cons)
            for _, row in cons.iterrows():
                code = str(row[code_col]).zfill(6)
                sector = get_stock_sector(code)
                sector_weights[sector] = sector_weights.get(sector, 0) + 1

            # 转为权重比例
            for sector in sector_weights:
                sector_weights[sector] = sector_weights[sector] / total

            cache_file.write_text(json.dumps(sector_weights, ensure_ascii=False))
            return sector_weights
    except Exception as e:
        print(f"  警告: 获取基准成分股失败: {e}，将使用等权近似")

    return sector_weights


def brinson_attribution(portfolio_sectors, benchmark_sectors):
    """BHB 归因计算

    对每个行业 i:
      allocation_i  = (w_p,i - w_b,i) × (R_b,i - R_b)
      selection_i   = w_b,i × (R_p,i - R_b,i)
      interaction_i = (w_p,i - w_b,i) × (R_p,i - R_b,i)

    Args:
        portfolio_sectors: {sector: {weight, return, codes}}
        benchmark_sectors: {sector: {weight, return}}

    Returns:
        dict with per-sector and total attribution
    """
    # 基准总收益 R_b = Σ w_b,i × R_b,i
    r_b = sum(
        s.get("weight", 0) * s.get("return", 0)
        for s in benchmark_sectors.values()
    )

    all_sectors = sorted(set(list(portfolio_sectors.keys()) + list(benchmark_sectors.keys())))

    details = []
    total_allocation = 0
    total_selection = 0
    total_interaction = 0

    for sector in all_sectors:
        w_p = portfolio_sectors.get(sector, {}).get("weight", 0)
        w_b = benchmark_sectors.get(sector, {}).get("weight", 0)
        r_p = portfolio_sectors.get(sector, {}).get("return", 0)
        r_b_i = benchmark_sectors.get(sector, {}).get("return", 0)

        allocation = (w_p - w_b) * (r_b_i - r_b)
        selection = w_b * (r_p - r_b_i)
        interaction = (w_p - w_b) * (r_p - r_b_i)

        total_allocation += allocation
        total_selection += selection
        total_interaction += interaction

        # 只记录有实际持仓或基准权重的行业
        if w_p > 0.001 or w_b > 0.001:
            details.append({
                "sector": sector,
                "portfolio_weight": w_p,
                "benchmark_weight": w_b,
                "portfolio_return": r_p,
                "benchmark_return": r_b_i,
                "allocation": allocation,
                "selection": selection,
                "interaction": interaction,
            })

    # 按组合权重降序排列
    details.sort(key=lambda x: x["portfolio_weight"], reverse=True)

    return {
        "details": details,
        "total_allocation": total_allocation,
        "total_selection": total_selection,
        "total_interaction": total_interaction,
        "total_active": total_allocation + total_selection + total_interaction,
        "benchmark_return": r_b,
    }


def brinson_analysis(snapshots, portfolio_values, benchmark_prices, start_date, end_date):
    """Brinson 归因分析主入口

    Args:
        snapshots: 每日持仓快照
        portfolio_values: 组合市值 DataFrame
        benchmark_prices: 基准价格 DataFrame
        start_date: 开始日期
        end_date: 结束日期

    Returns:
        dict: Brinson 归因结果，包含 details 和 totals
    """
    print("正在进行 Brinson 归因分析...")

    start_str = pd.to_datetime(start_date).strftime('%Y%m%d')
    end_str = pd.to_datetime(end_date).strftime('%Y%m%d')

    # 1. 分类组合持仓行业
    print("  分类组合持仓行业...")
    portfolio_sectors = classify_portfolio_sectors(snapshots, start_date, end_date)

    if not portfolio_sectors:
        print("  警告: 无法获取组合行业分类，跳过 Brinson 归因")
        return None

    # 2. 获取基准行业数据
    print("  获取基准行业数据...")
    benchmark_sectors = get_benchmark_sector_data(BENCHMARK_INDEX, start_str, end_str)

    if not benchmark_sectors:
        print("  警告: 无法获取基准行业数据，跳过 Brinson 归因")
        return None

    # 3. BHB 归因计算
    print("  计算 BHB 归因...")
    result = brinson_attribution(portfolio_sectors, benchmark_sectors)

    # 4. 校验：三效应之和 ≈ 超额收益
    if portfolio_values is not None and len(portfolio_values) >= 2:
        pv = portfolio_values['value'].astype(float)
        total_port_return = (pv.iloc[-1] / pv.iloc[0]) - 1

        bv = benchmark_prices['close'].astype(float)
        total_bench_return = (bv.iloc[-1] / bv.iloc[0]) - 1

        excess = total_port_return - total_bench_return
        brinson_total = result["total_active"]
        diff = abs(excess - brinson_total)

        result["excess_return"] = excess
        result["verification_diff"] = diff

        if diff > 0.05:
            print(f"  注意: Brinson 归因合计 ({brinson_total:+.2%}) 与超额收益 ({excess:+.2%}) 差异较大 ({diff:.2%})")
            print(f"  （差异可能来自：现金持仓、期间交易、行业数据精度等）")

    return result
