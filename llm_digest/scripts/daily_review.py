"""场景3：每日复盘

用法:
    python llm_digest/scripts/daily_review.py
    python llm_digest/scripts/daily_review.py --equity 500000
    python llm_digest/scripts/daily_review.py --date 2026-04-30
"""

import sys
import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from llm_digest.config import AA_DATA_DIR, OUTPUT_DIR
from risk_control.scripts.risk_report import (
    build_risk_snapshot,
    export_risk_snapshot,
    DEFAULT_PORTFOLIO_TOML,
)
from shared.data_provider import get_eastmoney_news, get_sw_sector_returns, get_stock_sector
from shared.portfolio_config import load_account_config


def normalize_trade_quantities(df):
    """根据方向统一数量符号，兼容旧版 trades.csv。"""
    df = df.copy()
    quantities = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    buy_mask = df["direction"] == "买入"
    sell_mask = df["direction"] == "卖出"
    df.loc[buy_mask, "quantity"] = quantities[buy_mask].abs()
    df.loc[sell_mask, "quantity"] = -quantities[sell_mask].abs()
    return df


def load_trades():
    """加载交易记录。"""
    trades_file = AA_DATA_DIR / "trades.csv"
    if not trades_file.exists():
        raise FileNotFoundError(f"交易记录不存在: {trades_file}")
    df = pd.read_csv(trades_file, dtype={"code": str})
    df = df[df["direction"].isin(["买入", "卖出"])]
    return normalize_trade_quantities(df)


def load_today_trades(review_date=None):
    """加载指定日期成交记录。"""
    if review_date is None:
        review_date = datetime.now().strftime("%Y%m%d")
    trades = load_trades()
    trades = trades.copy()
    trades["date"] = trades["date"].astype(str)
    return trades[trades["date"] == review_date].copy()


def summarize_today_trades(today_trades):
    """汇总今日成交，用于每日复盘 prompt。"""
    if today_trades.empty:
        return {
            "count": 0,
            "net_buy_amount": 0.0,
            "summary_lines": ["- 今日无成交"],
            "stock_actions": [],
        }

    df = normalize_trade_quantities(today_trades)
    grouped = []
    net_buy_amount = 0.0

    for code, group in df.groupby("code"):
        group = group.sort_values("date")
        name = str(group.iloc[0]["name"])
        buy_amount = float(group.loc[group["direction"] == "买入", "amount"].abs().sum())
        sell_amount = float(group.loc[group["direction"] == "卖出", "amount"].abs().sum())
        net_amount = sell_amount - buy_amount
        net_qty = float(pd.to_numeric(group["quantity"], errors="coerce").fillna(0).sum())
        actions = set(group["direction"].tolist())
        if "买入" in actions and "卖出" in actions:
            action = "调仓"
        elif "买入" in actions:
            action = "净买入"
        else:
            action = "净卖出"

        grouped.append({
            "code": code,
            "name": name,
            "action": action,
            "buy_amount": buy_amount,
            "sell_amount": sell_amount,
            "net_amount": net_amount,
            "net_quantity": net_qty,
            "trade_count": int(len(group)),
        })
        net_buy_amount += buy_amount - sell_amount

    grouped.sort(key=lambda item: abs(item["buy_amount"] + item["sell_amount"]), reverse=True)
    lines = []
    for item in grouped:
        if item["action"] == "净买入":
            lines.append(
                f"- {item['name']}({item['code']}): 净买入 "
                f"¥{item['buy_amount']:,.0f}，成交 {item['trade_count']} 笔，净增 {item['net_quantity']:.0f} 股"
            )
        elif item["action"] == "净卖出":
            lines.append(
                f"- {item['name']}({item['code']}): 净卖出 "
                f"¥{item['sell_amount']:,.0f}，成交 {item['trade_count']} 笔，净减 {abs(item['net_quantity']):.0f} 股"
            )
        else:
            lines.append(
                f"- {item['name']}({item['code']}): 日内调仓，买入 ¥{item['buy_amount']:,.0f} / "
                f"卖出 ¥{item['sell_amount']:,.0f}，净变动 {item['net_quantity']:.0f} 股"
            )

    return {
        "count": int(len(df)),
        "net_buy_amount": float(net_buy_amount),
        "summary_lines": lines,
        "stock_actions": grouped,
    }


def summarize_market(snapshot):
    """总结大盘表现。"""
    market = snapshot["market"]
    prices = market["prices"]
    if prices is None or prices.empty or len(prices) < 2:
        return {
            "lines": [f"- 指数: {market['index_name']}，波动率 {market['volatility']:.1f}%"],
        }

    closes = prices["close"].astype(float).reset_index(drop=True)
    current = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) >= 2 else current
    daily_ret = (current - prev) / prev if prev else 0.0

    def _window_ret(window):
        if len(closes) <= window:
            return None
        base = float(closes.iloc[-window - 1])
        return (current - base) / base if base else None

    ret_5 = _window_ret(5)
    ret_20 = _window_ret(20)
    regime = market["regime"]
    line = f"- {market['index_name']}: 今日 {daily_ret:+.2%}"
    if ret_5 is not None:
        line += f"，5日 {ret_5:+.2%}"
    if ret_20 is not None:
        line += f"，20日 {ret_20:+.2%}"
    line += f"，波动率 {market['volatility']:.1f}%，当前区间 {regime['label']}"
    return {"lines": [line]}


def summarize_hot_sectors(review_date):
    """用行业近5日/20日强度近似热点主线和持续性。"""
    review_dt = datetime.strptime(review_date, "%Y%m%d")
    start_5 = (review_dt - timedelta(days=7)).strftime("%Y%m%d")
    start_20 = (review_dt - timedelta(days=30)).strftime("%Y%m%d")

    sector_5 = get_sw_sector_returns(start_5, review_date) or {}
    sector_20 = get_sw_sector_returns(start_20, review_date) or {}

    if not sector_5:
        return {"lines": ["- 暂无行业热点数据"]}

    top_5 = sorted(
        sector_5.items(),
        key=lambda item: item[1].get("return", 0),
        reverse=True,
    )[:5]
    top_20_names = {
        name for name, _ in sorted(
            sector_20.items(),
            key=lambda item: item[1].get("return", 0),
            reverse=True,
        )[:10]
    }

    lines = []
    for name, data in top_5:
        sustained = "持续性较强" if name in top_20_names else "短线脉冲"
        lines.append(f"- {name}: 近5日 {data.get('return', 0):+.2%}，{sustained}")
    return {"lines": lines}


def summarize_news(limit=12):
    headlines = get_eastmoney_news(limit=limit)
    if not headlines:
        return {"lines": ["- 暂无可用新闻快讯"]}
    return {"lines": [f"- {title}" for title in headlines[:limit]]}


def summarize_holdings(snapshot):
    """从当前持仓 + 风控结果提炼复盘要点。"""
    portfolio_df = snapshot["portfolio_df"].copy()
    stop_map = {item["code"]: item for item in snapshot["stop_loss"]}
    signal_map = {}
    for sig in snapshot["signals"]:
        signal_map.setdefault(sig["code"], []).append(sig)

    total_equity = snapshot["total_equity"]
    lines = []
    for _, row in portfolio_df.sort_values("market_value", ascending=False).iterrows():
        code = str(row["code"])
        name = str(row["name"])
        cost = float(row["cost_price"])
        current = float(row["current_price"])
        mv = float(row["market_value"])
        weight = mv / total_equity if total_equity > 0 else 0.0
        pnl = (current - cost) / cost if cost > 0 else 0.0
        sector = get_stock_sector(code, name)
        risk_item = stop_map.get(code, {})
        risk_signal = risk_item.get("signal", "hold")
        signals = signal_map.get(code, [])
        signal_titles = "；".join(sig["title"] for sig in signals[:2]) or "无新增插件信号"
        lines.append(
            f"- {name}({code}) | {sector} | 仓位 {weight:.1%} | 盈亏 {pnl:+.1%} | "
            f"风控 {risk_signal} | 关键点 {signal_titles}"
        )
    return {"lines": lines[:12]}


def summarize_watchlist(snapshot):
    """为次日交易计划整理观察列表。"""
    watch = []
    for level in ("danger", "warning", "watch"):
        for sig in snapshot["alert_groups"].get(level, []):
            prefix = {"danger": "高优先级", "warning": "中优先级", "watch": "观察"}[level]
            watch.append(f"- {prefix}: {sig['name']}({sig['code']}) {sig['title']}")
    if not watch:
        watch = ["- 暂无风控观察项"]
    return {"lines": watch[:12]}


def build_daily_review_context(snapshot, review_date):
    news = summarize_news()
    market = summarize_market(snapshot)
    sectors = summarize_hot_sectors(review_date)
    holdings = summarize_holdings(snapshot)
    watchlist = summarize_watchlist(snapshot)
    trades = summarize_today_trades(load_today_trades(review_date))

    return {
        "review_date": snapshot["today"],
        "total_equity": f"¥{snapshot['total_equity']:,.0f}",
        "position": f"{snapshot['portfolio_summary']['current_position']:.0%}",
        "holding_count": snapshot["portfolio_summary"]["holding_count"],
        "market_news": "\n".join(news["lines"]),
        "market_summary": "\n".join(market["lines"]),
        "hot_sectors": "\n".join(sectors["lines"]),
        "holding_review": "\n".join(holdings["lines"]),
        "today_trades": "\n".join(trades["summary_lines"]),
        "trade_count": trades["count"],
        "net_buy_amount": f"¥{trades['net_buy_amount']:,.0f}",
        "watchlist": "\n".join(watchlist["lines"]),
    }, {
        "news": news,
        "market": market,
        "hot_sectors": sectors,
        "holdings": holdings,
        "trades": trades,
        "watchlist": watchlist,
    }


def _render_prompt(context):
    """渲染 Jinja2 模板，返回完整 prompt 文本。"""
    from jinja2 import Environment, FileSystemLoader
    from llm_digest.config import PROMPTS_DIR

    env = Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), keep_trailing_newline=True)
    template = env.get_template("daily_review.md")
    return template.render(**context)


def generate_daily_review(review_date=None, total_equity=None, skip_llm=False):
    if review_date is None:
        review_date = datetime.now().strftime("%Y%m%d")
    if total_equity is None:
        account = load_account_config(str(DEFAULT_PORTFOLIO_TOML))
        total_equity = account.get("total_equity")
    if total_equity is None:
        raise RuntimeError("未指定总权益，且 portfolio.toml 缺少 [account].total_equity")

    snapshot = build_risk_snapshot(total_equity)
    risk_snapshot_file = OUTPUT_DIR / f"risk_snapshot_{review_date}_daily_review.json"
    export_risk_snapshot(snapshot, risk_snapshot_file)
    context, structured = build_daily_review_context(snapshot, review_date)

    # 渲染 prompt（始终生成，供任意 LLM agent 使用）
    prompt_text = _render_prompt(context)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    prompt_file = OUTPUT_DIR / f"daily_review_{review_date}_prompt.md"
    prompt_file.write_text(prompt_text, encoding="utf-8")

    # LLM 调用（可选）
    llm_result = None
    if not skip_llm:
        try:
            from llm_digest.llm_client import chat_with_template
            llm_result = chat_with_template(
                "daily_review.md",
                context,
                system="你是一位严格、务实的职业交易复盘助手，擅长从新闻、市场、热点、持仓和交易动作中提炼出次日可执行计划。",
            )
        except Exception as e:
            print(f"⚠ LLM 调用跳过: {e}")

    if llm_result:
        output_file = OUTPUT_DIR / f"daily_review_{review_date}.md"
        output_file.write_text(
            f"# 每日投资复盘 {snapshot['today']}\n\n{llm_result}\n",
            encoding="utf-8",
        )
        print(f"✓ 每日复盘已生成: {output_file}")
    else:
        print(f"✓ 复盘 prompt 已生成: {prompt_file}（可喂给任意大模型）")

    json_file = OUTPUT_DIR / f"daily_review_{review_date}.json"
    json_file.write_text(
        json.dumps({
            "snapshot_date": snapshot["today"],
            "review_date": review_date,
            "context": context,
            "structured": structured,
            "artifacts": {
                "risk_snapshot": str(risk_snapshot_file),
                "prompt": str(prompt_file),
            },
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return prompt_file


def main():
    parser = argparse.ArgumentParser(description="每日复盘")
    parser.add_argument("--date", help="复盘日期 YYYYMMDD，默认今天")
    parser.add_argument("--equity", type=float, default=None, help="总权益，不指定则从 portfolio.toml 读取")
    parser.add_argument("--skip-llm", action="store_true", help="跳过 LLM 调用，仅生成 prompt 文件")
    args = parser.parse_args()
    generate_daily_review(review_date=args.date, total_equity=args.equity, skip_llm=args.skip_llm)


if __name__ == "__main__":
    main()
