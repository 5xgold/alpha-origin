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

sys.path.append(str(Path(__file__).parent.parent))
from config import CACHE_DIR, SECTOR_CACHE_DAYS, BENCHMARK_INDEX, parse_benchmark_config
from shared.data_provider import get_stock_sector, get_sw_sector_returns, get_index_constituents


def classify_portfolio_sectors(snapshots, start_date, end_date, stock_prices_cache=None):
    """分类组合持仓的行业，计算行业权重和收益

    基于期初持仓计算行业权重，用市场价格计算期间收益率。
    对于期间卖出的股票，仍用其价格变动计算收益率。

    Args:
        snapshots: {date: {code: {quantity, avg_cost, name, ...}, 'cash': float}}
        start_date: 分析开始日期
        end_date: 分析结束日期
        stock_prices_cache: 股票价格缓存 {code: Series(date→close)}

    Returns:
        dict: {sector: {weight: float, return: float, codes: [str]}}
    """
    sorted_dates = sorted(snapshots.keys())
    end_dt = pd.to_datetime(end_date)
    start_dt = pd.to_datetime(start_date)

    # 找到分析区间内第一个有持仓的日期
    start_snap_date = None
    for d in sorted_dates:
        if pd.to_datetime(d) >= start_dt:
            start_snap_date = d
            break

    if start_snap_date is None:
        return {}

    start_snap = snapshots[start_snap_date]

    # 基于期初持仓计算行业权重和收益
    sector_data = {}  # {sector: {codes: [], start_value: float, end_value: float}}
    total_start_value = 0

    for code in start_snap:
        if code == 'cash' or code.startswith('_'):
            continue
        if not isinstance(start_snap[code], dict):
            continue

        qty = start_snap[code].get('quantity', 0)
        if qty <= 0:
            continue

        name = start_snap[code].get('name', '')
        sector = get_stock_sector(code, name)

        if sector not in sector_data:
            sector_data[sector] = {"codes": [], "start_value": 0, "end_value": 0}

        if code not in sector_data[sector]["codes"]:
            sector_data[sector]["codes"].append(code)

        # 期初市值：用市场价
        start_price = None
        if stock_prices_cache and code in stock_prices_cache:
            prices = stock_prices_cache[code]
            start_snap_dt = pd.to_datetime(start_snap_date)
            valid = prices[prices.index <= start_snap_dt]
            if not valid.empty:
                start_price = float(valid.iloc[-1])
        if start_price is None:
            start_price = start_snap[code].get('avg_cost', 0)

        sv = qty * start_price
        sector_data[sector]["start_value"] += sv
        total_start_value += sv

        # 期末市值：用期末市场价 × 期初数量（衡量价格变动，不受交易影响）
        end_price = None
        if stock_prices_cache and code in stock_prices_cache:
            prices = stock_prices_cache[code]
            end_snap_dt = pd.to_datetime(end_date)
            valid = prices[prices.index <= end_snap_dt]
            if not valid.empty:
                end_price = float(valid.iloc[-1])
        if end_price is None:
            end_price = start_price  # 无数据则假设不变

        ev = qty * end_price
        sector_data[sector]["end_value"] += ev

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


def get_benchmark_sector_data(benchmark_index, start_date, end_date, benchmark_config=None):
    """获取基准的行业权重和收益

    支持单一基准和复合基准。复合基准时，A股成分走申万行业，
    港股成分整体作为「境外」行业。

    Args:
        benchmark_index: 基准指数代码（单一基准时使用）
        start_date: 开始日期 (str YYYYMMDD)
        end_date: 结束日期 (str YYYYMMDD)
        benchmark_config: parse_benchmark_config() 返回的列表（复合基准时传入）

    Returns:
        dict: {sector: {weight: float, return: float}}
    """
    # 复合基准分发
    if benchmark_config and len(benchmark_config) > 1:
        return _get_composite_benchmark_sector_data(benchmark_config, start_date, end_date)

    # 单一基准：走现有逻辑
    cache_file = Path(CACHE_DIR) / f"benchmark_sectors_{benchmark_index}_{start_date}_{end_date}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        cache_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - cache_time < timedelta(days=SECTOR_CACHE_DAYS):
            return json.loads(cache_file.read_text())

    # 获取申万一级行业指数列表和收益
    sector_result = {}
    try:
        sector_result = get_sw_sector_returns(start_date, end_date)
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


def _get_composite_benchmark_sector_data(benchmark_config, start_date, end_date):
    """获取复合基准的行业权重和收益

    A股成分（如 000300）：申万行业权重 × 该成分权重
    港股成分（如 HK.800000）：整体作为「境外」行业，权重 = 该成分权重，收益 = 指数区间收益

    Args:
        benchmark_config: parse_benchmark_config() 返回的列表
        start_date: 开始日期 (YYYYMMDD)
        end_date: 结束日期 (YYYYMMDD)

    Returns:
        dict: {sector: {weight: float, return: float}}
    """
    from scripts.data_provider import _fetch_hk_index_futu

    merged = {}

    def _merge_sector_return(sector_name, added_weight, added_return):
        if sector_name in merged:
            prev_weight = merged[sector_name]["weight"]
            new_weight = prev_weight + added_weight
            if new_weight > 0:
                merged[sector_name]["return"] = (
                    merged[sector_name]["return"] * prev_weight + added_return * added_weight
                ) / new_weight
            merged[sector_name]["weight"] = new_weight
        else:
            merged[sector_name] = {"weight": added_weight, "return": added_return}

    for comp in benchmark_config:
        idx = comp["index"]
        weight = comp["weight"]
        source = comp["source"]

        if source == "futu":
            # 港股指数：整体作为「境外」行业
            df = _fetch_hk_index_futu(idx, start_date, end_date)
            if df is not None and not df.empty:
                close = df['close'].astype(float)
                hk_return = (close.iloc[-1] / close.iloc[0]) - 1
            else:
                hk_return = 0.0
                print(f"  警告: 无法获取 {idx} 数据，境外收益设为 0")

            _merge_sector_return("境外", weight, hk_return)
        else:
            # A股指数：获取申万行业数据，按权重缩放
            a_sectors = get_benchmark_sector_data(idx, start_date, end_date)
            for sector, data in a_sectors.items():
                _merge_sector_return(sector, data["weight"] * weight, data["return"])

    # 归一化权重
    total_w = sum(v["weight"] for v in merged.values())
    if total_w > 0:
        for sector in merged:
            merged[sector]["weight"] /= total_w

    return merged


def _get_benchmark_sector_weights(benchmark_index):
    """获取基准指数的行业权重分布（通过成分股行业聚合）"""
    cache_file = Path(CACHE_DIR) / f"benchmark_weights_{benchmark_index}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        cache_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
        if datetime.now() - cache_time < timedelta(days=SECTOR_CACHE_DAYS):
            return json.loads(cache_file.read_text())

    sector_weights = {}
    try:
        codes = get_index_constituents(benchmark_index)
        if codes:
            total = len(codes)
            for code in codes:
                sector = get_stock_sector(code)
                sector_weights[sector] = sector_weights.get(sector, 0) + 1

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


def brinson_analysis(snapshots, portfolio_values, benchmark_prices, start_date, end_date, stock_prices_cache=None, benchmark_config=None):
    """Brinson 归因分析主入口

    Args:
        snapshots: 每日持仓快照
        portfolio_values: 组合市值 DataFrame
        benchmark_prices: 基准价格 DataFrame
        start_date: 开始日期
        end_date: 结束日期
        stock_prices_cache: 股票价格缓存 {code: Series(date→close)}
        benchmark_config: parse_benchmark_config() 返回的列表（复合基准时传入）

    Returns:
        dict: Brinson 归因结果，包含 details 和 totals
    """
    print("正在进行 Brinson 归因分析...")

    start_str = pd.to_datetime(start_date).strftime('%Y%m%d')
    end_str = pd.to_datetime(end_date).strftime('%Y%m%d')

    # 将 list 格式的 snapshots 转为 dict 格式 {date: {code: {...}, 'cash': float}}
    if isinstance(snapshots, list):
        snap_dict = {}
        for snap in snapshots:
            d = snap['date']
            entry = {}
            for code, info in snap['positions'].items():
                qty = info.get('quantity', 0)
                cost = info.get('cost_basis', 0)
                avg_cost = cost / qty if qty > 0 else 0
                entry[code] = {'quantity': qty, 'avg_cost': avg_cost, 'name': info.get('name', '')}
            entry['cash'] = snap.get('cash', 0)
            snap_dict[d] = entry
        snapshots = snap_dict

    # 1. 分类组合持仓行业
    print("  分类组合持仓行业...")
    portfolio_sectors = classify_portfolio_sectors(snapshots, start_date, end_date, stock_prices_cache)

    if not portfolio_sectors:
        print("  警告: 无法获取组合行业分类，跳过 Brinson 归因")
        return None

    # 2. 获取基准行业数据
    print("  获取基准行业数据...")
    benchmark_sectors = get_benchmark_sector_data(BENCHMARK_INDEX, start_str, end_str, benchmark_config=benchmark_config)

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
        residual = excess - brinson_total
        diff = abs(residual)

        result["excess_return"] = excess
        result["residual_effect"] = residual
        result["verification_diff"] = diff

        if diff > 0.01:
            print(f"  注意: 股票行业归因 ({brinson_total:+.2%}) 与组合超额收益 ({excess:+.2%}) 存在残差 {residual:+.2%}")
            print("  （残差通常来自：现金仓位、期间交易、申购赎回和行业映射近似）")

    return result
