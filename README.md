# 量化投资工具集

个人量化投资分析工具，覆盖事后归因、实时风控、异常预警三道防线。

## 架构总览

```
PythonProjects/
├── attribution_analysis/    # 第一道：策略归因分析（已完成）
├── anomaly_detection/       # 第三道：异常检测（规划中）
└── docs/
    └── superpowers/         # 设计文档 & 实现计划
```

## 三道防线

### ✅ 第一道：策略归因分析（事后复盘）

分析实盘交易的收益来源，区分 Alpha 和 Beta。

- PDF 交割单自动解析（东方证券）
- 持仓重建 & 每日市值计算
- Alpha/Beta 回归分析（CAPM）
- Brinson 行业归因（BHB 模型）
- 多数据源行情：A股 baostock / 港股 FutuOpenD → Yahoo → 东方财富

```bash
cd attribution_analysis
python scripts/convert_broker_data.py --input data/raw/对账单.pdf --output data/trades.csv
python scripts/attribution.py --trades data/trades.csv --holdings data/holdings.csv \
    --start-date 2026-01-05 --end-date 2026-03-27
```

详见 [attribution_analysis/README.md](attribution_analysis/README.md)

### 🔲 第二道：实时风控（规划中）

### 🔲 第三道：异常检测 — 黑天鹅预警（规划中）

每 5 分钟检查，多信号联合触发：

| 信号 | 触发条件 |
|------|---------|
| 🔴 波动率突变 | 当日波动率 / 过去 20 日均值 > 2.5 |
| 🔴 流动性枯竭 | 买卖价差 > 过去 20 日均值的 3 倍 |
| 🔴 相关性崩塌 | 持仓相关性突然趋向 1.0 |
| 🔴 外部冲击 | 30 分钟内相关新闻数量突增 500% |

触发逻辑：1 个 🔴 预警 / 2 个 🔴 减仓 50% / 3 个 🔴 全部清仓

## 数据源

| 市场 | 数据源 | 备注 |
|------|--------|------|
| A股行情 | baostock | 不复权 |
| 港股行情 | FutuOpenD → Yahoo Finance → 东方财富 | 多源 fallback，不复权 |
| 指数/行业 | baostock + 东方财富 | 成分股 & 申万行业指数 |

## 技术栈

Python 3.14 / pandas / statsmodels / baostock / futu-api / yfinance / pdfplumber / pyecharts
