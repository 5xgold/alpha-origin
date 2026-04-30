#!/usr/bin/env python3
"""生成每日复盘可视化图表。"""

import sys
import json
import argparse
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "output"
IMG_DIR = OUTPUT_DIR / "review_imgs"
IMG_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(ROOT_DIR))
from shared.data_provider import get_benchmark_prices


BG = "#141824"
PANEL = "#1d2333"
FG = "#f5f6fa"
GRID = "#424a60"
GREEN = "#22c55e"
RED = "#ef4444"
YELLOW = "#f59e0b"
BLUE = "#60a5fa"

INDEX_CODES = [
    ("上证指数", "000001"),
    ("沪深300", "000300"),
    ("中证500", "000905"),
    ("深证成指", "399001"),
]


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _latest_file(pattern):
    files = sorted(OUTPUT_DIR.glob(pattern))
    return files[-1] if files else None


def resolve_review_files(review_date=None):
    if review_date:
        daily = OUTPUT_DIR / f"daily_review_{review_date}.json"
        risk = OUTPUT_DIR / f"risk_snapshot_{review_date}_daily_review.json"
        if not daily.exists():
            raise FileNotFoundError(f"未找到每日复盘 JSON: {daily}")
        if not risk.exists():
            risk = _latest_file("risk_snapshot_*.json")
    else:
        daily = _latest_file("daily_review_*.json")
        if daily is None:
            raise FileNotFoundError("未找到 daily_review_*.json，请先运行每日复盘")
        payload = _read_json(daily)
        risk_path = payload.get("artifacts", {}).get("risk_snapshot")
        risk = Path(risk_path) if risk_path else None
        if risk is None or not risk.exists():
            risk = _latest_file("risk_snapshot_*.json")

    if risk is None or not risk.exists():
        raise FileNotFoundError("未找到 risk_snapshot JSON，请先运行 daily-review 或 risk")
    return daily, risk


def load_review_bundle(review_date=None):
    daily_file, risk_file = resolve_review_files(review_date)
    return _read_json(daily_file), _read_json(risk_file)


def _set_panel_style(ax):
    ax.set_facecolor(PANEL)
    ax.tick_params(colors=FG, labelsize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.spines["left"].set_color(GRID)
    ax.grid(axis="x", color=GRID, alpha=0.25, linewidth=0.8)


def _parse_hot_sector_lines(lines):
    rows = []
    for line in lines:
        text = line.strip().lstrip("-").strip()
        if ":" not in text:
            continue
        name, rest = text.split(":", 1)
        ret = 0.0
        strength = "观察"
        for token in rest.split("，"):
            token = token.strip()
            if token.startswith("近5日"):
                raw = token.replace("近5日", "").strip().rstrip("%")
                try:
                    ret = float(raw) / 100.0
                except ValueError:
                    ret = 0.0
            elif token:
                strength = token
        rows.append({"name": name.strip(), "return": ret, "strength": strength})
    return rows


def build_portfolio_rows(risk_snapshot):
    rows = []
    stop_map = {item["code"]: item for item in risk_snapshot.get("stop_loss", [])}
    total_equity = float(risk_snapshot.get("total_equity", 0) or 0)
    for row in risk_snapshot.get("portfolio", []):
        cost = float(row.get("cost_price") or 0)
        current = float(row.get("current_price") or 0)
        market_value = float(row.get("market_value") or 0)
        pnl = ((current - cost) / cost) if cost > 0 else 0.0
        stop = stop_map.get(str(row["code"]), {})
        rows.append({
            "name": row["name"],
            "code": str(row["code"]),
            "market_value": market_value,
            "weight": (market_value / total_equity) if total_equity > 0 else 0.0,
            "pnl": pnl,
            "signal": stop.get("signal", "hold"),
        })
    rows.sort(key=lambda item: item["market_value"], reverse=True)
    return rows


def fetch_index_changes(review_date):
    rows = []
    start = (pd.to_datetime(review_date) - pd.Timedelta(days=40)).strftime("%Y%m%d")
    for label, code in INDEX_CODES:
        try:
            df = get_benchmark_prices(code, start, review_date)
            if df is None or df.empty or len(df) < 2:
                continue
            close = df["close"].astype(float).reset_index(drop=True)
            current = float(close.iloc[-1])
            prev = float(close.iloc[-2])
            ret = (current - prev) / prev if prev else 0.0
            rows.append({"name": label, "return": ret, "close": current})
        except Exception:
            continue
    return rows


def create_portfolio_chart(risk_snapshot, review_date):
    rows = build_portfolio_rows(risk_snapshot)
    if not rows:
        raise RuntimeError("risk snapshot 中没有可用持仓数据")

    names = [f"{r['name']}({r['code'][-3:]})" for r in rows[:8]]
    weights = [r["weight"] for r in rows[:8]]
    pnls = [r["pnl"] * 100 for r in rows[:8]]
    bar_colors = []
    for item in rows[:8]:
        if item["signal"] == "stop_loss":
            bar_colors.append(RED)
        elif item["signal"] in ("take_profit", "trailing_stop"):
            bar_colors.append(YELLOW)
        else:
            bar_colors.append(BLUE if item["pnl"] >= 0 else GREEN)

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.patch.set_facecolor(BG)
    for ax in axes:
        _set_panel_style(ax)

    wedges, _, autotexts = axes[0].pie(
        weights,
        labels=names,
        autopct="%1.0f%%",
        colors=["#60a5fa", "#22c55e", "#f59e0b", "#e879f9", "#38bdf8", "#f97316", "#a78bfa", "#14b8a6"],
        startangle=90,
        wedgeprops=dict(width=0.42, edgecolor=BG, linewidth=2),
        textprops={"color": FG, "fontsize": 10},
    )
    for item in autotexts:
        item.set_color(BG)
        item.set_fontweight("bold")
    axes[0].set_title("持仓结构", color=FG, fontsize=14, fontweight="bold", pad=10)

    bars = axes[1].barh(names, pnls, color=bar_colors, height=0.56)
    axes[1].axvline(0, color=FG, linewidth=1)
    axes[1].set_title("持仓盈亏与风控", color=FG, fontsize=14, fontweight="bold", pad=10)
    axes[1].set_xlabel("盈亏 %", color=FG)
    for bar, pnl in zip(bars, pnls):
        offset = 0.4 if pnl >= 0 else -1.6
        axes[1].text(bar.get_width() + offset, bar.get_y() + bar.get_height() / 2,
                     f"{pnl:+.1f}%", color=FG, va="center", fontsize=9)

    fig.suptitle(f"每日投资复盘 {review_date}", color=FG, fontsize=16, fontweight="bold")
    fig.tight_layout()
    path = IMG_DIR / f"chart1_portfolio_{review_date}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    return path


def create_market_chart(daily_review, risk_snapshot, review_date):
    index_rows = fetch_index_changes(review_date)
    hot_rows = _parse_hot_sector_lines(daily_review.get("structured", {}).get("hot_sectors", {}).get("lines", []))
    alert_groups = risk_snapshot.get("alert_groups", {})
    alert_counts = {
        "关注": len(alert_groups.get("watch", [])),
        "警告": len(alert_groups.get("warning", [])),
        "危险": len(alert_groups.get("danger", [])),
    }

    fig, axes = plt.subplots(1, 2, figsize=(14, 6))
    fig.patch.set_facecolor(BG)
    for ax in axes:
        _set_panel_style(ax)

    if index_rows:
        idx_names = [item["name"] for item in index_rows]
        idx_rets = [item["return"] * 100 for item in index_rows]
        colors = [GREEN if item >= 0 else RED for item in idx_rets]
        bars = axes[0].bar(idx_names, idx_rets, color=colors, width=0.55)
        axes[0].axhline(0, color=FG, linewidth=1)
        axes[0].set_ylabel("涨跌幅 %", color=FG)
        axes[0].set_title("市场指数", color=FG, fontsize=14, fontweight="bold")
        for bar, ret in zip(bars, idx_rets):
            axes[0].text(bar.get_x() + bar.get_width() / 2, ret + (0.08 if ret >= 0 else -0.25),
                         f"{ret:+.2f}%", color=FG, ha="center", fontsize=9)
    else:
        axes[0].text(0.5, 0.5, "暂无指数数据", color=FG, ha="center", va="center", transform=axes[0].transAxes)
        axes[0].set_title("市场指数", color=FG, fontsize=14, fontweight="bold")

    if hot_rows:
        top = hot_rows[:5]
        names = [item["name"] for item in top]
        rets = [item["return"] * 100 for item in top]
        colors = [BLUE if item["strength"] == "持续性较强" else YELLOW for item in top]
        bars = axes[1].barh(names, rets, color=colors, height=0.58)
        axes[1].set_xlabel("近5日涨跌幅 %", color=FG)
        axes[1].set_title("热点与持续性", color=FG, fontsize=14, fontweight="bold")
        for bar, item in zip(bars, top):
            axes[1].text(bar.get_width() + 0.15, bar.get_y() + bar.get_height() / 2,
                         item["strength"], color=FG, va="center", fontsize=9)
    else:
        labels = list(alert_counts.keys())
        values = list(alert_counts.values())
        colors = [BLUE, YELLOW, RED]
        axes[1].bar(labels, values, color=colors, width=0.55)
        axes[1].set_title("风控预警分布", color=FG, fontsize=14, fontweight="bold")
        axes[1].set_ylabel("信号数", color=FG)

    fig.suptitle(f"每日投资复盘 {review_date}", color=FG, fontsize=16, fontweight="bold")
    fig.tight_layout()
    path = IMG_DIR / f"chart2_market_{review_date}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    return path


def main():
    parser = argparse.ArgumentParser(description="生成每日复盘图表")
    parser.add_argument("--date", help="指定复盘日期 YYYYMMDD，默认读取最新 daily_review JSON")
    args = parser.parse_args()

    daily_review, risk_snapshot = load_review_bundle(args.date)
    review_date = daily_review.get("review_date") or daily_review.get("snapshot_date", "").replace("-", "")
    chart1 = create_portfolio_chart(risk_snapshot, review_date)
    chart2 = create_market_chart(daily_review, risk_snapshot, review_date)
    print(f"图1已保存: {chart1}")
    print(f"图2已保存: {chart2}")
    print("✅ 图表生成完成")


if __name__ == "__main__":
    main()
