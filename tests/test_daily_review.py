import unittest
from unittest.mock import patch

import pandas as pd

from scripts.daily_review import evaluate_watchlist, normalize_review_date


class DailyReviewTests(unittest.TestCase):
    def test_normalize_review_date_accepts_dash_format(self):
        self.assertEqual(normalize_review_date("2026-04-30"), "20260430")

    @patch("scripts.daily_review.save_watch_state")
    @patch("scripts.daily_review.load_watch_state")
    @patch("scripts.daily_review._latest_close")
    @patch("scripts.daily_review.get_watchlist")
    def test_evaluate_watchlist_marks_triggered_rules(
        self,
        mock_watchlist,
        mock_latest_close,
        mock_load_watch_state,
        mock_save_watch_state,
    ):
        mock_watchlist.return_value = pd.DataFrame([
            {
                "code": "300750",
                "name": "宁德时代",
                "market": "深圳",
                "target_buy_price": 180.0,
                "breakout_price": 205.0,
                "signal_rules": {},
                "notes": "",
                "enabled": True,
            }
        ])
        mock_load_watch_state.return_value = {"_meta": {"version": 1, "last_updated": ""}, "signals": {}}
        mock_latest_close.return_value = (179.0, 178.0, None)

        result = evaluate_watchlist("20260430")

        self.assertEqual(len(result["triggered"]), 1)
        self.assertEqual(result["triggered"][0]["code"], "300750")
        self.assertIn("回调到目标买点", result["triggered"][0]["signals"])
        self.assertEqual(len(result["signals"]), 1)
        self.assertEqual(result["signals"][0]["strategy"], "target_buy")
        mock_save_watch_state.assert_called_once()


if __name__ == "__main__":
    unittest.main()
