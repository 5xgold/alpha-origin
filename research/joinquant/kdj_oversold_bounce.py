# -*- coding: utf-8 -*-
"""
聚宽研究环境脚本：KDJ 超卖反弹概率统计
========================================================
研究问题：
    全 A 股历史上，当 KDJ 的 J 值 < 阈值时买入，
    未来 3 个交易日内"恢复上涨"达标的概率是多少？
    - 大盘股达标线：+2%
    - 中小盘达标线：+3.95%

⚠️ 这是「研究环境」脚本（Jupyter Notebook / 研究 .py），
   不是 handle_data 回测策略。直接在聚宽研究环境逐段运行即可。

口径说明（可在 CONFIG 调整）：
    - J 阈值：J < J_THRESHOLD（KDJ 9 日，J = 3K - 2D）
    - 信号日：J 当日 < 阈值，且前一日 J >= 阈值（首次下穿，避免连续多日重复计数）
      → 设 FIRST_CROSS_ONLY = False 可改为"只要 J<阈值就算信号"
    - 买入价：信号日收盘价（T 日 close）
    - 达标判定：T+3 收盘价相对买入价涨幅 >= 达标线（实打实收上去）
      → BOUNCE_MODE = "close"       第 N 日收盘价相对买入价（当前口径）
      → BOUNCE_MODE = "high_touch"  改为 N 日内最高价盘中触及即算
    - 大小盘划分：按信号日总市值，>= CAP_SPLIT_YI 亿元 算大盘，否则中小盘
========================================================
"""

import pandas as pd
import numpy as np
# 注意：不要 from datetime import datetime —— jqdata 的 import * 会用模块覆盖该名字，
# 统一用 pd.Timestamp.now() 取当前日期，避开命名冲突。
from jqdata import *
from jqfactor import get_factor_values

# ============================================================
# CONFIG —— 所有可调参数集中在这里
# ============================================================
CONFIG = {
    # —— 统计区间 ——
    # 一次性按最大区间采集信号，再按 STAGES / 自然年分阶段汇总（避免重复拉数）
    "START_DATE": "2019-01-01",
    "END_DATE":   "2025-12-31",

    # —— 市场阶段划分（按信号日归入对应阶段，名称+起止；可自由增删/改名）——
    # 用于对比不同市场环境下策略效果差异。区间可重叠，落在多个阶段的样本会各自计入。
    "STAGES": [
        ("2019牛市启动",   "2019-01-01", "2019-04-30"),
        ("2020疫情V反弹",  "2020-02-01", "2020-07-31"),
        ("2021结构牛",     "2021-01-01", "2021-12-31"),
        ("2022熊市",       "2022-01-01", "2022-10-31"),
        ("2023震荡",       "2023-01-01", "2023-12-31"),
        ("2024超跌反弹",   "2024-01-01", "2024-12-31"),
        ("2025",          "2025-01-01", "2025-12-31"),
    ],
    "ALSO_BY_YEAR": True,   # 额外按自然年再汇总一份

    # —— 按市场温度自动分段（客观，免手工填日期）——
    # 用基准指数相对年线(MA)的位置+斜率，把每个信号日归入 牛/熊/震荡
    "AUTO_REGIME": {
        "ENABLE": True,
        "INDEX": "000300.XSHG",  # 判定大盘温度的基准（沪深300）
        "MA": 250,               # 年线周期
        "SLOPE_DAYS": 20,        # 用 MA 近 N 日斜率判定方向
        # 牛: close>MA 且 MA 上行; 熊: close<MA 且 MA 下行; 其余=震荡
    },

    # —— KDJ 参数 ——
    "KDJ_N": 9,          # RSV 窗口
    "KDJ_K_SMOOTH": 3,   # K 平滑（EMA alpha=1/3）
    "KDJ_D_SMOOTH": 3,   # D 平滑（EMA alpha=1/3）
    "J_THRESHOLD": 13,   # J < x 触发信号（放宽到 13，配合趋势/MACD 过滤补回样本）

    # —— 信号去重 ——
    "FIRST_CROSS_ONLY": True,  # True=只取"首次下穿阈值"那天；False=每个 J<阈值的交易日都算

    # —— 趋势过滤（叠加在 J<阈值 之上，滤掉下跌趋势中的假超卖）——
    "TREND_FILTER": True,        # True=要求多头排列 + MA20 未拐头向下
    "TREND_MA_FAST": 20,         # 短均线
    "TREND_MA_SLOW": 60,         # 长均线，要求 MA_FAST > MA_SLOW
    "MA20_DOWN_STREAK": 2,       # MA20 连续 >= N 天下行才算"拐头向下"（排除），单天微跌不算

    # —— 对照开关 ——
    # True：一次跑出「纯 J<阈值」vs「J<阈值+趋势+MACD 过滤」并排对比，量化过滤增益
    #       此时基础信号=纯 J 首次下穿，过滤条件转为每条记录的 pass 标记（同批候选的子集，对比公平）
    # False：过滤条件直接并入信号（只产出过滤后一组）
    "COMPARE_MODE": True,

    # —— MACD 过滤（叠加在 J<阈值 之上）——
    "MACD_FILTER": True,         # True=要求 DIF 快线 > 0（零轴上方，中期偏多）
    "MACD_FAST": 12,
    "MACD_SLOW": 26,
    "MACD_SIGNAL": 9,

    # —— 反弹达标口径 ——
    "FORWARD_DAYS": 3,            # 未来观察 N 个交易日
    "BOUNCE_MODE": "close",       # close=第N日收盘价实打实收上去; high_touch=N日内最高价触及
    "TARGET_BIG":   0.02,         # 大盘股达标线 +2%
    "TARGET_SMALL": 0.0395,       # 中小盘达标线 +3.95%

    # —— 大小盘划分 ——
    "CAP_SPLIT_YI": 500,   # 总市值 >= 500 亿 算大盘股（单位：亿元）

    # —— 股票池过滤 ——
    "EXCLUDE_ST": True,        # 剔除 ST/*ST
    "EXCLUDE_PAUSED": True,    # 剔除停牌
    "EXCLUDE_NEW_DAYS": 120,   # 剔除上市不足 N 个自然日的次新股
    "EXCLUDE_LIMIT_UP_ENTRY": True,  # 信号日涨停则剔除（买不进）

    # —— 性能 ——
    "SAMPLE_STOCKS": None,  # None=全市场；填整数则随机抽样 N 只快速验证脚本逻辑
    "RANDOM_SEED": 42,
}

# 一年约 244 个交易日；预留 KDJ 预热 + 未来窗口
WARMUP_DAYS = CONFIG["KDJ_N"] + 30


# ============================================================
# KDJ 计算 —— 与本地 feature_engine.calc_kdj 完全一致
# ============================================================
def calc_kdj(high, low, close, n=9):
    """KDJ（9日），K/D 用 EMA alpha=1/3，J = 3K - 2D。返回 DataFrame[k,d,j]。"""
    low_min = low.rolling(n).min()
    high_max = high.rolling(n).max()
    rsv = (close - low_min) / (high_max - low_min + 1e-9) * 100
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    return pd.DataFrame({"k": k, "d": d, "j": j}, index=close.index)


# ============================================================
# 主统计流程
# ============================================================
def run_study(cfg=CONFIG):
    start = cfg["START_DATE"]
    end = cfg["END_DATE"]
    fwd = cfg["FORWARD_DAYS"]

    print("=" * 60)
    print("KDJ 超卖反弹概率统计")
    print(f"区间: {start} ~ {end}  |  J < {cfg['J_THRESHOLD']}  |  未来 {fwd} 日")
    print(f"达标: 大盘 +{cfg['TARGET_BIG']*100:.2f}% / 中小盘 +{cfg['TARGET_SMALL']*100:.2f}%")
    print(f"反弹口径: {cfg['BOUNCE_MODE']}  |  大盘分界: 总市值 >= {cfg['CAP_SPLIT_YI']} 亿")
    print("=" * 60)

    # 交易日历（含 warmup 预热段，保证信号日 KDJ 已收敛）
    all_days = get_trade_days(
        start_date=pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS * 2),
        end_date=end,
    )
    all_days = [pd.Timestamp(d) for d in all_days]
    # 信号日有效区间：需保证未来还有 fwd 天可观察 → 末尾留出 fwd 天
    signal_days_full = [d for d in all_days if pd.Timestamp(start) <= d <= pd.Timestamp(end)]

    # 全市场股票池（取区间末的全部 A 股，再按上市/退市日期逐票过滤）
    stocks = get_all_securities(types=["stock"], date=end).index.tolist()

    if cfg["SAMPLE_STOCKS"]:
        rng = np.random.RandomState(cfg["RANDOM_SEED"])
        stocks = list(rng.choice(stocks, size=min(cfg["SAMPLE_STOCKS"], len(stocks)), replace=False))
        print(f"⚡ 抽样模式：随机 {len(stocks)} 只股票")

    print(f"候选股票数: {len(stocks)}")

    records = []  # 每条 = 一个信号样本
    info_all = get_all_securities(types=["stock"], date=end)

    # 诊断计数：定位"0 信号"到底卡在哪一步
    diag = {
        "err": 0, "no_price": 0, "short_price": 0, "below_count": 0,
        "after_trend": 0, "after_macd": 0, "raw_signal": 0, "kept": 0,
    }
    err_samples = []  # 前几条异常详情

    for i, code in enumerate(stocks):
        if i % 200 == 0:
            print(f"  进度 {i}/{len(stocks)} ... 已采集信号 {len(records)}")
        try:
            recs = _process_one_stock(code, start, end, fwd, info_all, cfg, diag)
            records.extend(recs)
        except Exception as e:
            diag["err"] += 1
            if len(err_samples) < 5:
                import traceback
                err_samples.append(f"{code}: {type(e).__name__}: {e}")
            continue

    # 诊断输出：无论有没有信号都打印，方便定位
    print("\n--- 采集诊断 ---")
    print(f"  候选股票: {len(stocks)} | 异常: {diag['err']} | 无行情: {diag['no_price']} "
          f"| 行情过短: {diag['short_price']}")
    print(f"  J<阈值的交易日数: {diag['below_count']} | 趋势过滤后: {diag['after_trend']} "
          f"| MACD过滤后: {diag['after_macd']} | 原始信号(去重后): {diag['raw_signal']} "
          f"| 过滤后保留: {diag['kept']}")
    if err_samples:
        print("  异常样例:")
        for s in err_samples:
            print(f"    - {s}")

    if not records:
        print("\n⚠️ 未采集到任何信号样本。对照上面诊断逐项排查：")
        print("   · 异常数高 → 多半是 get_price 字段/返回结构问题，开 DEBUG_FIRST_N 看详情")
        print("   · J<阈值交易日数=0 → KDJ 没算出或阈值太严，试 J_THRESHOLD 放宽")
        print("   · 原始信号>0 但保留=0 → 被次新/停牌/涨停/区间末过滤掉了")
        return None, None, None

    df = pd.DataFrame(records)
    summary = _summarize(df, cfg)
    stage_summary = _summarize_by_stage(df, cfg)
    return df, summary, stage_summary


def _process_one_stock(code, start, end, fwd, info_all, cfg, diag=None):
    """处理单只股票，返回该股所有信号样本记录列表。"""
    if diag is None:
        diag = {}

    def _bump(k, n=1):
        diag[k] = diag.get(k, 0) + n

    # 上市/退市过滤
    info = info_all.loc[code] if code in info_all.index else None
    if info is None:
        return []
    start_date_listed = pd.Timestamp(info["start_date"])
    end_date_delisted = pd.Timestamp(info["end_date"])

    # 取价格：前复权日线，含 warmup + 未来 fwd 天缓冲
    price_start = pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS * 2)
    price_end = pd.Timestamp(end) + pd.Timedelta(days=fwd * 3 + 15)  # 多取，覆盖未来窗口
    price_end = min(price_end, pd.Timestamp.now().normalize())

    df = get_price(
        code,
        start_date=price_start,
        end_date=price_end,
        frequency="daily",
        fields=["open", "high", "low", "close", "paused", "high_limit"],
        skip_paused=False,
        fq="pre",
    )
    if df is None or len(df) == 0:
        _bump("no_price")
        return []
    if len(df) < WARMUP_DAYS + fwd:
        _bump("short_price")
        return []

    df = df.dropna(subset=["close"])
    if df.empty:
        _bump("no_price")
        return []

    # 字段兜底：研究环境个别票可能不返回 high_limit/paused 列
    if "high_limit" not in df.columns:
        df["high_limit"] = np.nan
    if "paused" not in df.columns:
        df["paused"] = 0

    # KDJ
    kdj = calc_kdj(df["high"], df["low"], df["close"], n=cfg["KDJ_N"])
    df = df.join(kdj)

    # 信号判定列
    j = df["j"]
    below = j < cfg["J_THRESHOLD"]
    _bump("below_count", int(below.sum()))

    # 趋势条件序列：MA_FAST > MA_SLOW（多头排列）且 MA_FAST 未"拐头向下"
    # 拐头向下 = MA_FAST 连续 N 天下行（最近 N 个日差全 < 0），单天微跌不算
    if cfg.get("TREND_FILTER"):
        ma_fast = df["close"].rolling(cfg["TREND_MA_FAST"]).mean()
        ma_slow = df["close"].rolling(cfg["TREND_MA_SLOW"]).mean()
        streak = int(cfg.get("MA20_DOWN_STREAK", 2))
        turned_down = (ma_fast.diff() < 0).rolling(streak).sum() >= streak
        trend_ok = ((ma_fast > ma_slow) & (~turned_down)).fillna(False)
    else:
        trend_ok = pd.Series(True, index=df.index)

    # MACD 条件序列：DIF 快线 > 0（零轴上方）
    if cfg.get("MACD_FILTER"):
        ema_fast = df["close"].ewm(span=cfg["MACD_FAST"], adjust=False).mean()
        ema_slow = df["close"].ewm(span=cfg["MACD_SLOW"], adjust=False).mean()
        macd_ok = ((ema_fast - ema_slow) > 0).fillna(False)
    else:
        macd_ok = pd.Series(True, index=df.index)

    compare = cfg.get("COMPARE_MODE")
    if compare:
        # 对照模式：基础信号 = 纯 J<阈值 首次下穿；过滤条件转为逐记录标记
        base = below
    else:
        # 非对照：过滤条件直接并入信号
        base = below & trend_ok
        _bump("after_trend", int((below & trend_ok).sum()))
        base = base & macd_ok
        _bump("after_macd", int(base.sum()))

    if cfg["FIRST_CROSS_ONLY"]:
        signal = base & (~base.shift(1).fillna(False))  # 首次下穿
    else:
        signal = base

    dates = pd.to_datetime(df.index)
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    recs = []
    for idx in np.where(signal.values)[0]:
        sig_date = dates[idx]
        # 只统计在目标区间内的信号日
        if not (start_ts <= sig_date <= end_ts):
            continue
        _bump("raw_signal")
        # 上市满 N 天
        if (sig_date - start_date_listed).days < cfg["EXCLUDE_NEW_DAYS"]:
            continue
        if sig_date >= end_date_delisted:
            continue
        # 未来要有 fwd 个交易日
        if idx + fwd >= len(df):
            continue

        row = df.iloc[idx]
        # 停牌剔除
        if cfg["EXCLUDE_PAUSED"] and row.get("paused", 0) == 1:
            continue
        # 涨停剔除（买不进）
        if cfg["EXCLUDE_LIMIT_UP_ENTRY"] and not pd.isna(row.get("high_limit", np.nan)):
            if row["close"] >= row["high_limit"] - 1e-6:
                continue

        entry = row["close"]
        if entry <= 0:
            continue

        fut = df.iloc[idx + 1: idx + 1 + fwd]
        if cfg["BOUNCE_MODE"] == "high_touch":
            best_ret = (fut["high"].max() - entry) / entry
        else:  # close: 第 fwd 日收盘
            best_ret = (fut["close"].iloc[-1] - entry) / entry

        rec = {
            "code": code,
            "signal_date": sig_date,
            "j_value": float(row["j"]),
            "entry_close": float(entry),
            "best_ret": float(best_ret),
        }
        if compare:
            # 逐记录标记是否通过各过滤（用于并排对比）
            pt = bool(trend_ok.iloc[idx])
            pm = bool(macd_ok.iloc[idx])
            rec["pass_trend"] = pt
            rec["pass_macd"] = pm
            rec["pass_all"] = pt and pm
        recs.append(rec)

    if not recs:
        return []

    _bump("kept", len(recs))
    # 批量补市值（信号日总市值，单位亿元）→ 用于大小盘划分
    recs = _attach_market_cap(code, recs, cfg)
    return recs


def _attach_market_cap(code, recs, cfg):
    """为每条信号记录补信号日总市值（亿元），并判定大小盘 + 达标。"""
    sig_dates = sorted({r["signal_date"] for r in recs})
    cap_map = {}
    for d in sig_dates:
        try:
            q = query(valuation.market_cap).filter(valuation.code == code)
            vdf = get_fundamentals(q, date=d.strftime("%Y-%m-%d"))
            # market_cap 单位：亿元
            cap_map[d] = float(vdf["market_cap"].iloc[0]) if len(vdf) else np.nan
        except Exception:
            cap_map[d] = np.nan

    out = []
    for r in recs:
        cap = cap_map.get(r["signal_date"], np.nan)
        is_big = (not np.isnan(cap)) and cap >= cfg["CAP_SPLIT_YI"]
        target = cfg["TARGET_BIG"] if is_big else cfg["TARGET_SMALL"]
        r["market_cap_yi"] = cap
        r["is_big_cap"] = bool(is_big)
        r["target"] = target
        r["hit"] = bool(r["best_ret"] >= target)
        out.append(r)
    return out


def _stat_row(sub, label, cfg):
    """单组统计：样本数/达标数/概率/平均涨幅/中位数。"""
    n = len(sub)
    if n == 0:
        return {"分组": label, "样本数": 0, "达标数": 0, "概率": np.nan,
                "平均最佳涨幅": np.nan, "最佳涨幅中位数": np.nan}
    return {
        "分组": label,
        "样本数": n,
        "达标数": int(sub["hit"].sum()),
        "概率": round(sub["hit"].mean() * 100, 2),
        "平均最佳涨幅": round(sub["best_ret"].mean() * 100, 2),
        "最佳涨幅中位数": round(sub["best_ret"].median() * 100, 2),
    }


def _three_group_rows(df, cfg, prefix=""):
    """对一个样本集，产出 整体/大盘/中小盘 三行。prefix 用于阶段标签前缀。"""
    valid = df.dropna(subset=["market_cap_yi"])
    big = f"大盘≥{cfg['CAP_SPLIT_YI']}亿+{cfg['TARGET_BIG']*100:.2f}%"
    small = f"中小盘+{cfg['TARGET_SMALL']*100:.2f}%"
    return [
        _stat_row(df, f"{prefix}整体", cfg),
        _stat_row(valid[valid["is_big_cap"]], f"{prefix}{big}", cfg),
        _stat_row(valid[~valid["is_big_cap"]], f"{prefix}{small}", cfg),
    ]


def _summarize(df, cfg):
    """全区间汇总概率：整体 / 大盘 / 中小盘。对照模式额外并排对比纯信号 vs 过滤后。"""
    summary = pd.DataFrame(_three_group_rows(df, cfg))
    valid = df.dropna(subset=["market_cap_yi"])
    print("\n" + "=" * 60)
    print("全区间统计结果（全部信号）")
    print("=" * 60)
    print(summary.to_string(index=False))
    print("\n样本总数:", len(df), "| 缺市值样本:", len(df) - len(valid))

    if cfg.get("COMPARE_MODE") and "pass_all" in df.columns:
        _summarize_compare(df, cfg)
    return summary


def _summarize_compare(df, cfg):
    """对照：纯 J<阈值 vs +趋势 vs +MACD vs +全部过滤，并排对比达标率增益。"""
    variants = [
        ("纯J<%d" % cfg["J_THRESHOLD"], df),
        ("+趋势过滤", df[df["pass_trend"]]),
        ("+MACD过滤", df[df["pass_macd"]]),
        ("+趋势+MACD", df[df["pass_all"]]),
    ]
    rows = []
    for name, sub in variants:
        valid = sub.dropna(subset=["market_cap_yi"])
        for grp_label, grp in [
            ("整体", sub),
            ("大盘", valid[valid["is_big_cap"]]),
            ("中小盘", valid[~valid["is_big_cap"]]),
        ]:
            r = _stat_row(grp, f"{name}·{grp_label}", cfg)
            rows.append(r)
    cmp_df = pd.DataFrame(rows)
    print("\n" + "=" * 60)
    print("对照：过滤条件增益（纯信号 → 逐层过滤）")
    print("=" * 60)
    print(cmp_df.to_string(index=False))

    # 整体一行的横向对比 + 相对纯信号的增益
    overall = cmp_df[cmp_df["分组"].str.endswith("整体")].copy()
    base_prob = overall.iloc[0]["概率"]
    overall["较纯信号增益(pp)"] = (overall["概率"] - base_prob).round(2)
    overall["变体"] = overall["分组"].str.replace("·整体", "", regex=False)
    print("\n--- 整体达标率对照（pp=百分点）---")
    print(overall[["变体", "样本数", "概率", "较纯信号增益(pp)", "平均最佳涨幅"]].to_string(index=False))
    return cmp_df


def _build_regime_series(cfg):
    """用基准指数算每个交易日的市场温度标签（牛/熊/震荡）。

    返回 pd.Series(index=日期, value=标签)；失败返回 None。
    判定：close>MA 且 MA 上行 → 牛；close<MA 且 MA 下行 → 熊；其余 → 震荡。
    """
    rc = cfg.get("AUTO_REGIME", {})
    if not rc.get("ENABLE"):
        return None
    ma_n = rc["MA"]
    # 指数行情要往前多取 MA 周期做预热
    idx_start = pd.Timestamp(cfg["START_DATE"]) - pd.Timedelta(days=int(ma_n * 2.5))
    try:
        idf = get_price(
            rc["INDEX"], start_date=idx_start, end_date=cfg["END_DATE"],
            frequency="daily", fields=["close"], fq="pre",
        )
    except Exception as e:
        print(f"⚠️ 自动分段：取基准 {rc['INDEX']} 失败，跳过 regime 分组：{e}")
        return None
    if idf is None or len(idf) < ma_n + rc["SLOPE_DAYS"]:
        print("⚠️ 自动分段：基准数据不足，跳过 regime 分组")
        return None

    close = idf["close"]
    ma = close.rolling(ma_n).mean()
    ma_slope = ma - ma.shift(rc["SLOPE_DAYS"])  # >0 上行
    label = pd.Series("震荡", index=close.index)
    label[(close > ma) & (ma_slope > 0)] = "牛市"
    label[(close < ma) & (ma_slope < 0)] = "熊市"
    label[ma.isna()] = np.nan  # 预热不足的日子不判定
    label.index = pd.to_datetime(label.index).normalize()
    return label


def _summarize_by_stage(df, cfg):
    """按市场阶段（手工 STAGES + 可选自然年 + 可选自动温度）分组汇总。"""
    sig = pd.to_datetime(df["signal_date"])

    # 收集 (阶段名, 子集) —— 阶段区间可重叠，样本各自计入
    buckets = []
    for name, s, e in cfg.get("STAGES", []):
        mask = (sig >= pd.Timestamp(s)) & (sig <= pd.Timestamp(e))
        buckets.append((name, df[mask]))

    if cfg.get("ALSO_BY_YEAR"):
        for y in sorted(sig.dt.year.unique()):
            buckets.append((f"{y}年", df[sig.dt.year == y]))

    # 自动市场温度分段：把每个信号日映射到 牛/熊/震荡
    regime = _build_regime_series(cfg)
    if regime is not None:
        sig_norm = sig.dt.normalize()
        sig_regime = sig_norm.map(regime)  # 每条样本的温度标签
        for tag in ["牛市", "震荡", "熊市"]:
            sub = df[sig_regime.values == tag]
            buckets.append((f"温度·{tag}", sub))

    rows = []
    for name, sub in buckets:
        # 每个阶段给出 整体/大盘/中小盘 三行，阶段名作前缀分隔
        for r in _three_group_rows(sub, cfg, prefix=f"[{name}] "):
            rows.append(r)

    stage_summary = pd.DataFrame(rows)
    print("\n" + "=" * 60)
    print("分阶段统计结果（不同市场环境对比）")
    print("=" * 60)
    print(stage_summary.to_string(index=False))

    # 额外打印一张"整体"行的横向对比表，最直观看趋势
    overall = stage_summary[stage_summary["分组"].str.endswith("整体")].copy()
    overall["阶段"] = overall["分组"].str.replace(r"^\[|\] 整体$", "", regex=True)
    pivot = overall[["阶段", "样本数", "概率", "平均最佳涨幅"]]
    print("\n--- 各阶段【整体】达标概率横向对比 ---")
    print(pivot.to_string(index=False))
    return stage_summary


# ============================================================
# 单票调试 —— "没有捕捉到信号"时第一步先跑这个
# ============================================================
def debug_one(code="000001.XSHE", cfg=CONFIG):
    """对单只股票完整跑一遍并打印每一步，异常直接抛出（不吞）。

    用法（聚宽研究环境）：
        debug_one("000001.XSHE")
    能定位到底是 get_price 报错、KDJ 没值、还是阈值/过滤的问题。
    """
    start, end, fwd = cfg["START_DATE"], cfg["END_DATE"], cfg["FORWARD_DAYS"]
    print(f"=== 调试 {code} | 区间 {start}~{end} | J<{cfg['J_THRESHOLD']} ===")

    # 1) 取价格（不 try，让真实错误暴露）
    price_start = pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS * 2)
    price_end = min(pd.Timestamp(end) + pd.Timedelta(days=fwd * 3 + 15),
                    pd.Timestamp.now().normalize())
    df = get_price(code, start_date=price_start, end_date=price_end,
                   frequency="daily",
                   fields=["open", "high", "low", "close", "paused", "high_limit"],
                   skip_paused=False, fq="pre")
    print(f"[1] get_price 行数={0 if df is None else len(df)}, "
          f"列={list(df.columns) if df is not None else None}")
    if df is None or df.empty:
        print("    → get_price 返回空，多半是代码格式/权限/区间问题")
        return

    df = df.dropna(subset=["close"])
    kdj = calc_kdj(df["high"], df["low"], df["close"], n=cfg["KDJ_N"])
    df = df.join(kdj)
    print(f"[2] KDJ 计算完成，J 非空={df['j'].notna().sum()}, "
          f"J 最小值={df['j'].min():.2f}, J 最大值={df['j'].max():.2f}")

    below = df["j"] < cfg["J_THRESHOLD"]
    print(f"[3] J<{cfg['J_THRESHOLD']} 的交易日数={int(below.sum())}")

    if cfg.get("TREND_FILTER"):
        ma_fast = df["close"].rolling(cfg["TREND_MA_FAST"]).mean()
        ma_slow = df["close"].rolling(cfg["TREND_MA_SLOW"]).mean()
        streak = int(cfg.get("MA20_DOWN_STREAK", 2))
        turned_down = (ma_fast.diff() < 0).rolling(streak).sum() >= streak
        trend_ok = ((ma_fast > ma_slow) & (~turned_down)).fillna(False)
        below = below & trend_ok
        print(f"    叠加趋势过滤(MA{cfg['TREND_MA_FAST']}>MA{cfg['TREND_MA_SLOW']} 且 MA{cfg['TREND_MA_FAST']}未连跌{streak}天)后={int(below.sum())}")

    if cfg.get("MACD_FILTER"):
        ema_fast = df["close"].ewm(span=cfg["MACD_FAST"], adjust=False).mean()
        ema_slow = df["close"].ewm(span=cfg["MACD_SLOW"], adjust=False).mean()
        dif = ema_fast - ema_slow
        below = below & (dif > 0).fillna(False)
        print(f"    叠加 MACD 过滤(DIF>0)后={int(below.sum())}")

    if cfg["FIRST_CROSS_ONLY"]:
        signal = below & (~below.shift(1).fillna(False))
        print(f"    首次下穿(去重后)信号数={int(signal.sum())}")
    else:
        signal = below
    in_range = (pd.to_datetime(df.index) >= pd.Timestamp(start)) & \
               (pd.to_datetime(df.index) <= pd.Timestamp(end))
    print(f"[4] 落在统计区间内的信号数={int((signal & in_range).sum())}")

    # 完整跑一遍正式逻辑
    info_all = get_all_securities(types=["stock"], date=end)
    diag = {}
    recs = _process_one_stock(code, start, end, fwd, info_all, cfg, diag)
    print(f"[5] 正式流程保留样本数={len(recs)}")
    if recs:
        print("    样例:", recs[0])
    print("[diag]", diag)
    return recs


# ============================================================
# 执行入口
# ============================================================
# ⚠️ "没有捕捉到信号"先跑单票调试，看卡在哪一步：
#   debug_one("000001.XSHE")
#
# 在聚宽研究环境中，直接运行下面这行即可（返回 3 个对象）：
#   df, summary, stage_summary = run_study()
#     df            = 每条信号样本明细（含 signal_date，可自行再切片）
#     summary       = 全区间 整体/大盘/中小盘 汇总
#     stage_summary = 各市场阶段（+自然年）分组汇总，用于对比不同行情下的效果
#
# 对照模式（默认开）：COMPARE_MODE=True 时，summary 会额外打印
#   「纯J<阈值 → +趋势 → +MACD → +全部过滤」并排达标率与增益(pp)，
#   量化过滤条件到底提升了多少。关掉则只产出过滤后一组。
#
# 先用抽样验证脚本逻辑（快）：
#   CONFIG["SAMPLE_STOCKS"] = 100
#   df, summary, stage_summary = run_study()
#
# 验证无误后跑全市场：
#   CONFIG["SAMPLE_STOCKS"] = None
#   df, summary, stage_summary = run_study()
#
# 自定义市场阶段（看不同阶段策略效果差异）：
#   CONFIG["STAGES"] = [("我的牛市","2024-09-24","2024-10-08"), ("随后回调","2024-10-09","2024-12-31")]
#   df, summary, stage_summary = run_study()
#
# 切换 J 阈值做敏感性分析：
#   for x in [10, 0, -5, -10]:
#       CONFIG["J_THRESHOLD"] = x
#       run_study()

if __name__ == "__main__":
    df, summary, stage_summary = run_study()
