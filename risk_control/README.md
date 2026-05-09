# 风控系统

收盘后运行日线级别风控检查，用于晚间复盘和制定第二天交易计划。

## 快速开始

```bash
# 从项目根目录运行，默认读取 portfolio.toml
../quickstart.sh risk

# 如需临时覆盖总权益
../quickstart.sh risk 500000
```

参数说明：
- `总权益`：默认读取 `portfolio.toml` 的 `[account].total_equity`，命令行参数仅用于临时覆盖

### 持仓配置格式

```toml
[account]
total_equity = 500000

[[holdings]]
code = "601216"
name = "君正集团"
market = "上海"
quantity = 9100
cost_price = 5.5243
trade_plan = {status = "active", stop_loss_strategy = "atr"}
```

- `cost_price` 为 0 表示成本未知（如担保品划入），止损会基于当前价计算
- 港股代码 5 位数字，A股代码 6 位

## 三道防线

### 第一道：仓位管理
- 单只个股尽量不超过 20%
- 行业仓位上限 30%
- 根据市场波动率动态调整建议总仓位

### 第二道：止损止盈
- ATR 止损：成本 - 2×ATR(14)
- 可选总权益风险预算止损：默认单票最多亏总权益 2%，可用 `risk_rules.max_loss_pct_of_equity` 覆盖
  例如 10% 仓位允许 20% 跌幅，20% 仓位允许 10% 跌幅
- 分批止盈：+15% 卖 1/3，+30% 再卖 1/3
- 移动止损：近期高点 - 1.5×ATR
- 组合熔断：日亏 3% / 周亏 5% / 月亏 8%

启用方式：

```toml
[[holdings]]
code = "000001"
name = "平安银行"
market = "深圳"
quantity = 1000
cost_price = 12.5
trade_plan = {status = "active", stop_loss_strategy = "equity_risk_budget", plan_note = "单票最多亏总权益2%"}
risk_rules = {max_loss_pct_of_equity = 0.01}
```

计算方式：
- 持仓权重 = `建仓成本 / 总权益`
- 最大亏损比例 = `权益预算比例 / 持仓权重`，默认权益预算比例为 2%
- 止损价 = `成本价 × (1 - 最大亏损比例)`

示例：
- 总权益 50 万，单票建仓 5 万，仓位 10%，最大允许亏损 20%，对应止损价为成本价的 80%
- 总权益 50 万，单票建仓 10 万，仓位 20%，最大允许亏损 10%，对应止损价为成本价的 90%

### 第三道：异常检测
- 波动率突变（短期/长期 > 2.5）
- 流动性枯竭（量比 < 0.3）
- 相关性过高（持仓间 > 0.85）
- 外部冲击（Phase 2）

## 配置

编辑 `config.py` 调整风控参数。关键配置：

```python
MAX_SINGLE_STOCK_WEIGHT = 0.20      # 个股仓位上限
MAX_SINGLE_SECTOR_WEIGHT = 0.30     # 行业仓位上限
STOP_LOSS_ATR_MULTIPLIER = 2.0      # 止损 ATR 倍数
CIRCUIT_BREAKER = {"daily": 0.03, "weekly": 0.05, "monthly": 0.08}
```

## 输出示例

```
═══════════════════════════════════════════════════════
                   风控检查报告 2026-04-13
═══════════════════════════════════════════════════════

📊 组合概览
  总权益: ¥500,000  持仓市值: ¥365,832  现金: ¥134,168
  仓位: 73%  持仓数: 5

🛡️ 第一道防线：仓位管理
  沪深300波动率: 23.1% → 建议仓位 ≤60%
  ⚠️ 当前仓位 73% 超出建议 60%

🎯 第二道防线：止损止盈
  君正集团    5.524  5.550  5.084  +0.5% ✅持有
  互联网      0.874  0.697  0.831 -20.2% 🔴止损

  组合熔断: ✅ 未触发

🔍 第三道防线：异常检测
  ✅ 无异常信号

📋 明日操作建议
  1. 减仓 13% 至建议仓位 60% 以下
  2. 🔴 互联网 已触及止损价 0.831，建议止损
═══════════════════════════════════════════════════════
```

报告同时保存为 `../output/risk_report_YYYYMMDD.md`。

## 日内扩展（Phase 2）

所有计算函数频率无关，接受 `DataFrame[date, open, high, low, close, volume]`。扩展时只需：
1. `shared/data_provider.py` 增加 `get_intraday_prices(code, freq="5min")`
2. `config.py` 设置 `DATA_FREQ = "intraday"`, `MONITOR_INTERVAL = 300`
