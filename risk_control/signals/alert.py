"""分级预警体系 — 关注 / 警告 / 危险"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))
from risk_control.config import ALERT_ESCALATION_DAYS

# 预警级别定义
ALERT_LEVELS = {
    "watch": {"label": "关注", "emoji": "👀", "order": 0},
    "warning": {"label": "警告", "emoji": "⚠️", "order": 1},
    "danger": {"label": "危险", "emoji": "🔴", "order": 2},
}

LEVEL_ORDER = ["watch", "warning", "danger"]


def escalate_level(base_level, trigger_days):
    """根据持续天数升级预警级别

    Args:
        base_level: 策略给出的基础级别
        trigger_days: 信号持续天数

    Returns:
        str: 升级后的级别
    """
    idx = LEVEL_ORDER.index(base_level) if base_level in LEVEL_ORDER else 0

    if trigger_days >= ALERT_ESCALATION_DAYS.get("warning_to_danger", 5):
        idx = max(idx, 2)
    elif trigger_days >= ALERT_ESCALATION_DAYS.get("watch_to_warning", 3):
        idx = max(idx, 1)

    return LEVEL_ORDER[idx]


def classify_alerts(signals):
    """将信号按预警级别分组

    Args:
        signals: list[dict] — 统一格式的信号列表

    Returns:
        dict: {"danger": [...], "warning": [...], "watch": [...]}
    """
    groups = {level: [] for level in LEVEL_ORDER}
    for sig in signals:
        level = sig.get("alert_level", "watch")
        if level not in groups:
            level = "watch"
        groups[level].append(sig)
    return groups


def format_alert_section(alert_groups):
    """格式化信号报告区块

    Args:
        alert_groups: classify_alerts() 的输出

    Returns:
        list[str]: 报告行
    """
    total = sum(len(v) for v in alert_groups.values())
    if total == 0:
        return ["📋 信号系统", "  ✅ 无活跃信号"]

    lines = [f"📋 信号系统 ({total}条信号)"]

    # 按 danger → warning → watch 顺序输出
    for level in reversed(LEVEL_ORDER):
        sigs = alert_groups[level]
        if not sigs:
            continue

        meta = ALERT_LEVELS[level]
        lines.append("")
        lines.append(f"  {meta['emoji']} {meta['label']} ({len(sigs)})")

        for sig in sigs:
            trigger_tag = _trigger_tag(sig)
            lines.append(f"    {sig['name']}: {sig['title']} {trigger_tag}")

            # warning / danger 级别显示详情和应对方案
            if level in ("warning", "danger"):
                if sig.get("detail"):
                    lines.append(f"    → {sig['detail']}")
                if sig.get("response_plan"):
                    lines.append(f"    → {sig['response_plan']}")
            elif sig.get("detail"):
                # watch 级别只显示简要详情
                lines.append(f"    → {sig['detail']}")

    return lines


def _trigger_tag(sig):
    """生成触发标记：[首次] 或 [持续N天]"""
    count = sig.get("trigger_count", 1)
    if count <= 1:
        return "[首次]"
    first = sig.get("first_triggered", "")
    if first:
        return f"[持续自{first}]"
    return f"[第{count}次]"
