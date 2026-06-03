"""构造回测输入的辅助函数"""

import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).parent.parent.parent))

from shared.portfolio_config import load_portfolio_from_toml, load_account_config
from shared.data_provider import get_stock_prices, get_benchmark_prices
from risk_control.config import ATR_PERIOD


def portfolio_from_toml(toml_path=None):
    """从 portfolio.toml 加载当前持仓作为回测输入

    Returns:
        tuple[pd.DataFrame, float]: (portfolio_df, total_equity)
    """
    portfolio_df = load_portfolio_from_toml(toml_path)
    account = load_account_config(toml_path)
    total_equity = account.get("total_equity", 500000)
    return portfolio_df, total_equity


def single_stock_scenario(code, name, quantity, cost_price, buy_date=None):
    """创建单只股票的 what-if 场景

    Args:
        code: 股票代码
        name: 股票名称
        quantity: 持仓数量
        cost_price: 成本价
        buy_date: 买入日期 YYYYMMDD（可选）

    Returns:
        pd.DataFrame: 单行 portfolio_df
    """
    row = {
        "code": code,
        "name": name,
        "market": "上海" if code.startswith("6") else "深圳",
        "quantity": quantity,
        "cost_price": cost_price,
        "trade_plan": {"status": "active", "stop_loss_strategy": "atr"},
        "risk_rules": {},
    }
    if buy_date:
        row["buy_date"] = buy_date
    return pd.DataFrame([row])


def fetch_backtest_prices(codes, start_date, end_date, warmup_days=None):
    """获取回测所需的历史价格数据

    优先读本地 cache，缺失部分通过 baostock 补充。
    自动添加预热期数据（用于 ATR 计算）。

    Args:
        codes: 股票代码列表
        start_date: 回测起始日 YYYYMMDD
        end_date: 回测结束日 YYYYMMDD
        warmup_days: 预热天数，默认 ATR_PERIOD * 3

    Returns:
        dict: {code: DataFrame[date, open, high, low, close, volume]}
    """
    if warmup_days is None:
        warmup_days = ATR_PERIOD * 3

    # 计算含预热期的起始日
    start = pd.Timestamp(start_date)
    warmup_start = start - pd.Timedelta(days=warmup_days + 10)  # 多留余量应对非交易日
    warmup_start_str = warmup_start.strftime("%Y%m%d")

    prices_dict = {}
    for code in codes:
        try:
            df = get_stock_prices(code, warmup_start_str, end_date)
            if df is not None and not df.empty:
                prices_dict[code] = df
        except Exception as e:
            print(f"  警告: 获取 {code} 行情失败: {e}")

    return prices_dict


def fetch_market_index(start_date, end_date, index_code="000001"):
    """获取市场指数数据（用于波动率计算）"""
    warmup_days = ATR_PERIOD * 3
    start = pd.Timestamp(start_date)
    warmup_start = start - pd.Timedelta(days=warmup_days + 10)
    warmup_start_str = warmup_start.strftime("%Y%m%d")

    try:
        df = get_benchmark_prices(index_code, warmup_start_str, end_date)
        if df is not None and not df.empty:
            return df
    except Exception as e:
        print(f"  警告: 获取指数 {index_code} 失败: {e}")
    return pd.DataFrame()
