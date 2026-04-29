"""
持仓配置管理工具

从 portfolio.toml 读取持仓配置，转换为 DataFrame 供各模块使用
"""

import sys
from pathlib import Path
import pandas as pd

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))


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
        DataFrame with columns: code, name, market, quantity, cost_price, familiarity_detail

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

    # 解析熟悉程度评估（familiarity dict），向后兼容 conviction
    familiarity_details = []
    for h in holdings:
        fam = h.get("familiarity", {})
        if not fam and h.get("conviction", False):
            # conviction = true 向后兼容 → 视为四维度全通过
            fam = {d: True for d in [
                "business_model", "shareholder_friendly",
                "valuation_low", "trend_up",
            ]}
        familiarity_details.append(fam)
    df["familiarity_detail"] = familiarity_details

    return df[["code", "name", "market", "quantity", "cost_price", "familiarity_detail"]]


def sync_portfolio_to_csv(toml_path: str = None, csv_path: str = None):
    """
    将 portfolio.toml 同步到 CSV 文件（供旧代码兼容）

    Args:
        toml_path: TOML 文件路径
        csv_path: CSV 输出路径，默认为 risk_control/data/portfolio.csv
    """
    df = load_portfolio_from_toml(toml_path)
    # familiarity_detail 是 dict 列，CSV 不支持，导出时去掉
    export_df = df.drop(columns=["familiarity_detail"], errors="ignore")
    if csv_path is None:
        csv_path = Path(__file__).parent.parent / "risk_control" / "data" / "portfolio.csv"
    else:
        csv_path = Path(csv_path)

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    export_df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"✅ 持仓数据已同步到: {csv_path}")
    print(f"   共 {len(df)} 只股票")


if __name__ == "__main__":
    # 测试：读取并显示持仓
    try:
        df = load_portfolio_from_toml()
        print("📊 当前持仓：")
        print(df.to_string(index=False))
        print(f"\n总计：{len(df)} 只股票")

        # 同步到 CSV
        print("\n同步到 CSV...")
        sync_portfolio_to_csv()
    except Exception as e:
        print(f"❌ 错误：{e}")
        sys.exit(1)
