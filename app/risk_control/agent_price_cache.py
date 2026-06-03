"""外部行情补数增量缓存。

外部 JSON 只写 incoming；本模块负责校验、合并、去重、按日期倒序保存。
长期缓存:
- data/cache/agent_prices/prices/{code}.csv
- data/cache/agent_prices/indices/{code}.csv
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.append(str(Path(__file__).parent.parent))

from shared.config import CACHE_DIR


ROOT_DIR = Path(__file__).resolve().parent.parent.parent
OUTPUT_DIR = ROOT_DIR / "output"
AGENT_CACHE_DIR = Path(CACHE_DIR) / "agent_prices"
INCOMING_DIR = AGENT_CACHE_DIR / "incoming"
PRICES_DIR = AGENT_CACHE_DIR / "prices"
INDICES_DIR = AGENT_CACHE_DIR / "indices"
MERGE_REPORT_DIR = AGENT_CACHE_DIR / "merge_reports"

PRICE_REQUIRED_FIELDS = ["date", "open", "high", "low", "close", "volume"]
PRICE_FIELDS = PRICE_REQUIRED_FIELDS + ["adjust"]
INDEX_FIELDS = ["date", "close"]
DEFAULT_PRICE_ADJUST = "qfq"


def normalize_review_date(date_str=None):
    if not date_str:
        return datetime.now().strftime("%Y%m%d")
    digits = str(date_str).replace("-", "").strip()
    if len(digits) != 8 or not digits.isdigit():
        raise ValueError("日期格式必须为 YYYYMMDD 或 YYYY-MM-DD")
    return digits


def _safe_code(code):
    return str(code).strip().replace("/", "_").replace(".", "_")


def _series_path(section, code):
    directory = PRICES_DIR if section == "prices" else INDICES_DIR
    return directory / f"{_safe_code(code)}.csv"


def _empty_frame(fields):
    return pd.DataFrame(columns=fields)


def _normalize_adjust(value):
    text = str(value or DEFAULT_PRICE_ADJUST).strip().lower()
    if text in ("", "none", "raw", "no_adjust", "unadjusted", "3"):
        return "raw"
    if text in ("qfq", "forward", "forward_adjusted", "2"):
        return "qfq"
    if text in ("hfq", "backward", "backward_adjusted", "1"):
        return "hfq"
    return text


def _read_series(section, code):
    fields = PRICE_FIELDS if section == "prices" else INDEX_FIELDS
    path = _series_path(section, code)
    if not path.exists():
        return _empty_frame(fields)
    try:
        df = pd.read_csv(path, parse_dates=["date"])
    except Exception:
        return _empty_frame(fields)
    return _normalize_frame(df, section, strict=False)


def _write_series(section, code, df):
    path = _series_path(section, code)
    path.parent.mkdir(parents=True, exist_ok=True)
    local = df.copy()
    local["date"] = pd.to_datetime(local["date"], errors="coerce")
    local = local.dropna(subset=["date"]).sort_values("date", ascending=False)
    local["date"] = local["date"].dt.strftime("%Y-%m-%d")
    local.to_csv(path, index=False)
    return path


def _normalize_frame(df, section, strict=True):
    required = PRICE_REQUIRED_FIELDS if section == "prices" else INDEX_FIELDS
    output_fields = PRICE_FIELDS if section == "prices" else INDEX_FIELDS
    if df is None or df.empty:
        return _empty_frame(output_fields)
    missing = [field for field in required if field not in df.columns]
    if missing and strict:
        raise ValueError(f"{section} 缺少字段: {', '.join(missing)}")
    local = df.copy()
    for field in required:
        if field not in local.columns:
            local[field] = 0 if field == "volume" else pd.NA
    if section == "prices":
        if "adjust" not in local.columns:
            local["adjust"] = DEFAULT_PRICE_ADJUST
        local["adjust"] = local["adjust"].map(_normalize_adjust)
    local["date"] = pd.to_datetime(local["date"], errors="coerce")
    numeric_fields = [field for field in required if field != "date"]
    for field in numeric_fields:
        local[field] = pd.to_numeric(local[field], errors="coerce")
    local = local.dropna(subset=["date", "close"])
    if section == "prices":
        local = local.dropna(subset=["open", "high", "low"])
        local["volume"] = local["volume"].fillna(0)
        local = local[output_fields].drop_duplicates(subset=["date", "adjust"], keep="last")
    else:
        local = local[output_fields].drop_duplicates(subset=["date"], keep="last")
    return local.sort_values("date").reset_index(drop=True)


def read_cached_series(section, code, start_date=None, end_date=None, adjust=DEFAULT_PRICE_ADJUST):
    df = _read_series(section, code)
    if df.empty:
        return df
    local = df.sort_values("date").reset_index(drop=True)
    if section == "prices" and adjust is not None:
        local = local[local["adjust"] == _normalize_adjust(adjust)]
    if start_date is not None:
        local = local[local["date"] >= pd.to_datetime(start_date)]
    if end_date is not None:
        local = local[local["date"] <= pd.to_datetime(end_date)]
    return local.reset_index(drop=True)


def _load_incoming_files(review_date=None, incoming=None):
    if incoming:
        paths = [Path(p) for p in incoming]
        return [path for path in paths if path.exists()]
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    if review_date:
        patterns = [f"*{review_date}*.json"]
    else:
        patterns = ["*.json"]
    paths = []
    for pattern in patterns:
        paths.extend(INCOMING_DIR.glob(pattern))
    return sorted(set(paths))


def _merge_one(section, code, rows):
    incoming = _normalize_frame(pd.DataFrame(rows), section, strict=True)
    existing = _read_series(section, code)
    before_rows = len(existing)
    if existing.empty:
        merged = incoming
    else:
        merged = pd.concat([existing, incoming], ignore_index=True)
        merged = _normalize_frame(merged, section, strict=False)
    path = _write_series(section, code, merged)
    added_dates = sorted(
        set(pd.to_datetime(incoming["date"]).dt.strftime("%Y-%m-%d"))
        - set(pd.to_datetime(existing["date"]).dt.strftime("%Y-%m-%d"))
    )
    latest = "" if merged.empty else pd.to_datetime(merged["date"]).max().strftime("%Y-%m-%d")
    return {
        "code": str(code),
        "section": section,
        "path": str(path),
        "incoming_rows": int(len(incoming)),
        "rows_before": int(before_rows),
        "rows_after": int(len(merged)),
        "new_rows": int(max(0, len(merged) - before_rows)),
        "new_dates": added_dates,
        "latest_date": latest,
    }


def merge_incoming(review_date=None, incoming=None):
    review_date = normalize_review_date(review_date)
    for directory in [INCOMING_DIR, PRICES_DIR, INDICES_DIR, MERGE_REPORT_DIR]:
        directory.mkdir(parents=True, exist_ok=True)

    files = _load_incoming_files(review_date, incoming)
    report = {
        "review_date": f"{review_date[:4]}-{review_date[4:6]}-{review_date[6:]}",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "incoming_files": [str(path) for path in files],
        "merged": [],
        "errors": [],
    }

    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            report["errors"].append({"file": str(path), "error": f"读取失败: {exc}"})
            continue
        for section in ["prices", "indices"]:
            series_map = payload.get(section, {})
            if not isinstance(series_map, dict):
                report["errors"].append({"file": str(path), "section": section, "error": "必须是 code -> rows 的对象"})
                continue
            for code, rows in series_map.items():
                try:
                    report["merged"].append(_merge_one(section, code, rows))
                except Exception as exc:
                    report["errors"].append({
                        "file": str(path),
                        "section": section,
                        "code": str(code),
                        "error": str(exc),
                    })

    try:
        from risk_control.data_dependencies import build_data_requirements

        coverage = build_data_requirements(review_date)
        report["coverage"] = {
            "ready": bool(coverage.get("ready")),
            "missing_holdings": len(coverage.get("missing", {}).get("holdings", [])),
            "missing_market_indices": len(coverage.get("missing", {}).get("market_indices", [])),
        }
    except Exception as exc:
        report["coverage"] = {
            "ready": False,
            "error": str(exc),
        }

    report_path = MERGE_REPORT_DIR / f"{review_date}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_path = OUTPUT_DIR / f"risk_price_merge_{review_date}.json"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_path"] = str(report_path)
    report["output_path"] = str(output_path)
    return report


def main():
    parser = argparse.ArgumentParser(description="合并外部补数行情到长期增量 cache")
    parser.add_argument("--date", help="复盘/风控日期，格式 YYYYMMDD 或 YYYY-MM-DD")
    parser.add_argument("--incoming", action="append", help="指定 incoming JSON，可重复传入；默认读取 incoming/*日期*.json")
    parser.add_argument("--strict", action="store_true", help="存在合并错误时返回非 0")
    args = parser.parse_args()

    report = merge_incoming(args.date, args.incoming)
    print(f"合并日期: {report['review_date']}")
    print(f"Incoming: {len(report['incoming_files'])}")
    print(f"Merged: {len(report['merged'])}")
    print(f"Errors: {len(report['errors'])}")
    coverage = report.get("coverage", {})
    if coverage:
        print(f"Ready: {coverage.get('ready')}")
        if not coverage.get("ready"):
            print(
                "缺数: "
                f"持仓 {coverage.get('missing_holdings', 'N/A')} 项, "
                f"市场指数 {coverage.get('missing_market_indices', 'N/A')} 项"
            )
    print(f"Report: {report['output_path']}")
    if args.strict and report["errors"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
