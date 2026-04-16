"""场景3：财报摘要

用法:
    python llm_digest/scripts/earnings_summary.py \
        --input llm_digest/data/earnings/601216_2025Q4.pdf \
        --code 601216
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
import pdfplumber

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from llm_digest.config import RC_DATA_DIR, OUTPUT_DIR
from llm_digest.llm_client import chat_with_template, chat
from shared.data_provider import get_stock_prices

# 财报文本最大字符数（避免 token 超限）
MAX_TEXT_CHARS = 8000
# 分块摘要时每块最大字符数
CHUNK_SIZE = 6000


def extract_pdf_text(pdf_path):
    """用 pdfplumber 提取 PDF 文本"""
    text_parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # 优先提取表格
            tables = page.extract_tables()
            for table in tables:
                for row in table:
                    cells = [str(c or "") for c in row]
                    text_parts.append(" | ".join(cells))
            # 提取正文
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


def summarize_long_text(text):
    """长文本分块摘要"""
    if len(text) <= MAX_TEXT_CHARS:
        return text

    # 分块
    chunks = []
    for i in range(0, len(text), CHUNK_SIZE):
        chunks.append(text[i:i + CHUNK_SIZE])

    # 对每块提取关键信息
    summaries = []
    for i, chunk in enumerate(chunks):
        print(f"  摘要分块 {i+1}/{len(chunks)}...")
        summary = chat(
            f"请从以下财报文本中提取关键财务数据和重要信息，保留数字和表格，"
            f"去除套话和重复内容，压缩到 500 字以内：\n\n{chunk}",
            system="你是一位财务分析师，擅长从财报中提取关键数据。",
        )
        summaries.append(summary)

    return "\n\n---\n\n".join(summaries)


def get_holding_info(code):
    """检查是否持有该股票"""
    portfolio_file = RC_DATA_DIR / "portfolio.csv"
    if not portfolio_file.exists():
        return ""
    df = pd.read_csv(portfolio_file, dtype={"code": str})
    match = df[df["code"] == code.zfill(6)]
    if match.empty:
        return "当前未持有"
    row = match.iloc[0]
    return f"持有 {int(row['quantity'])} 股，成本 {row['cost_price']:.4f}"


def get_recent_price(code):
    """获取近期行情"""
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    try:
        df = get_stock_prices(code, start, end)
        if df is None or df.empty:
            return "无法获取近期行情"
        latest = df.iloc[-1]
        first = df.iloc[0]
        change = (latest["close"] - first["close"]) / first["close"] * 100
        return (
            f"近30日: {first['close']:.2f} → {latest['close']:.2f} ({change:+.2f}%)\n"
            f"最新收盘: {latest['close']:.2f}"
        )
    except Exception as e:
        return f"获取行情失败: {e}"


def infer_quarter(pdf_path):
    """从文件名推断财报季度（如 601216_2025Q4.pdf → 2025Q4）"""
    stem = Path(pdf_path).stem
    for part in stem.split("_"):
        if "Q" in part.upper() and len(part) >= 5:
            return part.upper()
    return "unknown"


def main():
    parser = argparse.ArgumentParser(description="财报摘要")
    parser.add_argument("--input", required=True, help="财报 PDF 路径")
    parser.add_argument("--code", required=True, help="股票代码")
    parser.add_argument("--name", help="公司名称")
    args = parser.parse_args()

    pdf_path = Path(args.input)
    if not pdf_path.exists():
        print(f"文件不存在: {pdf_path}")
        sys.exit(1)

    code = args.code.zfill(6)
    quarter = infer_quarter(pdf_path)
    print(f"分析 {code} {quarter} 财报: {pdf_path}")

    # 提取 PDF 文本
    print("  提取 PDF 文本...")
    raw_text = extract_pdf_text(pdf_path)
    if not raw_text.strip():
        print("  PDF 文本为空，请检查文件")
        sys.exit(1)
    print(f"  提取文本 {len(raw_text)} 字符")

    # 长文本分块摘要
    earnings_text = summarize_long_text(raw_text)

    # 获取辅助信息
    holding_info = get_holding_info(code)
    recent_price = get_recent_price(code)

    context = {
        "code": code,
        "name": args.name or "",
        "holding_info": holding_info,
        "recent_price": recent_price,
        "earnings_text": earnings_text,
    }

    print("  调用 LLM 生成摘要...")
    result = chat_with_template(
        "earnings_summary.md",
        context,
        system="你是一位专业的财务分析师，擅长解读上市公司财报并给出投资建议。",
    )

    output_file = OUTPUT_DIR / f"earnings_{code}_{quarter}.md"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    title = f"{args.name or code} {quarter}" if args.name else f"{code} {quarter}"
    output_file.write_text(f"# 财报摘要：{title}\n\n{result}\n", encoding="utf-8")
    print(f"  ✓ 摘要已生成: {output_file}")


if __name__ == "__main__":
    main()
