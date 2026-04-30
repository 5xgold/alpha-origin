import unittest

import pandas as pd

from llm_digest.scripts.daily_review import summarize_today_trades


class DailyReviewTests(unittest.TestCase):
    def test_summarize_today_trades_groups_actions_and_amounts(self):
        trades = pd.DataFrame([
            {
                "date": "20260430", "code": "600000", "name": "浦发银行",
                "direction": "买入", "quantity": 1000, "amount": -10000.0,
            },
            {
                "date": "20260430", "code": "600000", "name": "浦发银行",
                "direction": "卖出", "quantity": -400, "amount": 4200.0,
            },
            {
                "date": "20260430", "code": "000001", "name": "平安银行",
                "direction": "卖出", "quantity": -800, "amount": 9600.0,
            },
        ])

        result = summarize_today_trades(trades)

        self.assertEqual(result["count"], 3)
        self.assertEqual(len(result["stock_actions"]), 2)
        self.assertIn("浦发银行(600000): 日内调仓", result["summary_lines"][0])
        self.assertIn("平安银行(000001): 净卖出", "\n".join(result["summary_lines"]))
        self.assertAlmostEqual(result["net_buy_amount"], -3800.0)

    def test_summarize_today_trades_handles_empty_frame(self):
        result = summarize_today_trades(pd.DataFrame(columns=["date", "code", "name", "direction", "quantity", "amount"]))

        self.assertEqual(result["count"], 0)
        self.assertEqual(result["summary_lines"], ["- 今日无成交"])


if __name__ == "__main__":
    unittest.main()
