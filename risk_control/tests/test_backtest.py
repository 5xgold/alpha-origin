"""风控回测模块测试 — 使用合成数据，无网络调用"""

import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from risk_control.backtest.engine import run_backtest, BacktestResult
from risk_control.backtest.executor import execute_signals
from risk_control.backtest.params import override_config, run_parameter_sweep, SweepResult
from risk_control.backtest.metrics import compute_metrics


# ═══════════════════════════════════════════
# 测试数据工厂
# ═══════════════════════════════════════════


def _make_prices(code, start="2026-01-01", days=60, base_price=10.0, trend=0.0, volatility=0.3):
    """生成合成价格数据

    Args:
        trend: 每日涨跌幅（正=上涨，负=下跌）
        volatility: ATR 占价格的比例
    """
    dates = pd.bdate_range(start, periods=days)
    prices = []
    price = base_price
    for i, d in enumerate(dates):
        price = price * (1 + trend)
        atr = price * volatility / 14  # 模拟 ATR
        high = price + atr
        low = price - atr
        open_p = price + atr * 0.3
        prices.append({
            "date": d,
            "open": round(open_p, 3),
            "high": round(high, 3),
            "low": round(low, 3),
            "close": round(price, 3),
            "volume": 1000000,
        })
    return pd.DataFrame(prices)


def _make_portfolio(holdings):
    """构建 portfolio_df

    Args:
        holdings: list of (code, name, qty, cost_price)
    """
    rows = []
    for code, name, qty, cost in holdings:
        rows.append({
            "code": code,
            "name": name,
            "market": "上海",
            "quantity": qty,
            "cost_price": cost,
            "current_price": cost,
            "market_value": qty * cost,
            "trade_plan": {"status": "active", "stop_loss_strategy": "atr"},
            "risk_rules": {},
        })
    return pd.DataFrame(rows)


# ═══════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════


class TestExecutor:
    """executor.py 单元测试"""

    def test_stop_loss_sells_full_position(self):
        """止损信号卖出 100%"""
        portfolio_state = {
            "positions": {"A": {"quantity": 100, "cost_price": 10.0, "name": "Alpha"}},
            "cash": 50000.0,
        }
        signals = [{"code": "A", "strategy": "stop_loss_basic", "signal_type": "sell"}]
        cb = {"action": None}
        next_day_prices = {"A": 9.0}

        trades = execute_signals(signals, cb, portfolio_state, next_day_prices, "2026-02-01")

        assert len(trades) == 1
        assert trades[0]["quantity"] == 100
        assert trades[0]["price"] == 9.0
        assert trades[0]["reason"] == "stop_loss_basic"
        assert "A" not in portfolio_state["positions"]
        assert portfolio_state["cash"] == 50000.0 + 100 * 9.0

    def test_take_profit_sells_one_third(self):
        """止盈信号卖出 1/3"""
        portfolio_state = {
            "positions": {"B": {"quantity": 300, "cost_price": 10.0, "name": "Beta"}},
            "cash": 0.0,
        }
        signals = [{"code": "B", "strategy": "take_profit_tiered", "signal_type": "sell"}]
        cb = {"action": None}
        next_day_prices = {"B": 12.0}

        trades = execute_signals(signals, cb, portfolio_state, next_day_prices, "2026-02-01")

        assert len(trades) == 1
        assert trades[0]["quantity"] == 100  # 300 * 1/3
        assert portfolio_state["positions"]["B"]["quantity"] == 200

    def test_circuit_breaker_reduce_50(self):
        """熔断减仓 50%"""
        portfolio_state = {
            "positions": {
                "A": {"quantity": 100, "cost_price": 10.0, "name": "Alpha"},
                "B": {"quantity": 200, "cost_price": 20.0, "name": "Beta"},
            },
            "cash": 10000.0,
        }
        signals = []
        cb = {"action": "reduce_50"}
        next_day_prices = {"A": 9.0, "B": 18.0}

        trades = execute_signals(signals, cb, portfolio_state, next_day_prices, "2026-02-01")

        assert len(trades) == 2
        assert portfolio_state["positions"]["A"]["quantity"] == 50
        assert portfolio_state["positions"]["B"]["quantity"] == 100

    def test_circuit_breaker_liquidate(self):
        """熔断清仓"""
        portfolio_state = {
            "positions": {"A": {"quantity": 100, "cost_price": 10.0, "name": "Alpha"}},
            "cash": 0.0,
        }
        signals = []
        cb = {"action": "liquidate"}
        next_day_prices = {"A": 8.0}

        trades = execute_signals(signals, cb, portfolio_state, next_day_prices, "2026-02-01")

        assert len(trades) == 1
        assert trades[0]["quantity"] == 100
        assert not portfolio_state["positions"]
        assert portfolio_state["cash"] == 800.0

    def test_most_aggressive_signal_wins(self):
        """同一股票多信号取最激进"""
        portfolio_state = {
            "positions": {"A": {"quantity": 100, "cost_price": 10.0, "name": "Alpha"}},
            "cash": 0.0,
        }
        signals = [
            {"code": "A", "strategy": "take_profit_tiered", "signal_type": "sell"},
            {"code": "A", "strategy": "stop_loss_basic", "signal_type": "sell"},
        ]
        cb = {"action": None}
        next_day_prices = {"A": 9.0}

        trades = execute_signals(signals, cb, portfolio_state, next_day_prices, "2026-02-01")

        assert len(trades) == 1
        assert trades[0]["reason"] == "stop_loss_basic"
        assert trades[0]["quantity"] == 100  # 全卖

    def test_cash_sits_idle(self):
        """卖出后现金不再投资"""
        portfolio_state = {
            "positions": {"A": {"quantity": 100, "cost_price": 10.0, "name": "Alpha"}},
            "cash": 5000.0,
        }
        signals = [{"code": "A", "strategy": "stop_loss_basic", "signal_type": "sell"}]
        cb = {"action": None}
        next_day_prices = {"A": 9.0}

        execute_signals(signals, cb, portfolio_state, next_day_prices, "2026-02-01")

        # 现金增加了卖出所得
        assert portfolio_state["cash"] == 5000.0 + 900.0
        # 没有新的买入
        assert not portfolio_state["positions"]


class TestParamsOverride:
    """params.py 单元测试"""

    def test_override_restores_on_exit(self):
        """参数覆盖结束后恢复原值"""
        import risk_control.config as cfg
        original = cfg.STOP_LOSS_ATR_MULTIPLIER

        with override_config({"STOP_LOSS_ATR_MULTIPLIER": 99.0}):
            assert cfg.STOP_LOSS_ATR_MULTIPLIER == 99.0

        assert cfg.STOP_LOSS_ATR_MULTIPLIER == original

    def test_override_restores_on_exception(self):
        """异常时也恢复原值"""
        import risk_control.config as cfg
        original = cfg.STOP_LOSS_ATR_MULTIPLIER

        with pytest.raises(ValueError):
            with override_config({"STOP_LOSS_ATR_MULTIPLIER": 99.0}):
                raise ValueError("test")

        assert cfg.STOP_LOSS_ATR_MULTIPLIER == original


class TestEngine:
    """engine.py 集成测试"""

    def test_basic_run_no_crash(self):
        """基本运行不崩溃"""
        portfolio_df = _make_portfolio([("A", "Alpha", 100, 10.0)])
        prices_dict = {"A": _make_prices("A", days=40, base_price=10.0)}

        result = run_backtest(
            portfolio_df=portfolio_df,
            prices_dict=prices_dict,
            start_date="2026-01-20",
            end_date="2026-03-15",
            total_equity=50000,
        )

        assert isinstance(result, BacktestResult)
        assert len(result.daily_snapshots) > 0
        assert result.start_date == "2026-01-20"

    def test_declining_stock_triggers_stop_loss(self):
        """持续下跌的股票应触发止损"""
        portfolio_df = _make_portfolio([("A", "Alpha", 100, 10.0)])
        # 每日跌 1%，60 天后跌约 45%
        prices_dict = {"A": _make_prices("A", days=60, base_price=10.0, trend=-0.01)}

        result = run_backtest(
            portfolio_df=portfolio_df,
            prices_dict=prices_dict,
            start_date="2026-01-20",
            end_date="2026-03-15",
            total_equity=50000,
        )

        # 应该有止损交易
        assert len(result.trades_executed) > 0
        # 止损后现金应该增加
        last_snapshot = result.daily_snapshots[-1]
        assert last_snapshot["cash"] > 0

    def test_rising_stock_no_stop_loss(self):
        """持续上涨的股票不应触发止损"""
        portfolio_df = _make_portfolio([("A", "Alpha", 100, 10.0)])
        # 每日涨 0.5%
        prices_dict = {"A": _make_prices("A", days=60, base_price=10.0, trend=0.005)}

        result = run_backtest(
            portfolio_df=portfolio_df,
            prices_dict=prices_dict,
            start_date="2026-01-20",
            end_date="2026-03-15",
            total_equity=50000,
        )

        # 不应有止损交易（可能有止盈）
        stop_trades = [t for t in result.trades_executed if "stop_loss" in t.get("reason", "")]
        assert len(stop_trades) == 0

    def test_parameter_override_affects_result(self):
        """不同止损参数导致不同触发时间"""
        # 缓慢下跌
        prices_dict = {"A": _make_prices("A", days=60, base_price=10.0, trend=-0.003, volatility=0.1)}

        # 紧止损 (1×ATR, 1×ATR trailing)
        portfolio_tight = _make_portfolio([("A", "Alpha", 100, 10.0)])
        portfolio_tight.at[0, "risk_rules"] = {
            "stop_loss_atr_multiplier": 1.0,
            "trailing_stop_atr_multiplier": 1.0,
        }

        result_tight = run_backtest(
            portfolio_df=portfolio_tight,
            prices_dict=prices_dict,
            start_date="2026-02-01",
            end_date="2026-03-15",
            total_equity=50000,
        )

        # 松止损 (10×ATR, 10×ATR trailing)
        portfolio_loose = _make_portfolio([("A", "Alpha", 100, 10.0)])
        portfolio_loose.at[0, "risk_rules"] = {
            "stop_loss_atr_multiplier": 10.0,
            "trailing_stop_atr_multiplier": 10.0,
        }

        result_loose = run_backtest(
            portfolio_df=portfolio_loose,
            prices_dict=prices_dict,
            start_date="2026-02-01",
            end_date="2026-03-15",
            total_equity=50000,
        )

        # 两者都会触发（持续下跌），但紧止损应该更早触发
        assert len(result_tight.trades_executed) >= 1
        assert len(result_loose.trades_executed) >= 1
        tight_date = result_tight.trades_executed[0]["date"]
        loose_date = result_loose.trades_executed[0]["date"]
        assert tight_date <= loose_date  # 紧止损不晚于松止损


class TestMetrics:
    """metrics.py 单元测试"""

    def test_buy_and_hold_baseline(self):
        """buy-and-hold 基线计算正确"""
        portfolio_df = _make_portfolio([("A", "Alpha", 100, 10.0)])
        prices_dict = {"A": _make_prices("A", days=40, base_price=10.0, trend=0.01)}

        result = run_backtest(
            portfolio_df=portfolio_df,
            prices_dict=prices_dict,
            start_date="2026-01-20",
            end_date="2026-02-20",
            total_equity=50000,
        )

        metrics = compute_metrics(result, prices_dict, 50000)

        # buy-and-hold 收益应该为正（每日涨1%）
        assert metrics["total_return_buy_hold"] > 0
        # 指标结构完整
        assert "max_drawdown_with_rc" in metrics
        assert "drawdown_reduction_pct" in metrics
        assert "signal_accuracy_5d" in metrics

    def test_empty_result_returns_zeros(self):
        """空结果返回零值指标"""
        result = BacktestResult()
        metrics = compute_metrics(result, {}, 50000)
        assert metrics["max_drawdown_with_rc"] == 0.0
        assert metrics["total_trades_executed"] == 0
