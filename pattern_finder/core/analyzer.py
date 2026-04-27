"""
回测验证与统计分析模块
对检索到的相似历史案例进行统计，输出：
- 胜率、盈亏比、最大回撤
- 未来收益分布（5/10/20日）
- 分年份验证
- 市场环境标签（牛/震荡/熊）
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Optional
from dataclasses import dataclass, field
from similarity.retrieval import SearchResult, SampleRecord


# ─── 统计分析结果 ─────────────────────────────────────────────────

@dataclass
class BacktestStats:
    """相似案例统计结果"""
    total_cases:       int = 0
    success_cases:     int = 0
    win_rate:          float = 0.0

    # 收益分布
    mean_return:       float = 0.0
    median_return:     float = 0.0
    max_return:        float = 0.0
    min_return:        float = 0.0
    std_return:        float = 0.0

    # 风险指标
    mean_drawdown:     float = 0.0
    max_drawdown:      float = 0.0

    # 盈亏比
    avg_win:           float = 0.0
    avg_loss:          float = 0.0
    profit_loss_ratio: float = 0.0

    # 分位数
    pct_25:            float = 0.0
    pct_75:            float = 0.0

    # 原始数据（用于分布图）
    returns:           List[float] = field(default_factory=list)
    drawdowns:         List[float] = field(default_factory=list)
    years:             List[str]   = field(default_factory=list)


def compute_stats(results: List[SearchResult],
                  threshold: float = 0.10) -> BacktestStats:
    """
    对检索结果列表做统计分析
    threshold: 定义"成功"的收益率门槛
    """
    if not results:
        return BacktestStats()

    stats = BacktestStats()
    stats.total_cases = len(results)

    returns   = [r.sample.future_return  for r in results]
    drawdowns = [r.sample.future_drawdown for r in results]
    years     = [r.sample.end_date[:4]   for r in results]

    stats.returns   = returns
    stats.drawdowns = drawdowns
    stats.years     = years

    arr = np.array(returns)
    dd  = np.array(drawdowns)

    stats.success_cases = int(np.sum(arr >= threshold))
    stats.win_rate      = stats.success_cases / stats.total_cases

    stats.mean_return   = float(np.mean(arr))
    stats.median_return = float(np.median(arr))
    stats.max_return    = float(np.max(arr))
    stats.min_return    = float(np.min(arr))
    stats.std_return    = float(np.std(arr))
    stats.pct_25        = float(np.percentile(arr, 25))
    stats.pct_75        = float(np.percentile(arr, 75))

    stats.mean_drawdown = float(np.mean(dd))
    stats.max_drawdown  = float(np.max(dd))

    wins   = arr[arr >= 0]
    losses = arr[arr <  0]
    stats.avg_win  = float(np.mean(wins))  if len(wins)   > 0 else 0.0
    stats.avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0.0
    stats.profit_loss_ratio = (
        abs(stats.avg_win / stats.avg_loss)
        if stats.avg_loss != 0 else float("inf")
    )

    return stats


# ─── 分年度统计 ───────────────────────────────────────────────────

def stats_by_year(results: List[SearchResult],
                  threshold: float = 0.10) -> pd.DataFrame:
    """按年度分组统计，输出 DataFrame"""
    if not results:
        return pd.DataFrame()

    rows = []
    year_map: Dict[str, List[SearchResult]] = {}
    for r in results:
        y = r.sample.end_date[:4]
        year_map.setdefault(y, []).append(r)

    for year, group in sorted(year_map.items()):
        s = compute_stats(group, threshold)
        rows.append({
            "年份":   year,
            "样本数":  s.total_cases,
            "胜率":   f"{s.win_rate:.1%}",
            "均值收益": f"{s.mean_return:.1%}",
            "中位收益": f"{s.median_return:.1%}",
            "均值回撤": f"{s.mean_drawdown:.1%}",
        })
    return pd.DataFrame(rows)


# ─── 综合评分 ─────────────────────────────────────────────────────

def compute_score(stats: BacktestStats) -> Dict[str, float]:
    """
    综合评分（满分 100）
    用于 HTML 报告雷达图
    """
    # 胜率得分（0~40）
    win_score = min(stats.win_rate * 40 / 0.6, 40)

    # 盈亏比得分（0~20）
    plr_score = min(stats.profit_loss_ratio / 3 * 20, 20)

    # 均值收益得分（0~20）
    ret_score = min(max(stats.mean_return / 0.15 * 20, 0), 20)

    # 回撤控制得分（0~20）
    dd_score  = max(20 - stats.mean_drawdown / 0.1 * 20, 0)

    total = win_score + plr_score + ret_score + dd_score

    return {
        "胜率得分":   round(win_score, 1),
        "盈亏比得分": round(plr_score, 1),
        "收益得分":   round(ret_score, 1),
        "回撤控制":   round(dd_score, 1),
        "总分":       round(total, 1),
    }


# ─── 打印报告 ─────────────────────────────────────────────────────

def print_report(stats: BacktestStats,
                 results: List[SearchResult],
                 query_stock: str = ""):
    print("\n" + "═" * 56)
    print(f"  形态相似性历史回测报告  {query_stock}")
    print("═" * 56)
    print(f"  检索到相似案例：{stats.total_cases} 个")
    print(f"  成功案例（≥10%）：{stats.success_cases} 个  "
          f"胜率: {stats.win_rate:.1%}")
    print(f"  均值收益: {stats.mean_return:+.2%}   "
          f"中位: {stats.median_return:+.2%}")
    print(f"  最大收益: {stats.max_return:+.2%}   "
          f"最小: {stats.min_return:+.2%}")
    print(f"  均值回撤: {stats.mean_drawdown:.2%}   "
          f"最大回撤: {stats.max_drawdown:.2%}")
    print(f"  盈亏比: {stats.profit_loss_ratio:.2f}x")
    print("─" * 56)
    print("  TOP 5 相似案例：")
    for i, r in enumerate(results[:5], 1):
        print(f"  {i}. {r.sample.stock_code} "
              f"{r.sample.end_date}  "
              f"相似度={r.combined_score:.3f}  "
              f"后验收益={r.sample.future_return:+.1%}")
    print("═" * 56 + "\n")


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

    from features.feature_engine import build_indicators, create_windows
    from similarity.retrieval import (
        build_library_from_windows, hybrid_search
    )

    np.random.seed(42)
    n = 400
    dates  = pd.date_range("2020-01-01", periods=n, freq="B")
    close  = pd.Series(30 * np.cumprod(1 + np.random.randn(n)*0.013),
                       index=dates)
    df = pd.DataFrame({
        "open":   close*0.99, "high": close*1.02,
        "low":    close*0.98, "close": close,
        "volume": np.random.randint(5e5, 3e6, n),
    })
    df_feat = build_indicators(df)
    wins    = create_windows(df_feat)
    lib     = build_library_from_windows(wins, "TEST.SZ")

    results = hybrid_search(wins[-1]["feature_df"], lib, top_k=20)
    stats   = compute_stats(results)
    print_report(stats, results, "TEST.SZ")

    scores  = compute_score(stats)
    print("评分:", scores)

    by_year = stats_by_year(results)
    print(by_year.to_string(index=False))
