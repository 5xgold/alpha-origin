"""场景2：每日简报

用法:
    python llm_digest/scripts/daily_brief.py              # 默认今天
    python llm_digest/scripts/daily_brief.py --date 2026-04-14
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from llm_digest.config import RC_DATA_DIR, OUTPUT_DIR
from llm_digest.llm_client import chat_with_template
from shared.data_provider import get_stock_prices, get_benchmark_prices, get_sw_sector_returns, get_eastmoney_news


def normalize_security_code(code):
    """保留港股 5 位代码，A 股补足为 6 位。"""
    code = str(code).strip()
    if len(code) == 5 and code.isdigit():
        return code
    return code.zfill(6)


def get_market_overview(date_str):
    """获取大盘指数当日表现"""
    # 取前一个交易日作为起始（确保有两天数据算涨跌）
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    start = (dt - timedelta(days=7)).strftime("%Y-%m-%d")

    indices = [
        ("000300", "沪深300"),
        ("000001", "上证指数"),
    ]
    lines = []
    for code, name in indices:
        try:
            df = get_benchmark_prices(code, start, date_str)
            if df is not None and len(df) >= 2:
                prev_close = df.iloc[-2]["close"]
                today_close = df.iloc[-1]["close"]
                change = (today_close - prev_close) / prev_close * 100
                lines.append(f"- {name}: {today_close:.2f} ({change:+.2f}%)")
            else:
                lines.append(f"- {name}: 数据不足")
        except Exception as e:
            lines.append(f"- {name}: 获取失败 ({e})")
    return "\n".join(lines)


def get_portfolio_performance(date_str):
    """获取持仓个股当日表现"""
    portfolio_file = RC_DATA_DIR / "portfolio.csv"
    if not portfolio_file.exists():
        return "未找到持仓文件"

    df = pd.read_csv(portfolio_file, dtype={"code": str})
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    start = (dt - timedelta(days=7)).strftime("%Y-%m-%d")

    lines = []
    for _, row in df.iterrows():
        code = normalize_security_code(row["code"])
        name = row["name"]
        try:
            price_df = get_stock_prices(code, start, date_str)
            if price_df is not None and len(price_df) >= 2:
                prev_close = price_df.iloc[-2]["close"]
                today_close = price_df.iloc[-1]["close"]
                change = (today_close - prev_close) / prev_close * 100
                vol_today = price_df.iloc[-1].get("volume", "N/A")
                vol_prev = price_df.iloc[-2].get("volume", None)
                vol_change = ""
                if vol_prev and vol_prev > 0 and vol_today != "N/A":
                    vol_ratio = float(vol_today) / float(vol_prev)
                    vol_change = f"  量比 {vol_ratio:.1f}"
                lines.append(
                    f"- {name}({code}): {today_close:.3f} ({change:+.2f}%){vol_change}"
                )
            else:
                lines.append(f"- {name}({code}): 数据不足")
        except Exception as e:
            lines.append(f"- {name}({code}): 获取失败 ({e})")
    return "\n".join(lines)


def get_sector_performance(date_str):
    """获取行业板块涨跌排名"""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    start = (dt - timedelta(days=7)).strftime("%Y-%m-%d")
    try:
        sectors = get_sw_sector_returns(start, date_str)
        if not sectors:
            return "无行业数据"
        sorted_sectors = sorted(sectors.items(), key=lambda x: x[1]["return"], reverse=True)
        lines = []
        # 涨幅前5 + 跌幅前5
        top5 = sorted_sectors[:5]
        bottom5 = sorted_sectors[-5:]
        lines.append("涨幅前5:")
        for name, data in top5:
            lines.append(f"  {name}: {data['return']*100:+.2f}%")
        lines.append("跌幅前5:")
        for name, data in bottom5:
            lines.append(f"  {name}: {data['return']*100:+.2f}%")
        return "\n".join(lines)
    except Exception as e:
        return f"获取行业数据失败: {e}"


def get_risk_summary(date_str):
    """读取目标日期的风控建议，缺同日文件时回退到最近的历史报告。"""
    target_date = datetime.strptime(date_str, "%Y-%m-%d")
    candidates = []
    for path in OUTPUT_DIR.glob("risk_report_*.md"):
        suffix = path.stem.replace("risk_report_", "")
        try:
            report_date = datetime.strptime(suffix, "%Y%m%d")
        except ValueError:
            continue
        candidates.append((report_date, path))

    if not candidates:
        return ""

    candidates.sort(key=lambda item: item[0], reverse=True)
    chosen_path = next((path for report_date, path in candidates if report_date <= target_date), candidates[0][1])
    content = chosen_path.read_text(encoding="utf-8")
    # 提取操作建议部分
    lines = content.split("\n")
    summary_lines = []
    in_suggestion = False
    for line in lines:
        if "操作建议" in line or "明日操作" in line:
            in_suggestion = True
        if in_suggestion:
            summary_lines.append(line)
        if in_suggestion and line.strip().startswith("═"):
            break
    return "\n".join(summary_lines) if summary_lines else ""


def get_news():
    """获取东方财富快讯"""
    try:
        news_list = get_eastmoney_news(limit=20)
        if not news_list:
            return ""
        return "\n".join(f"- {item}" for item in news_list)
    except Exception as e:
        print(f"  获取新闻失败: {e}")
        return ""


def main():
    parser = argparse.ArgumentParser(description="每日简报")
    parser.add_argument("--date", help="日期（YYYY-MM-DD），默认今天")
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    print(f"生成 {date_str} 每日简报...")

    market = get_market_overview(date_str)
    portfolio = get_portfolio_performance(date_str)
    sectors = get_sector_performance(date_str)
    risk = get_risk_summary(date_str)
    news = get_news()

    context = {
        "date": date_str,
        "market_overview": market,
        "portfolio_performance": portfolio,
        "sector_performance": sectors,
        "risk_summary": risk,
        "news": news,
    }

    print("  调用 LLM 生成简报...")
    result = chat_with_template(
        "daily_brief.md",
        context,
        system="你是一位专业的投资顾问，为个人投资者提供简洁、可操作的每日简报。",
    )

    date_compact = date_str.replace("-", "")
    output_file = OUTPUT_DIR / f"daily_brief_{date_compact}.md"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file.write_text(f"# 每日简报 {date_str}\n\n{result}\n", encoding="utf-8")
    print(f"  ✓ 简报已生成: {output_file}")


if __name__ == "__main__":
    main()
