import json
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from risk_control.agent_price_cache import merge_incoming, read_cached_series
from risk_control.data_dependencies import build_data_requirements, fetch_missing_data
from shared.data_provider import get_stock_prices, get_benchmark_prices


PORTFOLIO_TOML = """
[account]
total_equity = 100000

[[holdings]]
code = "600036"
name = "招商银行"
market = "上海"
quantity = 100
cost_price = 38.0
trade_plan = {stop_loss_strategy = "atr"}
"""


def _rows(count=60):
    rows = []
    for i in range(count):
        day = i + 1
        rows.append({
            "date": f"2026-03-{day:02d}" if day <= 31 else f"2026-04-{day - 31:02d}",
            "open": 10.0 + i * 0.01,
            "high": 10.2 + i * 0.01,
            "low": 9.8 + i * 0.01,
            "close": 10.1 + i * 0.01,
            "volume": 100000 + i,
        })
    rows[-1]["date"] = "2026-05-08"
    return rows


def _business_day_rows(start="2026-01-08", end="2026-05-08"):
    import pandas as pd

    rows = []
    for i, ts in enumerate(pd.bdate_range(start=start, end=end)):
        rows.append({
            "date": ts.strftime("%Y-%m-%d"),
            "open": 10.0 + i * 0.01,
            "high": 10.2 + i * 0.01,
            "low": 9.8 + i * 0.01,
            "close": 10.1 + i * 0.01,
            "volume": 100000 + i,
        })
    return rows


def _business_day_rows_with_gap():
    return (
        _business_day_rows(start="2026-01-01", end="2026-03-10")
        + _business_day_rows(start="2026-04-10", end="2026-05-08")
    )


def _patch_agent_cache_dirs(root, incoming_dir=None):
    incoming_dir = incoming_dir or root / "agent_prices" / "incoming"
    return (
        patch("risk_control.agent_price_cache.AGENT_CACHE_DIR", root / "agent_prices"),
        patch("risk_control.agent_price_cache.INCOMING_DIR", incoming_dir),
        patch("risk_control.agent_price_cache.PRICES_DIR", root / "agent_prices" / "prices"),
        patch("risk_control.agent_price_cache.INDICES_DIR", root / "agent_prices" / "indices"),
        patch("risk_control.agent_price_cache.MERGE_REPORT_DIR", root / "agent_prices" / "merge_reports"),
        patch("risk_control.agent_price_cache.OUTPUT_DIR", root / "output"),
    )


def _merge_with_patched_dirs(root, review_date="20260508", incoming_dir=None):
    with ExitStack() as stack:
        for patcher in _patch_agent_cache_dirs(root, incoming_dir):
            stack.enter_context(patcher)
        return merge_incoming(review_date)


class RiskDataDependencyTests(unittest.TestCase):
    def test_build_data_requirements_reports_missing_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portfolio = root / "portfolio.toml"
            portfolio.write_text(PORTFOLIO_TOML, encoding="utf-8")
            with patch("risk_control.data_dependencies.CACHE_DIR", str(root)), \
                 patch("risk_control.data_dependencies.AGENT_PRICE_CACHE_DIR", root / "agent_prices"), \
                 patch("risk_control.data_dependencies.read_agent_price_series", return_value=None):
                payload = build_data_requirements("20260508", portfolio)

        self.assertFalse(payload["ready"])
        self.assertEqual(len(payload["missing"]["holdings"]), 1)
        self.assertEqual(payload["missing"]["holdings"][0]["status"]["reason"], "no_cache")

    def test_build_data_requirements_accepts_agent_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portfolio = root / "portfolio.toml"
            portfolio.write_text(PORTFOLIO_TOML, encoding="utf-8")
            incoming_dir = root / "agent_prices" / "incoming"
            incoming_dir.mkdir(parents=True)
            payload = {
                "prices": {"600036": _business_day_rows()},
                "indices": {
                    "000001": [{"date": "2026-05-08", "close": 3000 + i} for i in range(20)],
                    "000300": [{"date": "2026-05-08", "close": 4000 + i} for i in range(20)],
                    "HK.800000": [{"date": "2026-05-08", "close": 20000 + i} for i in range(20)],
                },
            }
            for key in payload["indices"]:
                for i, row in enumerate(payload["indices"][key]):
                    row["date"] = f"2026-04-{10 + i:02d}" if i < 19 else "2026-05-08"
            (incoming_dir / "20260508.json").write_text(json.dumps(payload), encoding="utf-8")
            _merge_with_patched_dirs(root, incoming_dir=incoming_dir)

            with patch("risk_control.data_dependencies.CACHE_DIR", str(root)), \
                 patch("risk_control.data_dependencies.AGENT_PRICE_CACHE_DIR", root / "agent_prices"), \
                 patch("risk_control.agent_price_cache.PRICES_DIR", root / "agent_prices" / "prices"), \
                 patch("risk_control.agent_price_cache.INDICES_DIR", root / "agent_prices" / "indices"):
                result = build_data_requirements("20260508", portfolio)

        self.assertTrue(result["ready"])
        self.assertEqual(result["missing"]["holdings"], [])
        self.assertEqual(result["missing"]["market_indices"], [])

    def test_data_provider_reads_agent_price_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            incoming_dir = root / "agent_prices" / "incoming"
            incoming_dir.mkdir(parents=True)
            (incoming_dir / "20260508.json").write_text(json.dumps({
                "prices": {"600036": _business_day_rows()},
                "indices": {
                    "000001": [
                        {"date": f"2026-05-{1 + i:02d}", "close": 4100 + i}
                        for i in range(7)
                    ] + [{"date": "2026-05-08", "close": 4179.95}]
                },
            }), encoding="utf-8")
            _merge_with_patched_dirs(root, incoming_dir=incoming_dir)

            with patch("shared.data_provider.CACHE_DIR", str(root)), \
                 patch("risk_control.agent_price_cache.PRICES_DIR", root / "agent_prices" / "prices"), \
                 patch("risk_control.agent_price_cache.INDICES_DIR", root / "agent_prices" / "indices"):
                stock = get_stock_prices("600036", "20260301", "20260508")
                index = get_benchmark_prices("000001", "20260501", "20260508")

        self.assertFalse(stock.empty)
        self.assertIn("high", stock.columns)
        self.assertEqual(float(index["close"].iloc[-1]), 4179.95)

    def test_merge_incoming_writes_descending_incremental_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            incoming_dir = root / "agent_prices" / "incoming"
            incoming_dir.mkdir(parents=True)
            (incoming_dir / "20260508.json").write_text(json.dumps({
                "prices": {"600036": _business_day_rows(start="2026-05-01", end="2026-05-08")},
                "indices": {"000001": [{"date": "2026-05-08", "close": 4179.95}]},
            }), encoding="utf-8")
            report = _merge_with_patched_dirs(root, incoming_dir=incoming_dir)

            price_file = root / "agent_prices" / "prices" / "600036.csv"
            index_file = root / "agent_prices" / "indices" / "000001.csv"
            price_exists = price_file.exists()
            index_exists = index_file.exists()
            lines = price_file.read_text(encoding="utf-8").splitlines()

        self.assertEqual(report["errors"], [])
        self.assertTrue(price_exists)
        self.assertTrue(index_exists)
        self.assertEqual(lines[0], "date,open,high,low,close,volume,adjust")
        self.assertTrue(lines[1].startswith("2026-05-08,"))
        self.assertTrue(lines[1].endswith(",qfq"))

    def test_agent_price_cache_records_and_filters_adjust(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            incoming_dir = root / "agent_prices" / "incoming"
            incoming_dir.mkdir(parents=True)
            rows = _business_day_rows(start="2026-05-01", end="2026-05-08")
            raw_rows = [dict(row, adjust="raw", close=88.0) for row in rows]
            qfq_rows = [dict(row, adjust="qfq") for row in rows]
            (incoming_dir / "20260508.json").write_text(json.dumps({
                "prices": {"600036": raw_rows + qfq_rows},
            }), encoding="utf-8")
            _merge_with_patched_dirs(root, incoming_dir=incoming_dir)

            with patch("risk_control.agent_price_cache.PRICES_DIR", root / "agent_prices" / "prices"):
                qfq = read_cached_series("prices", "600036", adjust="qfq")
                raw = read_cached_series("prices", "600036", adjust="raw")

        self.assertFalse(qfq.empty)
        self.assertFalse(raw.empty)
        self.assertEqual(float(qfq["close"].iloc[-1]), rows[-1]["close"])
        self.assertEqual(float(raw["close"].iloc[-1]), 88.0)

    def test_data_provider_requires_full_agent_cache_for_stock_prices(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache_dir = root / "agent_prices"
            cache_dir.mkdir()
            partial_rows = _business_day_rows()[:-5]
            (cache_dir / "20260508.json").write_text(json.dumps({
                "prices": {"600036": partial_rows},
            }), encoding="utf-8")
            stocks_dir = root / "stocks"
            stocks_dir.mkdir()
            fallback_rows = _business_day_rows()
            fallback_rows[-1]["close"] = 88.0
            (stocks_dir / "600036_20260301_20260508_qfq.csv").write_text(
                "date,open,high,low,close,volume\n"
                + "\n".join(
                    f"{r['date']},{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']}"
                    for r in fallback_rows
                ),
                encoding="utf-8",
            )

            with patch("shared.data_provider.CACHE_DIR", str(root)):
                stock = get_stock_prices("600036", "20260301", "20260508")

        self.assertEqual(float(stock["close"].iloc[-1]), 88.0)

    def test_data_provider_does_not_short_circuit_partial_agent_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cache_dir = root / "agent_prices"
            cache_dir.mkdir()
            (cache_dir / "20260508.json").write_text(json.dumps({
                "prices": {"600036": _rows(3)},
            }), encoding="utf-8")
            stocks_dir = root / "stocks"
            stocks_dir.mkdir()
            fallback_rows = _rows(60)
            fallback_rows[-1]["close"] = 99.0
            (stocks_dir / "600036_20260301_20260508_qfq.csv").write_text(
                "date,open,high,low,close,volume\n"
                + "\n".join(
                    f"{r['date']},{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']}"
                    for r in fallback_rows
                ),
                encoding="utf-8",
            )

            with patch("shared.data_provider.CACHE_DIR", str(root)):
                stock = get_stock_prices("600036", "20260301", "20260508")

        self.assertGreater(len(stock), 3)
        self.assertEqual(float(stock["close"].iloc[-1]), 99.0)

    def test_build_data_requirements_accepts_stock_and_benchmark_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portfolio = root / "portfolio.toml"
            portfolio.write_text(PORTFOLIO_TOML, encoding="utf-8")
            stocks_dir = root / "stocks"
            stocks_dir.mkdir()
            stock_rows = _rows(60)
            (stocks_dir / "600036_20260301_20260508_qfq.csv").write_text(
                "date,open,high,low,close,volume\n"
                + "\n".join(
                    f"{r['date']},{r['open']},{r['high']},{r['low']},{r['close']},{r['volume']}"
                    for r in stock_rows
                ),
                encoding="utf-8",
            )
            benchmarks_dir = root / "benchmarks"
            benchmarks_dir.mkdir()
            for code in ["000001", "000300", "HK_800000"]:
                rows = [
                    f"2026-04-{10 + i:02d},1,1,1,{3000 + i},0,0"
                    for i in range(19)
                ]
                rows.append("2026-05-08,1,1,1,3999,0,0")
                (benchmarks_dir / f"{code}.csv").write_text(
                    "date,open,high,low,close,volume,amount\n" + "\n".join(rows),
                    encoding="utf-8",
                )

            with patch("risk_control.data_dependencies.CACHE_DIR", str(root)), \
                 patch("risk_control.data_dependencies.AGENT_PRICE_CACHE_DIR", root / "agent_prices"):
                payload = build_data_requirements("20260508", portfolio)

        self.assertTrue(payload["ready"])
        self.assertEqual(payload["missing"]["holdings"], [])
        self.assertEqual(payload["missing"]["market_indices"], [])

    def test_build_data_requirements_rejects_internal_price_gap(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            portfolio = root / "portfolio.toml"
            portfolio.write_text(PORTFOLIO_TOML, encoding="utf-8")
            incoming_dir = root / "agent_prices" / "incoming"
            incoming_dir.mkdir(parents=True)
            payload = {
                "prices": {"600036": _business_day_rows_with_gap()},
                "indices": {
                    "000001": [{"date": f"2026-04-{10 + i:02d}", "close": 3000 + i} for i in range(20)],
                    "000300": [{"date": f"2026-04-{10 + i:02d}", "close": 4000 + i} for i in range(20)],
                    "HK.800000": [{"date": f"2026-04-{10 + i:02d}", "close": 20000 + i} for i in range(20)],
                },
            }
            for rows in payload["indices"].values():
                rows[-1]["date"] = "2026-05-08"
            (incoming_dir / "20260508.json").write_text(json.dumps(payload), encoding="utf-8")
            _merge_with_patched_dirs(root, incoming_dir=incoming_dir)

            with patch("risk_control.data_dependencies.CACHE_DIR", str(root)), \
                 patch("risk_control.data_dependencies.AGENT_PRICE_CACHE_DIR", root / "agent_prices"), \
                 patch("risk_control.agent_price_cache.PRICES_DIR", root / "agent_prices" / "prices"), \
                 patch("risk_control.agent_price_cache.INDICES_DIR", root / "agent_prices" / "indices"):
                result = build_data_requirements("20260508", portfolio)

        self.assertFalse(result["ready"])
        status = result["missing"]["holdings"][0]["status"]
        self.assertEqual(status["reason"], "date_gap")
        self.assertTrue(status["has_gaps"])

    def test_fetch_missing_data_skips_when_ready(self):
        payload = {"ready": True, "missing": {"holdings": [{"code": "600036"}], "market_indices": []}}

        with patch("risk_control.data_dependencies.get_stock_prices") as fetch_stock:
            result = fetch_missing_data(payload)

        self.assertEqual(result["holdings"], [])
        fetch_stock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
