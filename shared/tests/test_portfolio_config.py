import tempfile
import unittest
from pathlib import Path

from shared.portfolio_config import load_watchlist_from_toml


class PortfolioConfigTests(unittest.TestCase):
    def test_load_watchlist_from_toml(self):
        content = """
[account]
total_equity = 100000

[[holdings]]
code = "600519"
name = "贵州茅台"
market = "上海"
quantity = 10
cost_price = 1800

[[watchlist]]
code = "300750"
name = "宁德时代"
market = "深圳"
target_buy_price = 180
breakout_price = 205
notes = "test"
"""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "portfolio.toml"
            path.write_text(content, encoding="utf-8")
            df = load_watchlist_from_toml(str(path))

        self.assertEqual(len(df), 1)
        self.assertEqual(df.iloc[0]["code"], "300750")
        self.assertEqual(float(df.iloc[0]["target_buy_price"]), 180.0)
        self.assertTrue(bool(df.iloc[0]["enabled"]))


if __name__ == "__main__":
    unittest.main()
