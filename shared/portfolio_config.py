"""
持仓配置管理工具

从 portfolio.toml 读取持仓配置，转换为 DataFrame 供各模块使用
"""

import sys
from pathlib import Path
import re
import pandas as pd

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))


_BUY_DATE_PATTERN = re.compile(r"^\d{8}$")
_VALID_STOP_LOSS_STRATEGIES = {"atr", "entry_day_low_guard", "equity_risk_budget"}
_BUY_DATE_REQUIRED_STRATEGIES = {"entry_day_low_guard"}


def _load_toml(toml_path: str = None) -> dict:
    """读取 portfolio.toml 并返回原始 dict"""
    try:
        import tomli
    except ImportError:
        try:
            import tomllib as tomli
        except ImportError:
            raise ImportError(
                "需要安装 tomli 库：pip install tomli\n"
                "或使用 Python 3.11+ (内置 tomllib)"
            )

    if toml_path is None:
        toml_path = Path(__file__).parent.parent / "portfolio.toml"
    else:
        toml_path = Path(toml_path)

    if not toml_path.exists():
        raise FileNotFoundError(
            f"持仓配置文件不存在: {toml_path}\n"
            f"请创建 portfolio.toml 文件，参考格式：\n"
            f"[account]\n"
            f"total_equity = 500000\n\n"
            f"[[holdings]]\n"
            f'code = "601216"\n'
            f'name = "君正集团"\n'
            f'market = "上海"\n'
            f'quantity = 9100\n'
            f'cost_price = 5.5243\n'
        )

    with open(toml_path, "rb") as f:
        return tomli.load(f)


def load_account_config(toml_path: str = None) -> dict:
    """从 portfolio.toml 读取 [account] 段

    Returns:
        dict: {"total_equity": float, ...}，无 [account] 段则返回空 dict
    """
    data = _load_toml(toml_path)
    return data.get("account", {})


def load_portfolio_from_toml(toml_path: str = None) -> pd.DataFrame:
    """
    从 portfolio.toml 加载持仓配置

    Args:
        toml_path: TOML 文件路径，默认为项目根目录的 portfolio.toml

    Returns:
        DataFrame with columns:
        code, name, market, quantity, cost_price, buy_date, trade_plan, risk_rules

    Raises:
        FileNotFoundError: 如果 portfolio.toml 不存在
        ValueError: 如果 TOML 格式错误
    """
    data = _load_toml(toml_path)

    # Extract holdings
    if "holdings" not in data:
        raise ValueError("portfolio.toml 缺少 [[holdings]] 配置")

    holdings = data["holdings"]
    if not holdings:
        raise ValueError("portfolio.toml 中没有持仓数据")

    # Convert to DataFrame
    df = pd.DataFrame(holdings)

    # Validate required columns
    required = {"code", "name", "market", "quantity", "cost_price"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"持仓数据缺少必要字段：{missing}")

    # Ensure correct types
    df["code"] = df["code"].astype(str)
    df["name"] = df["name"].astype(str)
    df["market"] = df["market"].astype(str)
    df["quantity"] = pd.to_numeric(df["quantity"])
    df["cost_price"] = pd.to_numeric(df["cost_price"])
    df["cost_price_source"] = "portfolio.toml"
    buy_date = df.get("buy_date")
    if buy_date is None:
        df["buy_date"] = ""
    else:
        df["buy_date"] = buy_date.fillna("").astype(str)

    trade_plan_list = []
    risk_rules_list = []
    for h in holdings:
        code = str(h.get("code", "unknown"))
        name = str(h.get("name", "unknown"))
        buy_date = str(h.get("buy_date", "") or "").strip()
        trade_plan = h.get("trade_plan", {})
        if not isinstance(trade_plan, dict):
            raise ValueError(f"{code} 的 trade_plan 必须是 table/dict")
        trade_plan = _normalize_trade_plan(trade_plan)
        risk_rules = h.get("risk_rules", {})
        if not isinstance(risk_rules, dict):
            raise ValueError(f"{code} 的 risk_rules 必须是 table/dict")
        risk_rules = _normalize_risk_rules(risk_rules)
        _validate_holding_config(code, name, buy_date, trade_plan, risk_rules)
        trade_plan_list.append(trade_plan)
        risk_rules_list.append(risk_rules)
    df["trade_plan"] = trade_plan_list
    df["risk_rules"] = risk_rules_list

    return df[[
        "code", "name", "market", "quantity", "cost_price", "buy_date",
        "cost_price_source", "trade_plan", "risk_rules",
    ]]


def _normalize_trade_plan(trade_plan):
    normalized = dict(trade_plan)
    normalized["status"] = str(normalized.get("status", "active") or "active").strip()
    normalized["plan_note"] = str(normalized.get("plan_note", "") or "").strip()
    normalized["stop_loss_strategy"] = str(normalized.get("stop_loss_strategy", "") or "").strip()
    normalized.pop("executor", None)
    normalized.pop("updated_at", None)
    return normalized


def _normalize_risk_rules(risk_rules):
    normalized = dict(risk_rules)
    stop_loss_params = normalized.get("stop_loss_params", {})
    if stop_loss_params is None:
        stop_loss_params = {}
    if not isinstance(stop_loss_params, dict):
        raise ValueError("risk_rules.stop_loss_params 必须是 table/dict")
    normalized["stop_loss_params"] = dict(stop_loss_params)
    normalized["stop_loss_strategy"] = str(normalized.get("stop_loss_strategy", "") or "").strip()
    if "max_loss_pct_of_equity" in normalized:
        normalized["max_loss_pct_of_equity"] = float(normalized["max_loss_pct_of_equity"])
    return normalized


def _validate_holding_config(code, name, buy_date, trade_plan, risk_rules):
    strategy = _resolve_stop_loss_strategy(trade_plan, risk_rules)
    if not strategy:
        return

    if strategy not in _VALID_STOP_LOSS_STRATEGIES:
        raise ValueError(f"{name}({code}) 的 stop_loss_strategy 不支持: {strategy}")

    if strategy not in _BUY_DATE_REQUIRED_STRATEGIES:
        if "max_loss_pct_of_equity" in risk_rules and risk_rules["max_loss_pct_of_equity"] <= 0:
            raise ValueError(f"{name}({code}) 的 risk_rules.max_loss_pct_of_equity 必须大于 0")
        return

    if not buy_date:
        raise ValueError(
            f"{name}({code}) 启用了 stop_loss_strategy='{strategy}'，"
            "但缺少 buy_date。请在对应 [[holdings]] 下补充 "
            'buy_date = "YYYYMMDD"，例如 buy_date = "20240102"。'
        )

    if not _BUY_DATE_PATTERN.match(buy_date):
        raise ValueError(
            f"{name}({code}) 的 buy_date 格式非法: {buy_date}。"
            '请使用 YYYYMMDD，例如 "20240102"。'
        )

    if "max_loss_pct_of_equity" in risk_rules and risk_rules["max_loss_pct_of_equity"] <= 0:
        raise ValueError(f"{name}({code}) 的 risk_rules.max_loss_pct_of_equity 必须大于 0")


def _resolve_stop_loss_strategy(trade_plan, risk_rules):
    if isinstance(trade_plan, dict):
        strategy = str(trade_plan.get("stop_loss_strategy", "") or "").strip()
        if strategy:
            return strategy
    return str(risk_rules.get("stop_loss_strategy", "") or "").strip()


def load_watchlist_from_toml(toml_path: str = None) -> pd.DataFrame:
    """从 portfolio.toml 加载观察列表。

    支持的可选字段:
    - target_buy_price: 回调到该价格及以下视为关注买点
    - breakout_price: 向上突破该价格视为突破买点
    - signal_rules: 插件策略的参数 dict
    - notes: 备注
    - enabled: 是否启用，默认 true
    """
    data = _load_toml(toml_path)
    rows = data.get("watchlist", [])
    if not rows:
        return pd.DataFrame(columns=[
            "code", "name", "market", "target_buy_price",
            "breakout_price", "signal_rules", "notes", "enabled",
        ])

    df = pd.DataFrame(rows)
    required = {"code", "name", "market"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"观察列表缺少必要字段：{missing}")

    df["code"] = df["code"].astype(str)
    df["name"] = df["name"].astype(str)
    df["market"] = df["market"].astype(str)
    df["target_buy_price"] = pd.to_numeric(df.get("target_buy_price"), errors="coerce")
    df["breakout_price"] = pd.to_numeric(df.get("breakout_price"), errors="coerce")
    signal_rules = df.get("signal_rules")
    if signal_rules is None:
        df["signal_rules"] = [{} for _ in range(len(df))]
    else:
        normalized = []
        for idx, value in enumerate(signal_rules.tolist()):
            if value is None:
                normalized.append({})
            elif isinstance(value, float) and pd.isna(value):
                normalized.append({})
            elif isinstance(value, dict):
                normalized.append(value)
            else:
                raise ValueError(f"观察列表第 {idx + 1} 行的 signal_rules 必须是 table/dict")
        df["signal_rules"] = normalized
    df["notes"] = df.get("notes", "").fillna("").astype(str)
    enabled = df.get("enabled")
    if enabled is None:
        df["enabled"] = True
    else:
        df["enabled"] = enabled.fillna(True).astype(bool)

    return df[[
        "code", "name", "market", "target_buy_price",
        "breakout_price", "signal_rules", "notes", "enabled",
    ]]


if __name__ == "__main__":
    # 测试：读取并显示持仓
    try:
        df = load_portfolio_from_toml()
        print("📊 当前持仓：")
        print(df.to_string(index=False))
        print(f"\n总计：{len(df)} 只股票")

    except Exception as e:
        print(f"❌ 错误：{e}")
        sys.exit(1)
