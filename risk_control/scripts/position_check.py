"""第一道防线：仓位管理检查"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from shared.data_provider import get_stock_sector
from risk_control.config import (
    FAMILIARITY_DIMENSIONS,
    FAMILIARITY_POSITION_TIERS,
    get_familiarity_level,
    MAX_SINGLE_SECTOR_WEIGHT,
    VOLATILITY_POSITION_BANDS,
)


def _get_suggested_position(market_vol):
    """根据市场波动率查表得到建议仓位上限"""
    for vol_cap, max_pos in VOLATILITY_POSITION_BANDS:
        if market_vol <= vol_cap:
            return max_pos
    return VOLATILITY_POSITION_BANDS[-1][1]


def check_positions(portfolio_df, total_equity, market_vol, market_index_name="沪深300"):
    """仓位管理检查

    Args:
        portfolio_df: DataFrame[code, name, market, quantity, cost_price, current_price, market_value]
        total_equity: 总权益（含现金）
        market_vol: 市场指数年化波动率（%）
        market_index_name: 市场指数名称（用于报告显示）

    Returns:
        dict: {
            stock_violations: [{code, name, weight, limit, familiarity_level}],
            stock_familiarity: [{name, detail, level, limit}],
            sector_violations: [{sector, weight, limit, codes}],
            suggested_position: float,
            current_position: float,
            market_vol: float,
            market_index_name: str,
            position_warning: bool,
        }
    """
    result = {
        "stock_violations": [],
        "stock_familiarity": [],  # 每只股票的熟悉程度评估结果
        "sector_violations": [],
        "suggested_position": _get_suggested_position(market_vol),
        "current_position": 0.0,
        "market_vol": market_vol,
        "market_index_name": market_index_name,
        "position_warning": False,
    }

    if total_equity <= 0:
        return result

    total_market_value = portfolio_df["market_value"].sum()
    result["current_position"] = total_market_value / total_equity

    # 个股仓位检查（基于熟悉程度评估）
    for _, row in portfolio_df.iterrows():
        weight = row["market_value"] / total_equity
        fam_detail = row.get("familiarity_detail", {})
        if not isinstance(fam_detail, dict):
            fam_detail = {}
        true_count = sum(1 for d in FAMILIARITY_DIMENSIONS if fam_detail.get(d, False))
        fam_level = get_familiarity_level(true_count)
        limit = FAMILIARITY_POSITION_TIERS[fam_level]

        # 记录每只股票的熟悉程度（供报告概览表使用）
        result["stock_familiarity"].append({
            "name": row["name"],
            "detail": fam_detail,
            "level": fam_level,
            "limit": limit,
        })

        if weight > limit:
            result["stock_violations"].append({
                "code": row["code"],
                "name": row["name"],
                "weight": weight,
                "actual_pct": weight,
                "limit": limit,
                "familiarity_level": fam_level,
            })

    # 行业仓位检查
    sector_map = {}  # {sector: {value, codes}}
    for _, row in portfolio_df.iterrows():
        sector = get_stock_sector(str(row["code"]), str(row["name"]))
        if sector not in sector_map:
            sector_map[sector] = {"value": 0.0, "codes": []}
        sector_map[sector]["value"] += row["market_value"]
        sector_map[sector]["codes"].append(row["name"])

    for sector, info in sector_map.items():
        weight = info["value"] / total_equity
        if weight > MAX_SINGLE_SECTOR_WEIGHT:
            result["sector_violations"].append({
                "sector": sector,
                "weight": weight,
                "actual_pct": weight,
                "limit": MAX_SINGLE_SECTOR_WEIGHT,
                "codes": info["codes"],
            })

    # 总仓位 vs 建议仓位
    result["position_warning"] = result["current_position"] > result["suggested_position"]

    return result
