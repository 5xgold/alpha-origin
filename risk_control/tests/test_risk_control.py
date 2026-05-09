import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from risk_control.signals.state import clear_inactive_signal_records
from risk_control.signals.strategies.add_position import check as check_add_position
from risk_control.scripts.anomaly_detect import detect_anomalies
from risk_control.scripts.risk_report import enrich_portfolio, validate_portfolio_prices
from risk_control.scripts.stop_loss import calc_stop_take_levels, check_circuit_breaker
from shared import data_provider


class RiskControlTests(unittest.TestCase):
    def test_enrich_portfolio_marks_missing_price_instead_of_zeroing_position(self):
        portfolio = pd.DataFrame([
            {"code": "A", "name": "Alpha", "quantity": 100, "cost_price": 0.0},
            {"code": "B", "name": "Beta", "quantity": 50, "cost_price": 12.5},
        ])

        enriched = enrich_portfolio(portfolio, {})

        self.assertTrue(pd.isna(enriched.loc[0, "current_price"]))
        self.assertTrue(pd.isna(enriched.loc[0, "market_value"]))
        self.assertEqual(enriched.loc[0, "price_status"], "missing")

        self.assertEqual(enriched.loc[1, "current_price"], 12.5)
        self.assertEqual(enriched.loc[1, "market_value"], 625.0)
        self.assertEqual(enriched.loc[1, "price_status"], "cost_fallback")

        with self.assertRaisesRegex(ValueError, "缺少行情且成本价为 0"):
            validate_portfolio_prices(enriched)

    def test_circuit_breaker_uses_drawdown_not_window_return(self):
        portfolio = pd.DataFrame([{"code": "A", "quantity": 1, "cost_price": 100.0}])
        prices = {
            "A": pd.DataFrame({
                "date": pd.date_range("2024-01-01", periods=6),
                "open": [100, 110, 120, 115, 112, 108],
                "high": [100, 110, 120, 115, 112, 108],
                "low": [100, 110, 120, 115, 112, 108],
                "close": [100, 110, 120, 115, 112, 108],
                "volume": [1, 1, 1, 1, 1, 1],
            })
        }

        result = check_circuit_breaker(portfolio, prices)

        self.assertAlmostEqual(result["weekly"]["drawdown"], -0.1)
        self.assertTrue(result["weekly"]["triggered"])
        self.assertEqual(result["action"], "reduce_50")

    def test_anomaly_action_uses_unique_signal_types(self):
        portfolio = pd.DataFrame([
            {"code": "A", "name": "Alpha"},
            {"code": "B", "name": "Beta"},
        ])
        base = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=70),
            "open": list(range(1, 71)),
            "high": list(range(1, 71)),
            "low": list(range(1, 71)),
            "close": list(range(1, 71)),
            "volume": [100] * 69 + [10],
        })
        prices = {"A": base, "B": base.copy()}

        result = detect_anomalies(portfolio, prices)

        self.assertEqual(result["signal_count"], 3)
        self.assertEqual(result["alert_count"], 2)
        self.assertEqual(result["action"], "reduce_50")

    def test_get_benchmark_prices_raises_runtime_error_instead_of_exit(self):
        class DummyQueryResult:
            error_code = "0"

            def next(self):
                return False

        with TemporaryDirectory() as tmpdir, \
             patch.object(data_provider, "CACHE_DIR", tmpdir), \
             patch.object(data_provider, "_cache_valid", return_value=False), \
             patch.object(data_provider, "_load_latest_matching_cache", return_value=(None, None)), \
             patch.object(data_provider, "_ensure_bs_login"), \
             patch.object(data_provider.bs, "query_history_k_data_plus", return_value=DummyQueryResult()):
            with self.assertRaisesRegex(RuntimeError, "获取基准指数 000300 失败"):
                data_provider.get_benchmark_prices("000300", "20240101", "20240131")

    def test_get_benchmark_prices_seeds_persistent_series_cache_from_legacy_files(self):
        legacy = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=3),
            "open": [10, 11, 12],
            "high": [11, 12, 13],
            "low": [9, 10, 11],
            "close": [10.5, 11.5, 12.5],
            "volume": [100, 110, 120],
        })

        with TemporaryDirectory() as tmpdir, \
             patch.object(data_provider, "CACHE_DIR", tmpdir), \
             patch.object(data_provider, "_ensure_bs_login") as ensure_login:
            legacy_path = Path(tmpdir) / "benchmark_000300_20240101_20240103.csv"
            legacy.to_csv(legacy_path, index=False)

            result = data_provider.get_benchmark_prices("000300", "20240101", "20240103")

            self.assertEqual(len(result), 3)
            self.assertListEqual(result["close"].tolist(), [10.5, 11.5, 12.5])
            self.assertTrue((Path(tmpdir) / "benchmarks" / "000300.csv").exists())
            ensure_login.assert_not_called()

    def test_clear_inactive_signal_records_resets_only_inactive_strategies(self):
        state = {
            "_meta": {"version": 1, "last_updated": ""},
            "signals": {
                "A": {
                    "stop_loss_basic": {"first_triggered": "2026-04-28", "trigger_count": 2},
                    "trailing_stop": {"first_triggered": "2026-04-29", "trigger_count": 1},
                },
                "B": {
                    "holding_period_weak": {"first_triggered": "2026-04-29", "trigger_count": 1},
                },
            },
            "holdings_first_seen": {},
        }

        clear_inactive_signal_records(state, {("A", "stop_loss_basic")})

        self.assertIn("stop_loss_basic", state["signals"]["A"])
        self.assertNotIn("trailing_stop", state["signals"]["A"])
        self.assertEqual(state["signals"]["B"], {})

    def test_add_position_increments_stage_between_triggers(self):
        portfolio = pd.DataFrame([{
            "code": "A",
            "name": "Alpha",
            "cost_price": 100.0,
            "current_price": 92.0,
            "market_value": 1000.0,
            "familiarity_detail": {
                "business_model": True,
                "shareholder_friendly": True,
                "valuation_low": True,
                "trend_up": True,
            },
        }])
        prices = {
            "A": pd.DataFrame({
                "date": pd.date_range("2024-01-01", periods=20),
                "open": [100.0] * 20,
                "high": [101.0] * 20,
                "low": [91.5] * 20,
                "close": [92.0] * 20,
                "volume": [1000] * 20,
            })
        }
        state = {"_meta": {"version": 1, "last_updated": ""}, "signals": {}, "holdings_first_seen": {}}

        first = check_add_position(portfolio, prices, state=state, total_equity=10000.0)
        second = check_add_position(portfolio, prices, state=state, total_equity=10000.0)

        self.assertEqual(len(first), 1)
        self.assertIn("第1次加仓", first[0]["response_plan"])
        self.assertEqual(state["signals"]["A"]["add_position"]["add_count"], 2)

        self.assertEqual(len(second), 1)
        self.assertIn("第2次加仓", second[0]["response_plan"])

    def test_calc_stop_take_levels_supports_per_holding_risk_rule_overrides(self):
        portfolio = pd.DataFrame([{
            "code": "A",
            "name": "Alpha",
            "market": "上海",
            "quantity": 100,
            "cost_price": 100.0,
            "current_price": 130.0,
            "risk_rules": {
                "stop_loss_atr_multiplier": 1.0,
                "trailing_stop_atr_multiplier": 1.5,
                "take_profit_tiers": [
                    {"trigger_pct": 0.1, "sell_ratio": 0.5},
                    {"trigger_pct": 0.2, "sell_ratio": 0.5},
                ],
            },
        }])
        prices = {
            "A": pd.DataFrame({
                "date": pd.date_range("2024-01-01", periods=20),
                "open": [100.0] * 20,
                "high": [132.0] * 20,
                "low": [98.0] * 20,
                "close": [130.0] * 20,
                "volume": [1000] * 20,
            })
        }

        result = calc_stop_take_levels(portfolio, prices)[0]

        self.assertEqual(result["stop_loss_atr_multiplier"], 1.0)
        self.assertEqual(result["trailing_stop_atr_multiplier"], 1.5)
        self.assertEqual(result["take_profit_tiers"][0]["trigger_pct"], 0.1)
        self.assertEqual(result["take_profit_tiers"][0]["sell_ratio"], 0.5)

    def test_calc_stop_take_levels_supports_entry_day_low_guard_stop(self):
        portfolio = pd.DataFrame([{
            "code": "A",
            "name": "Alpha",
            "market": "上海",
            "buy_date": "20240102",
            "quantity": 100,
            "cost_price": 100.0,
            "current_price": 9.94,
            "trade_plan": {
                "status": "active",
                "stop_loss_strategy": "entry_day_low_guard",
            },
            "risk_rules": {
                "stop_loss_params": {
                    "buffer_ticks": 5,
                },
            },
        }])
        prices = {
            "A": pd.DataFrame({
                "date": pd.to_datetime(["2024-01-02"] + list(pd.date_range("2024-01-03", periods=19))),
                "open": [10.2] * 20,
                "high": [10.4] * 20,
                "low": [10.0] + [9.9] * 19,
                "close": [10.1] + [9.94] * 19,
                "volume": [1000] * 20,
            })
        }
        result = calc_stop_take_levels(portfolio, prices)[0]

        self.assertEqual(result["stop_loss_strategy"], "entry_day_low_guard")
        self.assertEqual(result["entry_date"], "20240102")
        self.assertEqual(result["entry_day_low"], 10.0)
        self.assertEqual(result["price_tick"], 0.01)
        self.assertEqual(result["stop_loss"], 9.95)
        self.assertEqual(result["signal"], "stop_loss")

    def test_calc_stop_take_levels_supports_equity_risk_budget_stop(self):
        portfolio = pd.DataFrame([
            {
                "code": "A",
                "name": "Alpha",
                "market": "上海",
                "quantity": 100,
                "cost_price": 100.0,
                "current_price": 79.0,
                "risk_rules": {
                    "stop_loss_strategy": "equity_risk_budget",
                },
            },
            {
                "code": "B",
                "name": "Beta",
                "market": "上海",
                "quantity": 100,
                "cost_price": 200.0,
                "current_price": 179.0,
                "risk_rules": {
                    "stop_loss_strategy": "equity_risk_budget",
                },
            },
        ])
        prices = {
            "A": pd.DataFrame({
                "date": pd.date_range("2024-01-01", periods=20),
                "open": [100.0] * 20,
                "high": [101.0] * 20,
                "low": [79.0] * 20,
                "close": [79.0] * 20,
                "volume": [1000] * 20,
            }),
            "B": pd.DataFrame({
                "date": pd.date_range("2024-01-01", periods=20),
                "open": [200.0] * 20,
                "high": [201.0] * 20,
                "low": [179.0] * 20,
                "close": [179.0] * 20,
                "volume": [1000] * 20,
            }),
        }

        result = calc_stop_take_levels(portfolio, prices, total_equity=100000.0)

        self.assertEqual(result[0]["stop_loss_strategy"], "equity_risk_budget")
        self.assertAlmostEqual(result[0]["position_weight"], 0.1)
        self.assertAlmostEqual(result[0]["max_loss_pct"], 0.2)
        self.assertEqual(result[0]["stop_loss"], 80.0)
        self.assertEqual(result[0]["signal"], "stop_loss")

        self.assertAlmostEqual(result[1]["position_weight"], 0.2)
        self.assertAlmostEqual(result[1]["max_loss_pct"], 0.1)
        self.assertEqual(result[1]["stop_loss"], 180.0)
        self.assertEqual(result[1]["signal"], "stop_loss")

    def test_calc_stop_take_levels_trade_plan_strategy_overrides_risk_rules(self):
        portfolio = pd.DataFrame([{
            "code": "A",
            "name": "Alpha",
            "market": "上海",
            "buy_date": "20240102",
            "quantity": 100,
            "cost_price": 100.0,
            "current_price": 9.94,
            "trade_plan": {
                "status": "active",
                "stop_loss_strategy": "entry_day_low_guard",
            },
            "risk_rules": {
                "stop_loss_strategy": "atr",
                "stop_loss_params": {
                    "buffer_ticks": 5,
                },
            },
        }])
        prices = {
            "A": pd.DataFrame({
                "date": pd.to_datetime(["2024-01-02"] + list(pd.date_range("2024-01-03", periods=19))),
                "open": [10.2] * 20,
                "high": [10.4] * 20,
                "low": [10.0] + [9.9] * 19,
                "close": [10.1] + [9.94] * 19,
                "volume": [1000] * 20,
            })
        }

        result = calc_stop_take_levels(portfolio, prices)[0]

        self.assertEqual(result["stop_loss_strategy"], "entry_day_low_guard")
        self.assertEqual(result["stop_loss"], 9.95)


if __name__ == "__main__":
    unittest.main()
