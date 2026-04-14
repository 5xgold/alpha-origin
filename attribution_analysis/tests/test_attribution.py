import unittest
from unittest.mock import patch

import pandas as pd

from attribution_analysis.scripts.attribution import (
    build_performance_curves,
    calculate_account_metrics,
    calculate_portfolio_value,
    calculate_returns,
)
from attribution_analysis.scripts.brinson import _get_composite_benchmark_sector_data
from attribution_analysis.scripts.brinson import brinson_analysis


class AttributionAnalysisTests(unittest.TestCase):
    @patch('attribution_analysis.scripts.attribution.get_stock_prices')
    def test_calculate_portfolio_value_carries_positions_across_non_trade_days(self, mock_get_stock_prices):
        mock_get_stock_prices.return_value = pd.DataFrame({
            'date': pd.to_datetime(['2026-01-02', '2026-01-03', '2026-01-06']),
            'close': [10.0, 11.0, 12.0],
        })
        snapshots = [{
            'date': pd.Timestamp('2026-01-02'),
            'positions': {'000001': {'quantity': 100}},
            'cash': 0.0,
        }]

        values, _ = calculate_portfolio_value(
            snapshots,
            pd.Timestamp('2026-01-02'),
            pd.Timestamp('2026-01-06'),
            extra_dates=pd.to_datetime(['2026-01-02', '2026-01-03', '2026-01-06']),
        )

        self.assertEqual(values['date'].dt.strftime('%Y-%m-%d').tolist(), ['2026-01-02', '2026-01-03', '2026-01-06'])
        self.assertEqual(values['value'].tolist(), [1000.0, 1100.0, 1200.0])

    def test_strategy_curve_strips_external_cash_flows(self):
        portfolio_values = pd.DataFrame({
            'date': pd.to_datetime(['2026-01-02', '2026-01-03', '2026-01-06']),
            'value': [0.0, 50.0, 60.0],
            'stock_value': [1000.0, 1050.0, 1060.0],
            'cash': [-1000.0, -1000.0, -1000.0],
        })
        cash_flows = pd.DataFrame({
            'date': pd.to_datetime(['2026-01-02', '2026-01-03']),
            'amount': [1000.0, 100.0],
            'type': ['银行转存', '银行转存'],
        })
        benchmark = pd.DataFrame({
            'date': pd.to_datetime(['2026-01-02', '2026-01-03', '2026-01-06']),
            'close': [100.0, 100.0, 100.0],
        })

        curves = build_performance_curves(portfolio_values, cash_flows)
        returns = calculate_returns(curves, benchmark, value_col='strategy_nav')

        self.assertAlmostEqual(curves.loc[1, 'account_value'], 1150.0)
        self.assertAlmostEqual(curves.loc[1, 'strategy_return'], 0.0454545454, places=6)
        self.assertAlmostEqual(curves.loc[2, 'strategy_nav'], 1.0545454545, places=6)
        self.assertAlmostEqual((1 + returns['portfolio_return']).prod() - 1, 1.0545454545 - 1, places=6)

    def test_account_metrics_use_account_curve_consistently(self):
        curves = pd.DataFrame({
            'date': pd.to_datetime(['2026-01-02', '2026-01-03', '2026-01-06']),
            'account_value': [1000.0, 1150.0, 1110.0],
            'cumulative_flow': [0.0, 100.0, 100.0],
        })

        metrics = calculate_account_metrics(curves)

        self.assertAlmostEqual(metrics['ending_value'], 1110.0)
        self.assertAlmostEqual(metrics['net_flow'], 100.0)
        self.assertAlmostEqual(metrics['pnl'], 10.0)
        self.assertAlmostEqual(metrics['return'], 0.11)

    @patch('attribution_analysis.scripts.brinson.get_benchmark_sector_data')
    def test_composite_benchmark_sector_return_is_weighted_by_component(self, mock_get_benchmark_sector_data):
        mock_get_benchmark_sector_data.side_effect = [
            {'科技': {'weight': 0.6, 'return': 0.10}},
            {'科技': {'weight': 0.2, 'return': 0.40}},
        ]
        benchmark_config = [
            {'index': '000300', 'weight': 0.5, 'source': 'baostock'},
            {'index': '000905', 'weight': 0.5, 'source': 'baostock'},
        ]

        result = _get_composite_benchmark_sector_data(benchmark_config, '20260101', '20260131')

        self.assertAlmostEqual(result['科技']['weight'], 1.0)
        self.assertAlmostEqual(result['科技']['return'], 0.175)

    @patch('attribution_analysis.scripts.brinson.get_benchmark_sector_data')
    @patch('attribution_analysis.scripts.brinson.classify_portfolio_sectors')
    def test_brinson_analysis_exposes_residual_effect(self, mock_classify_portfolio, mock_benchmark_sectors):
        mock_classify_portfolio.return_value = {
            '科技': {'weight': 1.0, 'return': 0.10, 'codes': ['A']},
        }
        mock_benchmark_sectors.return_value = {
            '科技': {'weight': 1.0, 'return': 0.05},
        }
        portfolio_values = pd.DataFrame({
            'date': pd.to_datetime(['2026-01-02', '2026-01-03']),
            'value': [1.0, 1.20],
        })
        benchmark_prices = pd.DataFrame({
            'date': pd.to_datetime(['2026-01-02', '2026-01-03']),
            'close': [100.0, 105.0],
        })

        result = brinson_analysis({}, portfolio_values, benchmark_prices, '2026-01-02', '2026-01-03')

        self.assertAlmostEqual(result['total_active'], 0.05)
        self.assertAlmostEqual(result['excess_return'], 0.15)
        self.assertAlmostEqual(result['residual_effect'], 0.10)
        self.assertAlmostEqual(result['verification_diff'], 0.10)


if __name__ == '__main__':
    unittest.main()
