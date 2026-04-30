#!/usr/bin/env python3
"""生成每日复盘上下文，供人类或 AI 助手直接消费。"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

import pandas as pd


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from risk_control.scripts.risk_report import build_risk_snapshot, export_risk_snapshot
from risk_control.signals.alert import classify_alerts
from shared.data_provider import get_benchmark_prices, get_eastmoney_news, get_stock_prices, get_sw_sector_returns
from shared.store import get_account, get_today_trades, get_watchlist, save_output
from watchlist_signals import (
    clear_inactive_signal_records as clear_inactive_watch_signal_records,
    clear_stale_signals as clear_stale_watch_signals,
    load_state as load_watch_state,
    run_all_watch_signals,
    save_state as save_watch_state,
)


OUTPUT_DIR = ROOT_DIR / "output"


def normalize_review_date(date_str=None):
    if not date_str:
        return datetime.now().strftime("%Y%m%d")
    digits = str(date_str).replace("-", "").strip()
    if len(digits) != 8 or not digits.isdigit():
        raise ValueError("日期格式必须为 YYYYMMDD 或 YYYY-MM-DD")
    return digits


def _fmt_pct(value):
    return f"{value:+.1%}"


def _fmt_price(value):
    if value is None or pd.isna(value):
        return "N/A"
    return f"{float(value):.2f}"


def _latest_close(code, review_date, lookback_days=80):
    end_ts = pd.to_datetime(review_date)
    start_ts = end_ts - pd.Timedelta(days=lookback_days)
    df = get_stock_prices(
        str(code),
        start_ts.strftime("%Y%m%d"),
        end_ts.strftime("%Y%m%d"),
    )
    if df is None or df.empty:
        return None, None, None

    frame = df.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame[frame["date"] <= end_ts].sort_values("date").reset_index(drop=True)
    if frame.empty:
        return None, None, None

    current = float(frame["close"].iloc[-1])
    previous = float(frame["close"].iloc[-2]) if len(frame) >= 2 else None
    return current, previous, frame


def build_market_context(review_date):
    news = get_eastmoney_news(limit=12)
    end_ts = pd.to_datetime(review_date)
    start_5 = (end_ts - pd.Timedelta(days=7)).strftime("%Y%m%d")
    start_20 = (end_ts - pd.Timedelta(days=30)).strftime("%Y%m%d")
    end_str = end_ts.strftime("%Y%m%d")

    try:
        sector_5 = get_sw_sector_returns(start_5, end_str) or {}
    except Exception as exc:
        print(f"  获取近5日行业强度失败: {exc}")
        sector_5 = {}
    try:
        sector_20 = get_sw_sector_returns(start_20, end_str) or {}
    except Exception as exc:
        print(f"  获取近20日行业强度失败: {exc}")
        sector_20 = {}

    rows = []
    all_names = set(sector_5) | set(sector_20)
    for name in all_names:
        ret5 = float(sector_5.get(name, {}).get("return", 0.0) or 0.0)
        ret20 = float(sector_20.get(name, {}).get("return", 0.0) or 0.0)
        score = ret5 * 0.7 + ret20 * 0.3
        if ret5 > 0 and ret20 > 0:
            strength = "持续性较强"
        elif ret5 > 0:
            strength = "短线活跃"
        else:
            strength = "观察"
        rows.append({
            "name": name,
            "return_5d": ret5,
            "return_20d": ret20,
            "strength": strength,
            "score": score,
        })

    rows.sort(key=lambda item: item["score"], reverse=True)
    hot = rows[:5]
    hot_lines = [
        f"- {item['name']}: 近5日{item['return_5d']:+.1%}，近20日{item['return_20d']:+.1%}，{item['strength']}"
        for item in hot
    ]

    benchmark_rows = []
    for name, code in [("上证指数", "000001"), ("沪深300", "000300"), ("中证500", "000905"), ("深证成指", "399001")]:
        start = (end_ts - pd.Timedelta(days=10)).strftime("%Y%m%d")
        try:
            df = get_benchmark_prices(code, start, end_str)
            if df is None or df.empty or len(df) < 2:
                continue
            close = df["close"].astype(float).reset_index(drop=True)
            current = float(close.iloc[-1])
            previous = float(close.iloc[-2])
            day_return = (current - previous) / previous if previous else 0.0
            benchmark_rows.append({
                "name": name,
                "code": code,
                "close": current,
                "day_return": day_return,
            })
        except Exception as exc:
            print(f"  获取基准 {code} 失败，已跳过: {exc}")

    return {
        "news": news,
        "hot_sectors": hot,
        "hot_sector_lines": hot_lines,
        "benchmarks": benchmark_rows,
    }


def summarize_trades(review_date):
    trades = get_today_trades(review_date)
    if trades.empty:
        return {
            "trade_count": 0,
            "net_amount": 0.0,
            "lines": ["- 今日无成交"],
            "records": [],
        }

    frame = trades.copy()
    frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce").fillna(0.0)
    buy_mask = frame["direction"] == "买入"
    sell_mask = frame["direction"] == "卖出"
    net_buy = float(frame.loc[buy_mask, "amount"].sum() - frame.loc[sell_mask, "amount"].sum())

    lines = []
    records = []
    for _, row in frame.sort_values(["code", "direction"]).iterrows():
        qty = int(abs(float(row["quantity"])))
        amount = float(row["amount"])
        lines.append(f"- {row['name']}({row['code']}) {row['direction']} {qty}股，成交额 ¥{amount:,.0f}")
        records.append({
            "code": str(row["code"]),
            "name": row["name"],
            "direction": row["direction"],
            "quantity": qty,
            "amount": amount,
        })

    return {
        "trade_count": int(len(frame)),
        "net_amount": net_buy,
        "lines": lines,
        "records": records,
    }


def summarize_holdings(snapshot):
    portfolio_rows = snapshot["portfolio_df"].copy()
    stop_map = {item["code"]: item for item in snapshot["stop_loss"]}
    lines = []
    records = []

    total_equity = float(snapshot["total_equity"])
    for _, row in portfolio_rows.sort_values("market_value", ascending=False).iterrows():
        code = str(row["code"])
        cost = float(row["cost_price"])
        current = float(row["current_price"])
        market_value = float(row["market_value"])
        weight = (market_value / total_equity) if total_equity else 0.0
        pnl = ((current - cost) / cost) if cost else 0.0
        stop = stop_map.get(code, {})
        signal = stop.get("signal", "hold")
        records.append({
            "code": code,
            "name": row["name"],
            "weight": weight,
            "pnl_pct": pnl,
            "signal": signal,
            "current_price": current,
        })
        lines.append(
            f"- {row['name']}({code}) 仓位{weight:.1%}，浮盈亏{pnl:+.1%}，现价{current:.2f}，信号{signal}"
        )

    danger_names = [item["name"] for item in snapshot["alert_groups"].get("danger", [])]
    warning_names = [item["name"] for item in snapshot["alert_groups"].get("warning", [])]

    return {
        "lines": lines[:8],
        "records": records,
        "danger_names": danger_names,
        "warning_names": warning_names,
    }


def evaluate_watchlist(review_date):
    watchlist = get_watchlist()
    if watchlist.empty:
        return {
            "lines": ["- 未配置观察列表"],
            "records": [],
            "triggered": [],
            "signals": [],
            "alert_groups": {"watch": [], "warning": [], "danger": []},
        }

    latest_prices = {}
    active_df = watchlist[watchlist["enabled"]].copy()
    if active_df.empty:
        return {
            "lines": ["- 观察列表全部为禁用状态"],
            "records": [],
            "triggered": [],
            "signals": [],
            "alert_groups": {"watch": [], "warning": [], "danger": []},
        }

    records = []
    for _, row in active_df.iterrows():
        current, previous, _ = _latest_close(row["code"], review_date)
        latest_prices[str(row["code"])] = {
            "current_price": current,
            "previous_close": previous,
        }
        signals = []

        target_buy = row["target_buy_price"]
        if pd.notna(target_buy) and current is not None and current <= float(target_buy):
            signals.append("回调到目标买点")

        breakout = row["breakout_price"]
        if (
            pd.notna(breakout)
            and current is not None
            and previous is not None
            and previous < float(breakout) <= current
        ):
            signals.append("突破买点")

        if current is None:
            state = "no_data"
        elif signals:
            state = "triggered"
        else:
            state = "watch"

        records.append({
            "code": str(row["code"]),
            "name": row["name"],
            "market": row["market"],
            "current_price": current,
            "target_buy_price": None if pd.isna(target_buy) else float(target_buy),
            "breakout_price": None if pd.isna(breakout) else float(breakout),
            "signals": signals,
            "state": state,
            "notes": row["notes"],
        })

    watch_state = load_watch_state()
    clear_stale_watch_signals(watch_state, active_df["code"].astype(str).tolist())
    signal_results = run_all_watch_signals(
        active_df,
        state=watch_state,
        latest_prices=latest_prices,
        review_date=review_date,
    )
    active_signal_keys = {(sig["code"], sig["strategy"]) for sig in signal_results}
    clear_inactive_watch_signal_records(watch_state, active_signal_keys)
    save_watch_state(watch_state)
    alert_groups = classify_alerts(signal_results)

    records.sort(key=lambda item: (item["state"] != "triggered", item["code"]))
    lines = []
    for item in records[:8]:
        if item["state"] == "triggered":
            signal_text = " / ".join(item["signals"])
            lines.append(
                f"- {item['name']}({item['code']}) 现价{_fmt_price(item['current_price'])}，触发{signal_text}"
            )
        elif item["state"] == "no_data":
            lines.append(f"- {item['name']}({item['code']}) 暂无行情数据")
        else:
            target = f"回调≤{_fmt_price(item['target_buy_price'])}" if item["target_buy_price"] else ""
            breakout = f"突破≥{_fmt_price(item['breakout_price'])}" if item["breakout_price"] else ""
            joined = "，".join([part for part in [target, breakout] if part]) or "继续观察"
            lines.append(
                f"- {item['name']}({item['code']}) 现价{_fmt_price(item['current_price'])}，{joined}"
            )

    triggered_codes = {sig["code"] for sig in signal_results}
    triggered = [item for item in records if item["code"] in triggered_codes or item["state"] == "triggered"]
    return {
        "lines": lines,
        "records": records,
        "triggered": triggered,
        "signals": signal_results,
        "alert_groups": alert_groups,
    }


def build_action_plan(snapshot, trade_summary, watchlist_summary):
    actions = []
    for item in snapshot["alert_groups"].get("danger", []):
        actions.append(f"优先处理危险信号：{item['name']}({item['code']})")
    for item in watchlist_summary["alert_groups"].get("warning", []):
        actions.append(f"重点复核买点触发：{item['name']}({item['code']})")
    for item in watchlist_summary["triggered"]:
        actions.append(f"复核买点是否有效：{item['name']}({item['code']})")
    if trade_summary["trade_count"] > 0:
        direction = "净买入" if trade_summary["net_amount"] >= 0 else "净卖出"
        actions.append(f"复盘今日成交原因，当前为{direction} ¥{abs(trade_summary['net_amount']):,.0f}")
    if not actions:
        actions.append("无强制动作，按观察名单跟踪市场与个股变化")
    return actions[:6]


def render_prompt(context):
    market = context["structured"]["market"]
    portfolio = context["structured"]["portfolio"]
    watchlist = context["structured"]["watchlist"]
    trades = context["structured"]["today_trades"]
    actions = context["structured"]["next_actions"]

    sections = [
        f"# 每日复盘输入包 {context['review_date']}",
        "",
        "你是我的A股/港股交易复盘助手。请基于下面的结构化信息，输出一份晚间复盘报告。",
        "",
        "输出要求：",
        "1. 先写结论，再写证据，不要空话。",
        "2. 区分持仓处理、观察名单、明日动作三部分。",
        "3. 对卖出/止损/止盈建议，要写出触发依据。",
        "4. 对观察名单，只提示“是否接近或触发买点”，不要替我直接下结论重仓买入。",
        "",
        "## 市场",
    ]
    sections.extend(f"- {row['name']} 收于 {row['close']:.2f}，日涨跌 {_fmt_pct(row['day_return'])}" for row in market["benchmarks"])
    sections.append("")
    sections.append("### 新闻")
    sections.extend(f"- {line}" for line in market["news"][:8])
    sections.append("")
    sections.append("### 热点板块")
    sections.extend(market["hot_sectors"]["lines"])
    sections.append("")
    sections.append("## 今日成交")
    sections.extend(trades["lines"])
    sections.append("")
    sections.append("## 当前持仓")
    sections.extend(portfolio["lines"])
    sections.append("")
    sections.append("## 待买入观察列表")
    sections.extend(watchlist["lines"])
    if watchlist["signals"]:
        sections.append("")
        sections.append("### 观察列表信号")
        sections.extend(f"- {sig['name']}({sig['code']}): {sig['title']}，{sig['detail']}" for sig in watchlist["signals"])
    sections.append("")
    sections.append("## 明日动作候选")
    sections.extend(f"- {line}" for line in actions)
    sections.append("")
    sections.append("请输出 Markdown，结构为：")
    sections.append("1. 今日结论")
    sections.append("2. 市场与热点")
    sections.append("3. 持仓处理")
    sections.append("4. 观察名单")
    sections.append("5. 明日计划")
    return "\n".join(sections)


def render_report(context):
    market = context["structured"]["market"]
    trades = context["structured"]["today_trades"]
    portfolio = context["structured"]["portfolio"]
    watchlist = context["structured"]["watchlist"]
    actions = context["structured"]["next_actions"]

    lines = [
        f"# 每日复盘 {context['review_date']}",
        "",
        "## 今日结论",
    ]
    if portfolio["danger_names"]:
        lines.append(f"- 持仓风险优先级最高：{', '.join(portfolio['danger_names'])}")
    elif watchlist["triggered"]:
        names = "、".join(f"{item['name']}({item['code']})" for item in watchlist["triggered"])
        lines.append(f"- 观察名单出现触发信号：{names}")
    else:
        lines.append("- 持仓未见强制处理信号，重点仍是跟踪热点持续性与个股节奏。")

    if trades["trade_count"] > 0:
        direction = "净买入" if trades["net_amount"] >= 0 else "净卖出"
        lines.append(f"- 今日有 {trades['trade_count']} 笔成交，{direction} ¥{abs(trades['net_amount']):,.0f}。")
    else:
        lines.append("- 今日无成交，复盘重点放在持仓和待买入观察。")

    lines.extend([
        "",
        "## 市场与热点",
    ])
    for row in market["benchmarks"]:
        lines.append(f"- {row['name']}：{_fmt_pct(row['day_return'])}，收于 {row['close']:.2f}")
    lines.extend(market["hot_sectors"]["lines"] or ["- 暂无板块强弱数据"])

    lines.extend([
        "",
        "## 当前持仓",
    ])
    lines.extend(portfolio["lines"] or ["- 暂无持仓"])

    lines.extend([
        "",
        "## 待买入观察列表",
    ])
    lines.extend(watchlist["lines"])
    if watchlist["signals"]:
        lines.extend([
            "",
            "## 观察列表信号",
        ])
        lines.extend(f"- {sig['name']}({sig['code']})：{sig['title']}，{sig['detail']}" for sig in watchlist["signals"])

    lines.extend([
        "",
        "## 明日计划",
    ])
    lines.extend(f"- {line}" for line in actions)
    return "\n".join(lines) + "\n"


def build_context(review_date, snapshot):
    market_context = build_market_context(review_date)
    trade_summary = summarize_trades(review_date)
    holding_summary = summarize_holdings(snapshot)
    watchlist_summary = evaluate_watchlist(review_date)
    actions = build_action_plan(snapshot, trade_summary, watchlist_summary)

    return {
        "review_date": review_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "artifacts": {},
        "structured": {
            "market": {
                "benchmarks": market_context["benchmarks"],
                "news": market_context["news"],
                "hot_sectors": {
                    "rows": market_context["hot_sectors"],
                    "lines": market_context["hot_sector_lines"],
                },
            },
            "today_trades": trade_summary,
            "portfolio": holding_summary,
            "watchlist": watchlist_summary,
            "next_actions": actions,
        },
    }


def main():
    parser = argparse.ArgumentParser(description="每日复盘上下文生成器")
    parser.add_argument("--date", help="复盘日期，格式 YYYYMMDD 或 YYYY-MM-DD")
    parser.add_argument("--equity", type=float, default=None, help="总权益，不传则从 portfolio.toml 读取")
    args = parser.parse_args()

    review_date = normalize_review_date(args.date)
    equity = args.equity
    if equity is None:
        equity = get_account().get("total_equity")
    if equity is None:
        parser.error("未指定 --equity 且 portfolio.toml 中无 [account].total_equity")

    snapshot = build_risk_snapshot(float(equity))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    context = build_context(review_date, snapshot)

    risk_json = OUTPUT_DIR / f"risk_snapshot_{review_date}_daily_review.json"
    export_risk_snapshot(snapshot, risk_json)
    context["artifacts"]["risk_snapshot"] = str(risk_json)

    prompt_text = render_prompt(context)
    saved = save_output("daily_review", review_date, prompt_text, context)

    report_path = OUTPUT_DIR / f"daily_review_{review_date}_report.md"
    report_path.write_text(render_report(context), encoding="utf-8")
    context["artifacts"].update({
        "prompt": str(saved["prompt"]),
        "json": str(saved["json"]),
        "report": str(report_path),
    })
    saved["json"].write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"每日复盘日期: {review_date}")
    print(f"Prompt: {saved['prompt']}")
    print(f"JSON:   {saved['json']}")
    print(f"Report: {report_path}")
    print(f"Risk:   {risk_json}")


if __name__ == "__main__":
    main()
