import unittest
from unittest.mock import patch

import pandas as pd

from risk_control.scripts.anomaly_detect import detect_anomalies
from risk_control.scripts.risk_report import enrich_portfolio, validate_portfolio_prices
from risk_control.scripts.stop_loss import check_circuit_breaker
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

        with patch.object(data_provider, "_cache_valid", return_value=False), \
             patch.object(data_provider, "_load_latest_matching_cache", return_value=(None, None)), \
             patch.object(data_provider, "_ensure_bs_login"), \
             patch.object(data_provider.bs, "query_history_k_data_plus", return_value=DummyQueryResult()):
            with self.assertRaisesRegex(RuntimeError, "获取基准指数 000300 失败"):
                data_provider.get_benchmark_prices("000300", "20240101", "20240131")


if __name__ == "__main__":
    unittest.main()
