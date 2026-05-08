import unittest
from unittest.mock import patch

import pandas as pd

from scripts.daily_review import build_ai_review_contract, build_market_context, evaluate_watchlist, normalize_review_date, render_prompt


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

    @patch("scripts.daily_review.get_benchmark_prices")
    @patch("scripts.daily_review.get_sw_sector_returns")
    @patch("scripts.daily_review.get_a_share_market_breadth")
    @patch("scripts.daily_review.get_eastmoney_news")
    def test_build_market_context_includes_turnover_and_limit_compare(
        self,
        mock_news,
        mock_breadth,
        mock_sector_returns,
        mock_benchmark_prices,
    ):
        mock_news.return_value = ["政策新闻"]
        mock_sector_returns.return_value = {}
        mock_breadth.return_value = {
            "turnover_rows": [
                {"date": "20260429", "turnover": 900_000_000_000.0},
                {"date": "20260430", "turnover": 1_000_000_000_000.0},
            ],
            "limit_rows": [
                {"date": "20260429", "limit_up": 40, "limit_down": 5},
                {"date": "20260430", "limit_up": 55, "limit_down": 3},
            ],
        }
        mock_benchmark_prices.return_value = pd.DataFrame([
            {"date": "2026-04-29", "close": 3000.0},
            {"date": "2026-04-30", "close": 3030.0},
        ])

        result = build_market_context("20260430")

        self.assertEqual(len(result["breadth"]["lines"]), 3)
        self.assertIn("20260430", result["breadth"]["lines"][1])
        self.assertIn("两市成交额10,000亿", result["breadth"]["lines"][1])
        self.assertIn("涨停55家", result["breadth"]["lines"][1])
        self.assertIn("跌停3家", result["breadth"]["lines"][1])
        self.assertIn("成交额较前一交易日+11.1%", result["breadth"]["lines"][2])

    def test_render_prompt_marks_dates_for_external_ai(self):
        context = {
            "review_date": "20260430",
            "generated_at": "2026-04-30T18:00:00",
            "data_degradations": [],
            "structured": {
                "market": {
                    "benchmarks": [],
                    "breadth": {"lines": ["- 20260430: 两市成交额10,000亿，涨停55家，跌停3家"]},
                    "news": [],
                    "hot_sectors": {"lines": []},
                },
                "portfolio": {"lines": []},
                "watchlist": {"lines": [], "signals": []},
                "today_trades": {"lines": []},
                "next_actions": [],
            },
        }
        context["ai_review_contract"] = build_ai_review_contract(context)

        prompt = render_prompt(context)

        self.assertIn("复盘日期: 2026-04-30", prompt)
        self.assertIn("AI 职责: 复盘编辑 + 一致性检查员，不是交易规则引擎", prompt)
        self.assertIn("所有“今日/当日”均指复盘日期", prompt)
        self.assertIn("引用市场宽度时必须带日期", prompt)
        self.assertIn("明日计划只能从“明日动作候选”中归纳", prompt)
        self.assertIn("必须检查：", prompt)
        self.assertIn("禁止事项：", prompt)
        self.assertIn("两日成交额与涨跌停（按交易日对比）", prompt)

    def test_ai_review_contract_requires_degradation_disclosure(self):
        context = {
            "review_date": "20260430",
            "data_degradations": ["benchmark/000001: 回退到本地时序缓存"],
            "structured": {
                "portfolio": {
                    "danger_names": ["招商银行"],
                    "warning_names": [],
                },
                "watchlist": {"triggered": []},
                "next_actions": ["优先处理危险信号：招商银行(600036)"],
            },
        }

        contract = build_ai_review_contract(context)

        self.assertEqual(contract["ai_role"], "复盘编辑 + 一致性检查员，不是交易规则引擎")
        self.assertIn("结论必须说明数据降级及其影响", contract["required_checks"][0])
        self.assertIn("danger 持仓: 招商银行", contract["evidence_targets"])
        self.assertIn("优先处理危险信号：招商银行(600036)", contract["allowed_next_actions"])


if __name__ == "__main__":
    unittest.main()
