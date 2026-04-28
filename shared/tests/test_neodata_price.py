import unittest

from shared.neodata_price import _extract_security_name, _parse_index_pe, _parse_price_from_content


class NeoDataPriceTests(unittest.TestCase):
    def test_parse_etf_quote_content(self):
        content = (
            "券商ETF(代码:159842) 最新价格:1.082元 昨日收盘价格:1.070元 "
            "今日开盘价格:1.075元 最高价:1.085元 最低价:1.071元 "
            "当日涨跌幅:+1.12% 成交数量(手):123,456 成交金额(万元):7,890.12"
        )

        parsed = _parse_price_from_content(content)

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["price"], 1.082)
        self.assertEqual(parsed["volume"], 12345600)
        self.assertEqual(_extract_security_name(content), "券商ETF")

    def test_parse_index_percentile_with_parentheses(self):
        content = "沪深300 市盈率TTM: 12.34 历史百分位（%）: 18.9"

        parsed = _parse_index_pe(content)

        self.assertEqual(parsed["pe_ttm"], 12.34)
        self.assertEqual(parsed["pe_percentile"], 18.9)


if __name__ == "__main__":
    unittest.main()
