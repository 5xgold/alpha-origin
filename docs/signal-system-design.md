# 信号插件系统设计文档

**版本**: v0.7.0 | **日期**: 2026-04-29

## 1. 设计动机

原有风控模块（三道防线）是"静态快照"模式：每次运行独立计算止损/止盈价位，不记录历史状态。存在三个问题：

1. **无状态** — 止盈触发后下次运行仍重复提示，无法区分"首次触发"和"持续触发"
2. **无分级** — 所有风险信号同等对待，缺少紧急程度区分
3. **不可扩展** — 新增策略需要修改 stop_loss.py 核心逻辑，耦合度高

## 2. 架构决策

### 2.1 叠加而非替换

信号系统作为**第四层**叠加在现有三道防线之上，不修改 Defense 1/2/3 的核心逻辑。

```
Defense 1: 仓位管理 (不变)
Defense 2: 止损止盈 (不变，输出 sl_levels)
Defense 3: 异常检测 (不变)
    ↓
Signal System (新增)
    ├── 读取 sl_levels → 包装为统一信号格式
    ├── 运行新策略 → 生成额外信号
    ├── 状态追踪 → 首次/持续标记
    └── 分级预警 → 关注/警告/危险
```

**为什么不重构 stop_loss.py？**
- 现有代码经过测试验证，稳定运行
- 信号系统是增强层，不是替代层
- 如果信号系统出问题，三道防线仍然正常工作

### 2.2 注册表模式（非继承）

选择函数注册表而非类继承，原因：
- 项目风格是函数式（函数返回 dict/list），不是 OOP
- 注册表模式更轻量，新增策略只需一个装饰器
- 策略间无共享状态，不需要继承

```python
@register_signal("my_strategy", signal_type="sell")
def check(portfolio_df, prices_dict, *, state, **kwargs):
    return [...]  # 统一信号格式
```

### 2.3 统一信号格式

所有策略输出相同结构，便于报告渲染和后续处理：

```python
{
    "code": "601985",
    "name": "中国核电",
    "strategy": "dynamic_stop_upgrade",
    "signal_type": "sell",          # buy / sell / alert
    "alert_level": "watch",         # watch / warning / danger
    "title": "止损升级至保本价",
    "detail": "盈利5.2%，止损从8.39升至8.69",
    "response_plan": "止损价上移，无需操作",
    "first_triggered": "2026-04-29",
    "trigger_count": 1,
}
```

## 3. 分级预警体系

### 三个级别

| 级别 | 含义 | 报告详细度 |
|------|------|-----------|
| 👀 关注 | 信息提示，无需立即行动 | 一行摘要 + 简要详情 |
| ⚠️ 警告 | 需要关注，建议准备应对 | 摘要 + 依据 + 建议操作 |
| 🔴 危险 | 需要立即行动 | 完整应对方案 |

### 升级机制

同一信号持续触发会自动升级：
- 持续 3 天：watch → warning
- 持续 5 天：warning → danger

基础止损信号直接从 danger 开始，不需要升级。

## 4. 市场多空区间

### 设计思想

同一套止损/止盈参数在多头和空头市场应有不同松紧度：
- **多头区间**：手松，给持仓更多空间（止损放宽、止盈目标放大）
- **空头区间**：手紧，快速止损保护本金（止损收紧、止盈目标缩小）
- **震荡区间**：默认参数，不调节

### 实现方式

通过乘数调节，不改变基础参数：

```python
MARKET_REGIME_PROFILES = {
    "bull":    {"stop_loss_multiplier": 1.3, "take_profit_multiplier": 1.5, ...},
    "bear":    {"stop_loss_multiplier": 0.7, "take_profit_multiplier": 0.7, ...},
    "neutral": {"stop_loss_multiplier": 1.0, "take_profit_multiplier": 1.0, ...},
}
CURRENT_MARKET_REGIME = "neutral"  # 手动切换
```

乘数作用于 ATR 倍数和止盈百分比：
- 止损价 = 成本 - (2×ATR × stop_loss_multiplier)
- 止盈触发 = 成本 × (1 + 15% × take_profit_multiplier)
- 移动止损 = 最高价 - (1.5×ATR × trailing_stop_multiplier)
- 熔断阈值 = 基础阈值 × circuit_breaker_multiplier

当前为手动设置，未来可接入自动多空判断（如均线系统、市场宽度指标）。

## 5. 六个策略插件

### 迁移策略（包装现有逻辑）

| 策略 | 来源 | 信号类型 | 基础级别 |
|------|------|---------|---------|
| stop_loss_basic | sl_levels.signal == "stop_loss" | sell | danger |
| take_profit_tiered | sl_levels.signal == "take_profit" | sell | warning |
| trailing_stop | sl_levels.signal == "trailing_stop" | sell | warning |

### 新增策略

| 策略 | 功能 | 信号类型 | 基础级别 |
|------|------|---------|---------|
| dynamic_stop_upgrade | 盈利阶段自动提升止损价 | sell/alert | watch→warning |
| holding_period | 持仓周期管理（停滞/亏损/走弱） | alert | watch→danger |
| add_position | 金字塔加仓（支撑位+仓位约束） | buy | watch |

### 动态止损升级详解

止损价只升不降，分三个阶段：

```
Phase 0 (默认):  止损 = 成本 - 2×ATR
Phase 1 (盈利≥5%):  止损 = 成本价（保本）
Phase 2 (盈利≥15%): 止损 = 成本 × 1.08
Phase 3 (盈利≥25%): 止损 = 最高价 - 1.0×ATR（紧移动止损）
```

阶段记录在 risk_state.json 中，即使盈利回落也不降级。

### 加仓策略约束

加仓建议受三重约束：
1. 距成本至少跌 5% 才考虑
2. 价格在支撑位 3% 范围内
3. 当前仓位未达熟悉程度上限

金字塔递减：第一次加仓 50%，第二次 25%，之后不再建议。

## 6. 状态追踪

### 文件位置

`data/cache/risk_state.json` — 随 data/cache/ 目录被 gitignore。

### 设计原则

- **轻量**：单个 JSON 文件，不引入数据库
- **幂等**：同一天多次运行不会重复计数
- **自愈**：文件损坏或缺失时自动重建
- **清理**：已不在持仓中的股票记录自动清除

### 状态内容

```json
{
  "_meta": {"version": 1, "last_updated": "..."},
  "signals": {
    "601985": {
      "dynamic_stop_upgrade": {
        "phase": 1, "stop_price": 8.688,
        "first_triggered": "2026-04-28",
        "last_triggered": "2026-04-29",
        "trigger_count": 2
      }
    }
  },
  "holdings_first_seen": {"601985": "2026-02-15"}
}
```

## 7. 扩展指南

### 添加新策略

1. 在 `risk_control/signals/strategies/` 下创建新文件
2. 用 `@register_signal` 装饰器注册
3. 在 `strategies/__init__.py` 中添加导入
4. 在 `config.py` 中添加配置常量（如需要）

```python
# risk_control/signals/strategies/my_strategy.py
from risk_control.signals.registry import register_signal

@register_signal("my_strategy", signal_type="sell")
def check(portfolio_df, prices_dict, *, state, **kwargs):
    signals = []
    # ... 策略逻辑 ...
    signals.append({
        "code": ..., "name": ...,
        "strategy": "my_strategy",
        "signal_type": "sell",
        "alert_level": "warning",
        "title": ..., "detail": ..., "response_plan": ...,
        "first_triggered": ..., "trigger_count": ...,
    })
    return signals
```

### 未来方向

- **观察列表**：买入信号策略 + 每日复盘集成
- **自动多空判断**：基于均线/市场宽度自动切换 CURRENT_MARKET_REGIME
- **回测框架**：信号历史回测，验证策略有效性
