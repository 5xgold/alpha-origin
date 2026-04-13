"""第一道防线：仓位管理检查"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from shared.data_provider import get_stock_sector
from risk_control.config import (
    MAX_SINGLE_STOCK_WEIGHT,
    MAX_SINGLE_SECTOR_WEIGHT,
    VOLATILITY_POSITION_BANDS,
)


def _get_suggested_position(market_vol):
    """根据市场波动率查表得到建议仓位上限"""
    for vol_cap, max_pos in VOLATILITY_POSITION_BANDS:
        if market_vol <= vol_cap:
            return max_pos
    return VOLATILITY_POSITION_BANDS[-1][1]


def check_positions(portfolio_df, total_equity, market_vol):
    """仓位管理检查

    Args:
        portfolio_df: DataFrame[code, name, market, quantity, cost_price, current_price, market_value]
        total_equity: 总权益（含现金）
        market_vol: 沪深300年化波动率（%）

    Returns:
        dict: {
            stock_violations: [{code, name, weight, limit}],
            sector_violations: [{sector, weight, limit, codes}],
            suggested_position: float,
            current_position: float,
            market_vol: float,
            position_warning: bool,
        }
    """
    result = {
        "stock_violations": [],
        "sector_violations": [],
        "suggested_position": _get_suggested_position(market_vol),
        "current_position": 0.0,
        "market_vol": market_vol,
        "position_warning": False,
    }

    if total_equity <= 0:
        return result

    total_market_value = portfolio_df["market_value"].sum()
    result["current_position"] = total_market_value / total_equity

    # 个股仓位检查
    for _, row in portfolio_df.iterrows():
        weight = row["market_value"] / total_equity
        if weight > MAX_SINGLE_STOCK_WEIGHT:
            result["stock_violations"].append({
                "code": row["code"],
                "name": row["name"],
                "weight": weight,
                "limit": MAX_SINGLE_STOCK_WEIGHT,
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
                "limit": MAX_SINGLE_SECTOR_WEIGHT,
                "codes": info["codes"],
            })

    # 总仓位 vs 建议仓位
    result["position_warning"] = result["current_position"] > result["suggested_position"]

    return result
