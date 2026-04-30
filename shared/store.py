"""内部数据访问层

当前实现为本地文件读写，接口不暴露文件路径。
未来数据量增长后，可替换为数据库实现，调用方零改动。
"""

import json
from pathlib import Path
from datetime import datetime

import pandas as pd

_ROOT = Path(__file__).parent.parent

# ── 内部路径（仅本模块使用，不对外暴露）──

_AA_DATA_DIR = _ROOT / "attribution_analysis" / "data"
_RC_DATA_DIR = _ROOT / "risk_control" / "data"
_OUTPUT_DIR = _ROOT / "output"
_PORTFOLIO_TOML = _ROOT / "portfolio.toml"


# ═══════════════════════════════════════════
# 交易记录
# ═══════════════════════════════════════════

def _normalize_trade_quantities(df):
    """根据方向统一数量符号，兼容旧版 trades.csv。"""
    df = df.copy()
    quantities = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    buy_mask = df["direction"] == "买入"
    sell_mask = df["direction"] == "卖出"
    df.loc[buy_mask, "quantity"] = quantities[buy_mask].abs()
    df.loc[sell_mask, "quantity"] = -quantities[sell_mask].abs()
    return df


def get_trades(code=None, start=None, end=None):
    """读取交易记录，可选按代码和日期范围过滤。

    Args:
        code: 股票代码（如 "601216"），None 返回全部
        start: 起始日期 YYYYMMDD，None 不限
        end: 截止日期 YYYYMMDD，None 不限

    Returns:
        DataFrame[date, code, name, direction, quantity, amount, ...]
    """
    trades_file = _AA_DATA_DIR / "trades.csv"
    if not trades_file.exists():
        return pd.DataFrame()
    df = pd.read_csv(trades_file, dtype={"code": str})
    df = df[df["direction"].isin(["买入", "卖出"])]
    df = _normalize_trade_quantities(df)

    if code:
        code = str(code).strip()
        normalized = code.zfill(6) if not (len(code) == 5 and code.isdigit()) else code
        match = df[df["code"] == normalized]
        if match.empty and normalized != code:
            match = df[df["code"] == code]
        df = match

    df["date"] = df["date"].astype(str)
    if start:
        df = df[df["date"] >= str(start)]
    if end:
        df = df[df["date"] <= str(end)]

    return df.copy()


def get_today_trades(date=None):
    """读取指定日期的成交记录。

    Args:
        date: YYYYMMDD 格式，默认今天
    """
    if date is None:
        date = datetime.now().strftime("%Y%m%d")
    return get_trades(start=date, end=date)


# ═══════════════════════════════════════════
# 持仓
# ═══════════════════════════════════════════

def get_portfolio():
    """读取当前持仓。

    Returns:
        DataFrame[code, name, market, quantity, cost_price, familiarity_detail]
    """
    from shared.portfolio_config import load_portfolio_from_toml
    return load_portfolio_from_toml(str(_PORTFOLIO_TOML))


def get_watchlist():
    """读取待买入观察列表。"""
    from shared.portfolio_config import load_watchlist_from_toml
    return load_watchlist_from_toml(str(_PORTFOLIO_TOML))


def get_account():
    """读取账户配置。

    Returns:
        dict: {"total_equity": float, ...}
    """
    from shared.portfolio_config import load_account_config
    return load_account_config(str(_PORTFOLIO_TOML))


# ═══════════════════════════════════════════
# 报告读取
# ═══════════════════════════════════════════

def get_attribution_report():
    """读取最新的归因报告。

    Returns:
        str: 报告 markdown 内容，不存在返回 None
    """
    report_file = _OUTPUT_DIR / "report.md"
    if not report_file.exists():
        return None
    return report_file.read_text(encoding="utf-8")


def get_risk_signals_for(code, name):
    """从最新风控报告中提取指定股票的信号。

    Args:
        code: 股票代码
        name: 股票名称（用于文本匹配）

    Returns:
        str: 相关信号行，无信号返回 "无相关风控信号"
    """
    risk_files = sorted(_OUTPUT_DIR.glob("risk_report_*.md"), reverse=True)
    if not risk_files:
        return "无相关风控信号"
    content = risk_files[0].read_text(encoding="utf-8")
    relevant = [line for line in content.split("\n") if name in line]
    return "\n".join(relevant) if relevant else "无相关风控信号"


# ═══════════════════════════════════════════
# 写入
# ═══════════════════════════════════════════

def save_output(name, ident, prompt_text, context):
    """统一保存脚本输出（prompt + JSON）。

    Args:
        name: 输出类型（如 "daily_review", "trade_review"）
        ident: 标识符（如日期 "20260430" 或 "601216_20260430"）
        prompt_text: 渲染好的完整 prompt
        context: 结构化数据 dict

    Returns:
        dict: {"prompt": Path, "json": Path}
    """
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    prompt_file = _OUTPUT_DIR / f"{name}_{ident}_prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")

    json_file = _OUTPUT_DIR / f"{name}_{ident}.json"
    json_file.write_text(
        json.dumps(context, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )

    return {"prompt": prompt_file, "json": json_file}


def save_risk_snapshot(snapshot_data, path):
    """保存风控快照 JSON。

    Args:
        snapshot_data: 已序列化的 dict
        path: 输出路径
    """
    import numpy as np

    def _default(value):
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        if isinstance(value, (np.integer, np.floating, np.bool_)):
            return value.item()
        if isinstance(value, np.ndarray):
            return value.tolist()
        if pd.isna(value):
            return None
        raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")

    Path(path).write_text(
        json.dumps(snapshot_data, ensure_ascii=False, indent=2, default=_default),
        encoding="utf-8",
    )


def _json_default(value):
    """JSON 序列化兜底。"""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    if pd.isna(value):
        return None
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
