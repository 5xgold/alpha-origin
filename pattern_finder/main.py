"""
主程序入口
用法示例：

# 模式 1：用模拟数据快速体验
python main.py --mode demo

# 模式 2：用 akshare 拉真实数据构建样本库并查询
python main.py --mode akshare \
    --lib_stocks sh600519,sh000001,sz000858 \
    --query_stock sh600519 \
    --start 20220101 --end 20241231

# 模式 3：加载已保存的样本库，对新股票查询
python main.py --mode query \
    --lib_path data/cache/library.pkl \
    --query_stock sh600519
"""

import os
import sys
import argparse
import numpy as np
import pandas as pd
from datetime import datetime

# 把 pattern-finder 根目录加入 path
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from data.loader      import make_demo_data, load_akshare, load_csv
from features.feature_engine import build_indicators, create_windows, extract_vector
from similarity.retrieval     import (
    SampleLibrary, build_library_from_windows, hybrid_search
)
from backtest.analyzer        import compute_stats, print_report, stats_by_year
from visualization.report     import generate_html_report
from config.settings          import (
    LOOKBACK_DAYS, FORWARD_DAYS, TOP_K_SIMILAR, CACHE_DIR, OUTPUT_DIR
)


# ─── 构建样本库 ───────────────────────────────────────────────────

def build_library(stock_list: list, source: str = "demo",
                  start: str = "20200101", end: str = "20241231",
                  success_only: bool = True) -> SampleLibrary:
    """
    遍历股票列表，构建历史样本库
    success_only：只入库成功案例（可减小库大小）
    """
    library = SampleLibrary()

    for code in stock_list:
        print(f"  构建样本：{code} ...")
        try:
            if source == "demo":
                df_raw = make_demo_data(n=500, seed=hash(code) % 9999)
            elif source == "akshare":
                s_date = f"{start[:4]}-{start[4:6]}-{start[6:]}"
                e_date = f"{end[:4]}-{end[4:6]}-{end[6:]}"
                df_raw = load_akshare(code, s_date, e_date)
            elif source == "csv":
                df_raw = load_csv(code)   # code 作为文件路径
            else:
                raise ValueError(f"未知数据源: {source}")

            df_feat = build_indicators(df_raw)
            windows = create_windows(df_feat, lookback=LOOKBACK_DAYS,
                                     forward=FORWARD_DAYS, step=3)
            build_library_from_windows(
                windows, code, library, success_only=success_only
            )
            print(f"    ✓  加入 {sum(w['label'] for w in windows)} 个成功窗口")

        except Exception as e:
            print(f"    ✗  {code} 失败：{e}")

    return library


# ─── 查询当前股票 ─────────────────────────────────────────────────

def query_stock(stock_code: str, library: SampleLibrary,
                source: str = "demo",
                start: str = "20220101", end: str = "20241231",
                output_path: str = None) -> str:
    """查询给定股票当前形态的相似历史案例，生成报告"""
    print(f"\n  查询股票：{stock_code}")

    if source == "demo":
        df_raw = make_demo_data(n=300, seed=1234)
    elif source == "akshare":
        s_date = f"{start[:4]}-{start[4:6]}-{start[6:]}"
        e_date = f"{end[:4]}-{end[4:6]}-{end[6:]}"
        df_raw = load_akshare(stock_code, s_date, e_date)
    elif source == "csv":
        df_raw = load_csv(stock_code)
    else:
        raise ValueError(f"未知数据源: {source}")

    df_feat   = build_indicators(df_raw)
    # 取最新一个完整窗口作为查询
    query_win = df_feat.tail(LOOKBACK_DAYS)

    results   = hybrid_search(query_win, library, top_k=TOP_K_SIMILAR)
    stats     = compute_stats(results)

    print_report(stats, results, stock_code)

    by_year   = stats_by_year(results)
    if not by_year.empty:
        print("\n  分年度胜率：")
        print(by_year.to_string(index=False))

    # 生成 HTML 报告
    if output_path is None:
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        date_str    = datetime.now().strftime("%Y%m%d_%H%M")
        output_path = os.path.join(OUTPUT_DIR,
                                   f"{stock_code}-report-{date_str}.html")
    report_path = generate_html_report(
        query_stock = stock_code,
        query_df    = df_feat,
        results     = results,
        stats       = stats,
        output_path = output_path,
    )
    print(f"\n  ✅ HTML 报告已生成：{report_path}\n")
    return report_path


# ─── CLI 入口 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="形态相似检索系统")
    parser.add_argument("--mode", choices=["demo", "akshare", "query", "csv"],
                        default="demo")
    parser.add_argument("--lib_stocks",
                        default="sh600519,sh000001,sz000858",
                        help="构建样本库的股票列表，逗号分隔")
    parser.add_argument("--query_stock", default="sh600519",
                        help="待查询的股票代码")
    parser.add_argument("--start", default="20200101")
    parser.add_argument("--end",   default="20241231")
    parser.add_argument("--lib_path",
                        default=os.path.join(CACHE_DIR, "library.pkl"),
                        help="样本库保存/加载路径")
    parser.add_argument("--rebuild", action="store_true",
                        help="强制重新构建样本库")
    parser.add_argument("--output", default=None,
                        help="输出报告路径（默认自动命名）")
    args = parser.parse_args()

    # ── 样本库处理 ──────────────────────────────────────────────
    os.makedirs(CACHE_DIR, exist_ok=True)

    if args.mode == "demo":
        print("\n=== Demo 模式：使用模拟数据 ===")
        stock_list = ["DEMO_A", "DEMO_B", "DEMO_C",
                      "DEMO_D", "DEMO_E"]
        print(f"构建样本库（{len(stock_list)} 只股票）...")
        library = build_library(stock_list, source="demo")
        print(f"  样本库大小：{len(library)} 条成功案例")
        report = query_stock("DEMO_QUERY", library,
                             source="demo", output_path=args.output)

    elif args.mode in ("akshare", "csv"):
        source     = args.mode
        stock_list = [s.strip() for s in args.lib_stocks.split(",")]

        # 尝试加载已有样本库
        if not args.rebuild and os.path.exists(args.lib_path):
            print(f"\n  加载已有样本库：{args.lib_path}")
            library = SampleLibrary.load(args.lib_path)
            print(f"  样本库大小：{len(library)} 条")
        else:
            print(f"\n  构建样本库（{len(stock_list)} 只股票，"
                  f"{args.start}~{args.end}）...")
            library = build_library(stock_list, source=source,
                                    start=args.start, end=args.end)
            library.save(args.lib_path)
            print(f"  样本库已保存：{args.lib_path}  共 {len(library)} 条")

        report = query_stock(args.query_stock, library,
                             source=source,
                             start=args.start, end=args.end,
                             output_path=args.output)

    elif args.mode == "query":
        if not os.path.exists(args.lib_path):
            print(f"  ✗ 样本库不存在：{args.lib_path}")
            sys.exit(1)
        print(f"\n  加载样本库：{args.lib_path}")
        library = SampleLibrary.load(args.lib_path)
        report  = query_stock(args.query_stock, library,
                              source="demo", output_path=args.output)

    print(f"  完成！报告路径：{report}")
    return report


if __name__ == "__main__":
    main()
