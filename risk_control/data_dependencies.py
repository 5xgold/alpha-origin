"""风控数据依赖生成、本地缓存检查与缺口补数。

默认只读取 portfolio.toml 与本地 cache；显式传入 --fetch-missing 时才访问外部行情源。
用途是在跑风控前明确：策略依赖什么数据、本地缺什么、是否需要补缺口。
"""

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))

from risk_control.config import ATR_PERIOD, MARKET_INDEX, PORTFOLIO_LOOKBACK_DAYS
from risk_control.agent_price_cache import read_cached_series as read_agent_price_series
from shared.config import CACHE_DIR, parse_benchmark_config
from shared.data_provider import (
    baostock_session,
    get_benchmark_prices,
    get_stock_prices,
    latest_baostock_available_date,
)
from shared.portfolio_config import load_portfolio_from_toml


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PORTFOLIO_TOML = ROOT_DIR / "portfolio.toml"
OUTPUT_DIR = ROOT_DIR / "output"
AGENT_PRICE_CACHE_DIR = Path(CACHE_DIR) / "agent_prices"

HOLDING_LOOKBACK_TRADING_DAYS = max(PORTFOLIO_LOOKBACK_DAYS, 60)
HOLDING_LOOKBACK_CALENDAR_DAYS = 120
INDEX_LOOKBACK_TRADING_DAYS = 20
INDEX_LOOKBACK_CALENDAR_DAYS = 60


def normalize_review_date(date_str=None):
    if not date_str:
        return latest_baostock_available_date()
    digits = str(date_str).replace("-", "").strip()
    if len(digits) != 8 or not digits.isdigit():
        raise ValueError("日期格式必须为 YYYYMMDD 或 YYYY-MM-DD")
    return digits


def _fmt_date(value):
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _strategy_for(row):
    trade_plan = row.get("trade_plan", {})
    if isinstance(trade_plan, dict):
        strategy = str(trade_plan.get("stop_loss_strategy", "") or "").strip()
        if strategy:
            return strategy
    risk_rules = row.get("risk_rules", {})
    if isinstance(risk_rules, dict):
        strategy = str(risk_rules.get("stop_loss_strategy", "") or "").strip()
        if strategy:
            return strategy
    return "atr"


def _empty_prices_frame():
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


def _load_agent_payloads(review_date):
    payloads = []
    if not AGENT_PRICE_CACHE_DIR.exists():
        return payloads
    candidates = [
        AGENT_PRICE_CACHE_DIR / f"{review_date}.json",
        AGENT_PRICE_CACHE_DIR / f"risk_prices_{review_date}.json",
    ]
    candidates.extend(sorted(AGENT_PRICE_CACHE_DIR.glob("*.json")))
    seen = set()
    for path in candidates:
        if path in seen or not path.exists():
            continue
        seen.add(path)
        try:
            payloads.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return payloads


def _rows_to_frame(rows):
    if not rows:
        return _empty_prices_frame()
    df = pd.DataFrame(rows)
    if "date" not in df.columns:
        return _empty_prices_frame()
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").drop_duplicates(subset=["date"])
    return df.reset_index(drop=True)


def _agent_series(code, review_date, series_key):
    frames = []
    sources = []
    cached = read_agent_price_series(series_key, code)
    if cached is not None and not cached.empty:
        frames.append(_rows_to_frame(cached.to_dict(orient="records")))
        sources.append(str(AGENT_PRICE_CACHE_DIR / series_key / f"{str(code).replace('.', '_')}.csv"))
    for path, payload in _load_agent_payloads(review_date):
        rows = payload.get(series_key, {}).get(code, [])
        if rows:
            frames.append(_rows_to_frame(rows))
            sources.append(str(path))
    if not frames:
        return _empty_prices_frame(), []
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["date"]).sort_values("date").drop_duplicates(subset=["date"])
    return df.reset_index(drop=True), sources


def _stocks_cache_series(code):
    frames = []
    stocks_dir = Path(CACHE_DIR) / "stocks"
    if not stocks_dir.exists():
        return _empty_prices_frame(), []
    code_str = str(code).strip()
    patterns = [
        f"{code_str}_*_*_*.csv",
    ]
    if code_str.isdigit() and len(code_str) < 6:
        patterns.append(f"{code_str.zfill(6)}_*_*_*.csv")
    sources = []
    for pattern in patterns:
        for path in stocks_dir.glob(pattern):
            try:
                df = pd.read_csv(path, parse_dates=["date"])
            except Exception:
                continue
            frames.append(df)
            sources.append(str(path))
    if not frames:
        return _empty_prices_frame(), []
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["date"]).sort_values("date").drop_duplicates(subset=["date"])
    return df.reset_index(drop=True), sorted(set(sources))


def _benchmark_cache_series(code):
    frames = []
    code_key = str(code).replace(".", "_")
    paths = []
    benchmark_series = Path(CACHE_DIR) / "benchmarks" / f"{code_key}.csv"
    if benchmark_series.exists():
        paths.append(benchmark_series)
    paths.extend(Path(CACHE_DIR).glob(f"benchmark_{code_key}_*.csv"))
    paths.extend(Path(CACHE_DIR).glob(f"benchmark_{code}_*.csv"))
    sources = []
    for path in paths:
        try:
            df = pd.read_csv(path, parse_dates=["date"])
        except Exception:
            continue
        frames.append(df)
        sources.append(str(path))
    if not frames:
        return _empty_prices_frame(), []
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["date"]).sort_values("date").drop_duplicates(subset=["date"])
    return df.reset_index(drop=True), sorted(set(sources))


def _combined_series(code, review_date, series_key):
    frames = []
    sources = []
    agent_df, agent_sources = _agent_series(code, review_date, series_key)
    if not agent_df.empty:
        frames.append(agent_df)
        sources.extend(agent_sources)
    if series_key == "prices":
        stock_df, stock_sources = _stocks_cache_series(code)
        if not stock_df.empty:
            frames.append(stock_df)
            sources.extend(stock_sources)
    if series_key == "indices":
        benchmark_df, benchmark_sources = _benchmark_cache_series(code)
        if not benchmark_df.empty:
            frames.append(benchmark_df)
            sources.extend(benchmark_sources)
    if not frames:
        return _empty_prices_frame(), []
    df = pd.concat(frames, ignore_index=True)
    df = df.dropna(subset=["date"]).sort_values("date").drop_duplicates(subset=["date"])
    return df.reset_index(drop=True), sorted(set(sources))


def _coverage_status(
    df,
    review_date,
    min_rows,
    required_fields,
    start_date=None,
    exact_date=None,
    allow_stale_calendar_days=0,
    max_internal_gap_calendar_days=14,
):
    ed = pd.to_datetime(review_date)
    local = df.copy()
    if local.empty:
        return {
            "ready": False,
            "available_rows": 0,
            "latest_date": "",
            "missing_fields": required_fields,
            "reason": "no_cache",
            "data_status": "missing",
            "as_of_date": "",
            "stale_days": None,
        }
    local = local[local["date"] <= ed]
    if start_date is not None:
        local = local[local["date"] >= pd.to_datetime(start_date)]
    if exact_date is not None:
        local = local[local["date"] == pd.to_datetime(exact_date)]
    missing_fields = [field for field in required_fields if field not in local.columns]
    latest_date = "" if local.empty else local["date"].max().strftime("%Y-%m-%d")
    local = local.sort_values("date").reset_index(drop=True)
    ready = not missing_fields and len(local) >= min_rows
    reason = ""
    data_status = "fresh"
    stale_days = None
    internal_gaps = []
    if exact_date is None and len(local) >= 2 and max_internal_gap_calendar_days:
        dates = list(local["date"].dropna().sort_values())
        for prev, current in zip(dates, dates[1:]):
            delta = current - prev
            days = int(delta.days)
            if days > max_internal_gap_calendar_days:
                internal_gaps.append({
                    "from": prev.strftime("%Y-%m-%d"),
                    "to": current.strftime("%Y-%m-%d"),
                    "calendar_days": days,
                })
    if missing_fields:
        reason = "missing_fields"
        data_status = "invalid"
    elif len(local) < min_rows:
        reason = "insufficient_rows"
        data_status = "insufficient"
    elif internal_gaps:
        reason = "date_gap"
        data_status = "gap"
        ready = False
    elif latest_date and latest_date.replace("-", "") < review_date and exact_date is None:
        latest_ts = pd.to_datetime(latest_date)
        stale_days = int((ed - latest_ts).days)
        if allow_stale_calendar_days and latest_ts >= ed - timedelta(days=allow_stale_calendar_days):
            reason = "stale_latest_date_accepted"
            data_status = "possibly_suspended"
        else:
            reason = "stale_latest_date"
            data_status = "stale"
            ready = False
    return {
        "ready": bool(ready),
        "available_rows": int(len(local)),
        "latest_date": latest_date,
        "missing_fields": missing_fields,
        "reason": reason,
        "data_status": data_status,
        "as_of_date": latest_date,
        "stale_days": stale_days,
        "has_gaps": bool(internal_gaps),
        "internal_gaps": internal_gaps,
    }


def _holding_requirement(row, review_date):
    strategy = _strategy_for(row)
    end = pd.to_datetime(review_date)
    start = (end - timedelta(days=HOLDING_LOOKBACK_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    code = str(row["code"])
    df, sources = _combined_series(code, review_date, "prices")
    status = _coverage_status(
        df,
        review_date,
        HOLDING_LOOKBACK_TRADING_DAYS,
        ["date", "open", "high", "low", "close", "volume"],
        start_date=start,
        allow_stale_calendar_days=10,
    )
    requirements = [{
        "series": "ohlcv_daily",
        "adjust": "qfq",
        "start_date": start,
        "end_date": _fmt_date(review_date),
        "lookback_trading_days": HOLDING_LOOKBACK_TRADING_DAYS,
        "fields": ["date", "open", "high", "low", "close", "volume"],
        "used_by": [
            "enrich_portfolio.current_price",
            "check_positions.current_position",
            "calc_stop_take_levels.atr_stop_take_trailing",
            "check_circuit_breaker.portfolio_drawdown",
            "detect_anomalies.vol_liquidity_correlation",
            "holding_period.ma20",
            "dynamic_stop_upgrade",
            "add_position.support_levels",
        ],
    }]
    if strategy == "entry_day_low_guard":
        buy_date = str(row.get("buy_date", "") or "").strip()
        entry_status = _coverage_status(
            df,
            review_date,
            1,
            ["date", "low"],
            exact_date=buy_date,
        )
        status["entry_day_low_ready"] = entry_status["ready"]
        if not entry_status["ready"]:
            status["ready"] = False
        requirements.append({
            "series": "entry_day_low",
            "date": _fmt_date(buy_date),
            "fields": ["date", "low"],
            "used_by": ["entry_day_low_guard.stop_loss"],
        })
    return {
        "code": code,
        "name": str(row["name"]),
        "market": str(row["market"]),
        "quantity": float(row["quantity"]),
        "cost_price": float(row["cost_price"]),
        "buy_date": str(row.get("buy_date", "") or ""),
        "stop_loss_strategy": strategy,
        "requirements": requirements,
        "cache_sources": sources,
        "status": status,
    }


def _index_requirement(component, review_date):
    end = pd.to_datetime(review_date)
    start = (end - timedelta(days=INDEX_LOOKBACK_CALENDAR_DAYS)).strftime("%Y-%m-%d")
    code = str(component["index"])
    df, sources = _combined_series(code, review_date, "indices")
    status = _coverage_status(
        df,
        review_date,
        INDEX_LOOKBACK_TRADING_DAYS,
        ["date", "close"],
        start_date=start,
    )
    return {
        "code": code,
        "weight": float(component["weight"]),
        "source_hint": component.get("source", ""),
        "requirements": [{
            "series": "index_close_daily",
            "start_date": start,
            "end_date": _fmt_date(review_date),
            "lookback_trading_days": INDEX_LOOKBACK_TRADING_DAYS,
            "fields": ["date", "close"],
            "used_by": ["check_positions.market_volatility"],
        }],
        "cache_sources": sources,
        "status": status,
    }


def build_data_requirements(review_date=None, portfolio_path=None):
    review_date = normalize_review_date(review_date)
    portfolio_df = load_portfolio_from_toml(str(portfolio_path or DEFAULT_PORTFOLIO_TOML))
    holdings = [_holding_requirement(row, review_date) for _, row in portfolio_df.iterrows()]
    indices = [_index_requirement(c, review_date) for c in parse_benchmark_config(MARKET_INDEX)]
    missing_holdings = [h for h in holdings if not h["status"]["ready"]]
    missing_indices = [idx for idx in indices if not idx["status"]["ready"]]
    ready = not missing_holdings and not missing_indices
    payload = {
        "review_date": _fmt_date(review_date),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ready": ready,
        "local_price_cache_paths": {
            "prices": str(AGENT_PRICE_CACHE_DIR / "prices" / "{code}.csv"),
            "indices": str(AGENT_PRICE_CACHE_DIR / "indices" / "{code}.csv"),
        },
        "incoming_price_cache_path": str(AGENT_PRICE_CACHE_DIR / "incoming" / f"{review_date}.json"),
        "minimum_policy": {
            "holding_ohlcv_trading_days": HOLDING_LOOKBACK_TRADING_DAYS,
            "holding_ohlcv_calendar_days": HOLDING_LOOKBACK_CALENDAR_DAYS,
            "market_index_trading_days": INDEX_LOOKBACK_TRADING_DAYS,
            "market_index_calendar_days": INDEX_LOOKBACK_CALENDAR_DAYS,
        },
        "holdings": holdings,
        "market_indices": indices,
        "missing": {
            "holdings": missing_holdings,
            "market_indices": missing_indices,
        },
        "backfill_instructions": [
            "先补 missing.holdings 与 missing.market_indices 中列出的数据，再执行风控。",
            "补数只写行情事实，不写止损、止盈、加减仓结论。",
            "默认用 baostock/Futu 等行情源补本地缺口；Ready=True 时不刷新历史行情。",
            "prices 每个 code 需要 date/open/high/low/close/volume，indices 每个 code 至少需要 date/close。",
            "prices 行情统一使用前复权 qfq；本地 cache 记录 adjust=qfq，旧数据缺 adjust 时按 qfq 兼容。",
            "停牌或无成交标的可使用最近有效交易日行情；此时 status.data_status=possibly_suspended，as_of_date 标明实际行情日期。",
        ],
    }
    return payload


def fetch_missing_data(payload):
    """按数据依赖清单补缺口。只在 payload 未 ready 时调用。"""
    fetched = {"holdings": [], "market_indices": [], "errors": []}
    if payload.get("ready"):
        return fetched

    try:
        with baostock_session():
            for item in payload.get("missing", {}).get("holdings", []):
                code = item["code"]
                for req in item.get("requirements", []):
                    if req.get("series") not in ("ohlcv_daily", "entry_day_low"):
                        continue
                    start = req.get("start_date") or req.get("date")
                    end = req.get("end_date") or req.get("date")
                    try:
                        df = get_stock_prices(code, start, end, adjust="qfq")
                        fetched["holdings"].append({
                            "code": code,
                            "series": req.get("series"),
                            "start_date": start,
                            "end_date": end,
                            "rows": 0 if df is None else int(len(df)),
                        })
                    except Exception as exc:
                        fetched["errors"].append({"code": code, "series": req.get("series"), "error": str(exc)})

            for item in payload.get("missing", {}).get("market_indices", []):
                code = item["code"]
                req = item.get("requirements", [{}])[0]
                start = req.get("start_date")
                end = req.get("end_date")
                try:
                    df = get_benchmark_prices(code, start, end)
                    fetched["market_indices"].append({
                        "code": code,
                        "series": req.get("series"),
                        "start_date": start,
                        "end_date": end,
                        "rows": 0 if df is None else int(len(df)),
                    })
                except Exception as exc:
                    fetched["errors"].append({"code": code, "series": req.get("series"), "error": str(exc)})
    except Exception as exc:
        fetched["errors"].append({"scope": "market_data_session", "error": str(exc)})
    return fetched


def main():
    parser = argparse.ArgumentParser(description="生成风控运行前的数据需求与本地 cache 缺口")
    parser.add_argument("--date", help="复盘/风控日期，格式 YYYYMMDD 或 YYYY-MM-DD")
    parser.add_argument("--portfolio", help="portfolio.toml 路径")
    parser.add_argument("--output", help="输出 JSON 路径，默认 output/risk_data_requirements_YYYYMMDD.json")
    parser.add_argument("--fetch-missing", action="store_true", help="仅当本地 cache 不满足依赖时，用行情源补缺口后重新检查")
    parser.add_argument("--strict", action="store_true", help="存在缺数时返回非 0，供 quickstart 阻止继续跑风控")
    args = parser.parse_args()

    review_date = normalize_review_date(args.date)
    payload = build_data_requirements(review_date, args.portfolio)
    if args.fetch_missing and not payload["ready"]:
        payload["backfill"] = fetch_missing_data(payload)
        payload = build_data_requirements(review_date, args.portfolio) | {"backfill": payload["backfill"]}
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = Path(args.output) if args.output else OUTPUT_DIR / f"risk_data_requirements_{review_date}.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"风控数据检查日期: {payload['review_date']}")
    print(f"Ready: {payload['ready']}")
    print(f"Requirements: {output}")
    print(f"Local price cache: {payload['local_price_cache_paths']['prices']}")
    if payload.get("backfill"):
        backfill = payload["backfill"]
        print(
            "Backfill: "
            f"持仓 {len(backfill.get('holdings', []))} 项, "
            f"市场指数 {len(backfill.get('market_indices', []))} 项, "
            f"错误 {len(backfill.get('errors', []))} 项"
        )
    if not payload["ready"]:
        print(
            "缺数: "
            f"持仓 {len(payload['missing']['holdings'])} 项, "
            f"市场指数 {len(payload['missing']['market_indices'])} 项"
        )
        if args.strict:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
