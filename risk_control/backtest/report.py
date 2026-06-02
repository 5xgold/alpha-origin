"""JSON + Markdown 报告生成"""

import json
from datetime import datetime
from pathlib import Path


def generate_report(sweep_results, prices_dict, initial_equity, output_dir=None):
    """生成回测报告

    Args:
        sweep_results: list[SweepResult]（已填充 metrics）
        prices_dict: 价格数据（用于指标计算）
        initial_equity: 初始权益
        output_dir: 输出目录，默认 output/

    Returns:
        tuple[Path, Path]: (json_path, markdown_path)
    """
    from risk_control.backtest.metrics import compute_metrics

    if output_dir is None:
        output_dir = Path(__file__).parent.parent.parent / "output"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 计算每个结果的指标
    for sr in sweep_results:
        if not sr.metrics:
            sr.metrics = compute_metrics(sr.result, prices_dict, initial_equity)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    json_path = output_dir / f"backtest_{timestamp}.json"
    md_path = output_dir / f"backtest_{timestamp}.md"

    # 构建 JSON
    report_data = _build_json_report(sweep_results, initial_equity)
    json_path.write_text(
        json.dumps(report_data, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    # 构建 Markdown
    md_content = _build_markdown_report(sweep_results, report_data)
    md_path.write_text(md_content, encoding="utf-8")

    return json_path, md_path


def _build_json_report(sweep_results, initial_equity):
    """构建 JSON 报告结构"""
    results_data = []
    for sr in sweep_results:
        results_data.append({
            "params": sr.params,
            "metrics": sr.metrics,
            "trades_count": len(sr.result.trades_executed),
            "signals_count": len(sr.result.signals_log),
        })

    # 找最优配置
    best_by_dd = min(results_data, key=lambda x: x["metrics"]["max_drawdown_with_rc"])
    best_by_acc = max(results_data, key=lambda x: x["metrics"]["signal_accuracy_5d"])

    period = {}
    if results_data and sweep_results:
        r = sweep_results[0].result
        period = {"start": r.start_date, "end": r.end_date}

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "initial_equity": initial_equity,
        "backtest_period": period,
        "total_combinations": len(results_data),
        "sweep_results": results_data,
        "best_by_metric": {
            "min_drawdown": {"params": best_by_dd["params"], "value": best_by_dd["metrics"]["max_drawdown_with_rc"]},
            "best_accuracy_5d": {"params": best_by_acc["params"], "value": best_by_acc["metrics"]["signal_accuracy_5d"]},
        },
    }


def _build_markdown_report(sweep_results, report_data):
    """构建 Markdown 报告"""
    lines = [
        "# 风控回测报告",
        "",
        f"生成时间: {report_data['generated_at']}",
        f"回测区间: {report_data['backtest_period'].get('start', '')} ~ {report_data['backtest_period'].get('end', '')}",
        f"初始权益: {report_data['initial_equity']:,.0f}",
        f"参数组合数: {report_data['total_combinations']}",
        "",
        "## 最优配置",
        "",
        f"- 最小回撤: {report_data['best_by_metric']['min_drawdown']['params']} → "
        f"{report_data['best_by_metric']['min_drawdown']['value']:.2%}",
        f"- 最高准确率(5日): {report_data['best_by_metric']['best_accuracy_5d']['params']} → "
        f"{report_data['best_by_metric']['best_accuracy_5d']['value']:.1%}",
        "",
        "## 参数对比",
        "",
        "| 止损ATR倍数 | 移动止损ATR倍数 | 最大回撤(风控) | 最大回撤(持有) | 回撤减少 | 收益(风控) | 收益(持有) | 准确率5d | 误杀率5d | 交易次数 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]

    for item in report_data["sweep_results"]:
        p = item["params"]
        m = item["metrics"]
        sl_mult = p.get("STOP_LOSS_ATR_MULTIPLIER", "-")
        trail_mult = p.get("TRAILING_STOP_ATR_MULTIPLIER", "-")
        lines.append(
            f"| {sl_mult} | {trail_mult} "
            f"| {m['max_drawdown_with_rc']:.2%} "
            f"| {m['max_drawdown_buy_hold']:.2%} "
            f"| {m['drawdown_reduction_pct']:.1%} "
            f"| {m['total_return_with_rc']:.2%} "
            f"| {m['total_return_buy_hold']:.2%} "
            f"| {m['signal_accuracy_5d']:.1%} "
            f"| {m['false_positive_rate_5d']:.1%} "
            f"| {m['total_trades_executed']} |"
        )

    lines.extend([
        "",
        "## 关键发现",
        "",
        _generate_findings(report_data),
        "",
        "---",
        f"*由 risk_control/backtest 自动生成*",
    ])

    return "\n".join(lines)


def _generate_findings(report_data):
    """根据数据生成关键发现"""
    results = report_data["sweep_results"]
    if not results:
        return "无数据"

    findings = []

    # 回撤减少是否有效
    avg_dd_reduction = sum(r["metrics"]["drawdown_reduction_pct"] for r in results) / len(results)
    if avg_dd_reduction > 0:
        findings.append(f"- 风控系统平均减少回撤 {avg_dd_reduction:.1%}")
    else:
        findings.append("- 风控系统未能有效减少回撤，需检查参数或市场环境")

    # 收益影响
    avg_return_impact = sum(r["metrics"]["return_impact"] for r in results) / len(results)
    if avg_return_impact < -0.02:
        findings.append(f"- 风控执行平均降低收益 {abs(avg_return_impact):.1%}（止损代价）")
    elif avg_return_impact > 0.02:
        findings.append(f"- 风控执行平均提升收益 {avg_return_impact:.1%}（避免了更大亏损）")

    # 最优 ATR 倍数
    best = report_data["best_by_metric"]["min_drawdown"]
    sl_mult = best["params"].get("STOP_LOSS_ATR_MULTIPLIER", "?")
    findings.append(f"- 最优止损 ATR 倍数: {sl_mult}（回撤最小）")

    return "\n".join(findings)
