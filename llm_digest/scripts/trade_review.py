"""场景1：交易复盘

用法:
    python llm_digest/scripts/trade_review.py --code 601216 --name 君正集团
    python llm_digest/scripts/trade_review.py   # 复盘所有已平仓股票
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from llm_digest.config import AA_DATA_DIR, RC_DATA_DIR, OUTPUT_DIR
from llm_digest.llm_client import chat_with_template
from shared.data_provider import get_stock_prices, get_benchmark_prices


def is_hk_code(code):
    """5 位纯数字代码视为港股。"""
    return len(code) == 5 and code.isdigit()


def normalize_trade_code(code):
    """保留港股 5 位代码，A 股补足为 6 位。"""
    code = str(code).strip()
    if not code:
        return code
    if is_hk_code(code):
        return code
    return code.zfill(6)


def normalize_trade_quantities(df):
    """根据方向统一数量符号，兼容旧版 trades.csv。"""
    df = df.copy()
    quantities = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    buy_mask = df["direction"] == "买入"
    sell_mask = df["direction"] == "卖出"
    df.loc[buy_mask, "quantity"] = quantities[buy_mask].abs()
    df.loc[sell_mask, "quantity"] = -quantities[sell_mask].abs()
    return df


def filter_trades_by_code(df, code):
    """按代码过滤，优先精确匹配，再兼容 A 股补零输入。"""
    normalized = normalize_trade_code(code)
    exact = df[df["code"] == normalized]
    if not exact.empty:
        return exact

    raw = str(code).strip()
    if raw != normalized:
        return df[df["code"] == raw]
    return exact


def load_trades(code=None):
    """加载交易记录，可选按股票代码筛选"""
    trades_file = AA_DATA_DIR / "trades.csv"
    if not trades_file.exists():
        raise FileNotFoundError(f"交易记录不存在: {trades_file}")
    df = pd.read_csv(trades_file, dtype={"code": str})
    # 排除扣税等非交易记录
    df = df[df["direction"].isin(["买入", "卖出"])]
    df = normalize_trade_quantities(df)
    if code:
        df = filter_trades_by_code(df, code)
    return df


def find_closed_positions(df):
    """找出已平仓的股票（净持仓为 0）"""
    closed = []
    for code, group in df.groupby("code"):
        net_qty = pd.to_numeric(group["quantity"], errors="coerce").fillna(0).sum()
        if net_qty == 0:
            name = group.iloc[0]["name"]
            closed.append({"code": code, "name": name})
    return closed


def compute_trade_summary(df, code):
    """计算单只股票的交易汇总"""
    stock_df = filter_trades_by_code(df, code).sort_values("date")
    if stock_df.empty:
        return None

    buys = stock_df[stock_df["direction"] == "买入"]
    sells = stock_df[stock_df["direction"] == "卖出"]

    first_date = str(stock_df.iloc[0]["date"])
    last_date = str(stock_df.iloc[-1]["date"])
    first_dt = datetime.strptime(first_date, "%Y%m%d")
    last_dt = datetime.strptime(last_date, "%Y%m%d")
    holding_days = (last_dt - first_dt).days

    total_buy = buys["amount"].abs().sum() if not buys.empty else 0
    total_sell = sells["amount"].abs().sum() if not sells.empty else 0
    total_pnl = total_sell - total_buy

    fee_cols = ["brokerage_fee", "stamp_duty", "transfer_fee", "other_fee"]
    total_fees = stock_df[fee_cols].sum().sum()

    # 格式化交易记录表格
    records = []
    for _, row in stock_df.iterrows():
        records.append(
            f"  {row['date']}  {row['direction']}  "
            f"{abs(int(row['quantity']))}股 × {row['price']:.3f}  "
            f"金额 {abs(row['amount']):.2f}"
        )

    return {
        "name": stock_df.iloc[0]["name"],
        "trade_records": "\n".join(records),
        "first_date": first_date,
        "last_date": last_date,
        "holding_days": holding_days,
        "total_pnl": f"{'+'if total_pnl>=0 else ''}{total_pnl:,.2f} 元",
        "total_fees": f"{total_fees:,.2f} 元",
    }


def get_price_data(code, start_date, end_date):
    """获取持仓期间行情"""
    start = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    end = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
    try:
        df = get_stock_prices(code, start, end)
        if df is None or df.empty:
            return "无法获取行情数据"
        lines = ["  日期        开盘    最高    最低    收盘    成交量"]
        for _, row in df.iterrows():
            date_str = str(row.get("date", row.name))
            lines.append(
                f"  {date_str}  {row['open']:.2f}  {row['high']:.2f}  "
                f"{row['low']:.2f}  {row['close']:.2f}  {row.get('volume', 'N/A')}"
            )
        # 限制行数避免 token 超限
        if len(lines) > 62:
            lines = lines[:32] + ["  ... (中间省略) ..."] + lines[-30:]
        return "\n".join(lines)
    except Exception as e:
        return f"获取行情失败: {e}"


def get_benchmark_performance(start_date, end_date):
    """获取同期沪深300表现"""
    start = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
    end = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
    try:
        df = get_benchmark_prices("000300", start, end)
        if df is None or df.empty:
            return "无法获取基准数据"
        first_close = df.iloc[0]["close"]
        last_close = df.iloc[-1]["close"]
        ret = (last_close - first_close) / first_close * 100
        return f"沪深300 同期收益: {ret:+.2f}% ({first_close:.2f} → {last_close:.2f})"
    except Exception as e:
        return f"获取基准失败: {e}"


def extract_brinson_for_stock(code, name):
    """从归因报告提取该股票所在行业的 Brinson 归因"""
    report_file = OUTPUT_DIR / "report.md"
    if not report_file.exists():
        return ""
    content = report_file.read_text(encoding="utf-8")
    # 简单提取 Brinson 表格中包含该行业的行
    if "Brinson" not in content:
        return ""
    lines = content.split("\n")
    brinson_lines = []
    in_brinson = False
    for line in lines:
        if "Brinson" in line:
            in_brinson = True
        if in_brinson:
            brinson_lines.append(line)
            if line.startswith("| **合计**") or (line.strip() == "" and len(brinson_lines) > 3):
                break
    return "\n".join(brinson_lines) if brinson_lines else ""


def extract_risk_signals(code, name):
    """从风控报告提取相关信号"""
    import glob
    risk_files = sorted(OUTPUT_DIR.glob("risk_report_*.md"), reverse=True)
    if not risk_files:
        return ""
    content = risk_files[0].read_text(encoding="utf-8")
    # 提取包含该股票名称的行
    relevant = [line for line in content.split("\n") if name in line]
    return "\n".join(relevant) if relevant else "无相关风控信号"


def review_single(code, name, trades_df):
    """复盘单只股票"""
    summary = compute_trade_summary(trades_df, code)
    if not summary:
        print(f"  未找到 {code} 的交易记录")
        return

    if not name:
        name = summary["name"]

    print(f"  正在复盘 {name}（{code}）...")

    price_data = get_price_data(code, summary["first_date"], summary["last_date"])
    benchmark = get_benchmark_performance(summary["first_date"], summary["last_date"])
    brinson = extract_brinson_for_stock(code, name)
    risk = extract_risk_signals(code, name)

    context = {
        "code": code,
        "name": name,
        "trade_records": summary["trade_records"],
        "holding_days": summary["holding_days"],
        "total_pnl": summary["total_pnl"],
        "total_fees": summary["total_fees"],
        "price_data": price_data,
        "benchmark_performance": benchmark,
        "brinson_attribution": brinson,
        "risk_signals": risk,
    }

    result = chat_with_template(
        "trade_review.md",
        context,
        system="你是一位经验丰富的量化交易复盘分析师，擅长从数据中提取可操作的洞察。",
    )

    today = datetime.now().strftime("%Y%m%d")
    output_file = OUTPUT_DIR / f"trade_review_{code}_{today}.md"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file.write_text(f"# 交易复盘：{name}（{code}）\n\n{result}\n", encoding="utf-8")
    print(f"  ✓ 报告已生成: {output_file}")


def main():
    parser = argparse.ArgumentParser(description="交易复盘")
    parser.add_argument("--code", help="股票代码（如 601216）")
    parser.add_argument("--name", help="股票名称（如 君正集团）")
    args = parser.parse_args()

    trades_df = load_trades()

    if args.code:
        review_single(args.code, args.name, trades_df)
    else:
        # 复盘所有已平仓股票
        closed = find_closed_positions(trades_df)
        if not closed:
            print("未找到已平仓股票")
            return
        print(f"找到 {len(closed)} 只已平仓股票")
        for stock in closed:
            review_single(stock["code"], stock["name"], trades_df)


if __name__ == "__main__":
    main()
