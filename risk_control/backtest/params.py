"""参数覆盖上下文管理器 + 扫描编排"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import product

import risk_control.config as cfg
from risk_control.backtest.engine import run_backtest, BacktestResult


@dataclass
class SweepResult:
    params: dict
    result: BacktestResult
    metrics: dict = field(default_factory=dict)


@contextmanager
def override_config(params: dict):
    """临时 patch risk_control.config 模块级常量，结束后恢复"""
    originals = {}
    for key, value in params.items():
        if hasattr(cfg, key):
            originals[key] = getattr(cfg, key)
            setattr(cfg, key, value)
    try:
        yield
    finally:
        for key, value in originals.items():
            setattr(cfg, key, value)


# 默认扫描参数
DEFAULT_SWEEP_PARAMS = {
    "STOP_LOSS_ATR_MULTIPLIER": [1.0, 1.5, 2.0, 2.5, 3.0],
    "TRAILING_STOP_ATR_MULTIPLIER": [1.0, 1.5, 2.0, 2.5],
}


def run_parameter_sweep(
    portfolio_df,
    prices_dict,
    start_date,
    end_date,
    total_equity,
    sweep_params=None,
    market_regime="neutral",
):
    """对参数组合做笛卡尔积扫描

    Args:
        sweep_params: {param_name: [v1, v2, ...]}，默认扫描 ATR 乘数

    Returns:
        list[SweepResult]
    """
    if sweep_params is None:
        sweep_params = DEFAULT_SWEEP_PARAMS

    param_names = list(sweep_params.keys())
    param_values = list(sweep_params.values())
    combinations = list(product(*param_values))

    results = []
    for combo in combinations:
        params = dict(zip(param_names, combo))
        with override_config(params):
            bt_result = run_backtest(
                portfolio_df=portfolio_df,
                prices_dict=prices_dict,
                start_date=start_date,
                end_date=end_date,
                total_equity=total_equity,
                market_regime=market_regime,
            )
        results.append(SweepResult(params=params, result=bt_result))

    return results
