# 策略归因分析框架

用于复盘一段时间内的实盘交易，回答两个问题：

- 账户这段时间真实赚了多少钱，净值曲线体验如何
- 剔除外部资金流后，交易决策本身是否跑赢了基准

## 快速开始

### 1. 安装依赖

```bash
cd attribution_analysis
pip install -r requirements.txt
```

### 2. 准备数据

从券商导出 PDF 对账单，放到 `data/raw/` 目录。当前支持：东方证券。

### 3. 转换数据

```bash
python ../shared/convert_broker_data.py \
    --input data/raw/交割单.pdf \
    --output-dir data
```

转换后会得到：

- `data/trades.csv`
- `data/holdings.csv`
- `data/cash_flows.csv`

### 4. 运行分析

```bash
python scripts/attribution.py \
    --trades data/trades.csv \
    --holdings data/holdings.csv \
    --cash-flows data/cash_flows.csv \
    --start-date 2025-01-01 \
    --end-date 2026-03-31 \
    --output ../output/report.md
```

### 5. 查看报告

直接打开 `../output/report.md`。

## 双口径说明

模块同时输出两套结果：

- 账户净值口径：包含银证转账、担保品划转等外部资金流。用于观察真实账户净值、账户盈亏和账户回撤。
- 策略收益口径：剔除外部资金流后的时间加权收益率（TWR）。用于计算 Alpha / Beta / Sharpe / 最大回撤 / 超额收益。

建议：

- 判断自己是否适合做量化，优先看策略收益口径
- 判断账户这段时间实际体验，参考账户净值口径

## 数据格式

标准 `trades.csv` 示例：

```csv
date,market,code,name,direction,quantity,price,amount,brokerage_fee,stamp_duty,transfer_fee,other_fee,net_amount,remark
20260103,沪深市场,600519,贵州茅台,买入,100,1680.00,168000.00,50.40,0,16.80,0,-168067.20,
```

更多格式约定见 [docs/csv-format.md](docs/csv-format.md)。

## 配置

编辑 `config.py` 可调整：

- 基准指数或复合基准配置
- 无风险利率
- 报告输出目录

## 输出内容

### 终端输出

```
==================================================
策略归因分析报告
分析区间：2025-01-01 至 2026-03-31
==================================================

【账户表现】
账户期末净值：     +1,025,000.00
净流入/流出：      +120,000.00
账户盈亏：         +25,000.00
账户净值收益率：    +14.8%
账户最大回撤：     9.2%

【策略表现】
时间加权收益率(TWR)：+2.5%
策略总收益率：     +2.5%
基准总收益率：     -8.0%
超额收益率：       +10.5%

Alpha（年化）：    +8.2%  ✓ 策略有效
Beta：             0.95   ✓ 市场敏感度正常
R²：               0.78   ✓ 模型拟合良好

夏普比率：         1.35
最大回撤：         12.3%
年化波动率：       18.5%
```

### Markdown 报告

包含：

- 账户净值口径指标
- 策略收益口径指标
- Brinson 归因表格（BHB 模型）
- 月度策略收益与超额收益
- 综合结论

## 常见问题

### Q: 为什么净值收益率和 TWR 不一样？

A: 两者回答的问题不同。

- 净值收益率包含外部资金流，反映真实账户体验
- TWR 剔除外部资金流，反映交易决策本身的收益质量

### Q: PDF 解析失败怎么办？

A: 检查 PDF 是否包含可提取表格。如果是扫描件，需要先做 OCR。

### Q: 某只股票获取行情失败怎么办？

A: 可能是代码格式问题、停牌或退市。检查 `../data/cache/`，必要时补充本地价格数据。

### Q: Alpha 为负说明什么？

A: 说明策略收益口径下跑输基准。常见原因：

1. 选股没有持续优势
2. 交易成本过高
3. 市场暴露和持仓节奏不合理

## 技术栈

- Python 3.14
- baostock
- futu-api
- pandas
- statsmodels
- pdfplumber

## 项目结构

```text
attribution_analysis/
├── config.py
├── requirements.txt
├── quickstart.sh
├── README.md
├── data/
│   ├── raw/
│   ├── trades.csv
│   ├── holdings.csv
│   └── cash_flows.csv
├── docs/
│   ├── architecture.md
│   └── csv-format.md
├── scripts/
│   ├── attribution.py
│   └── brinson.py
└── tests/
    └── test_attribution.py
```

> PDF 解析脚本在 `../shared/convert_broker_data.py` 和 `../shared/pdf_portfolio.py`。
> 行情数据获取在 `../shared/data_provider.py`，缓存目录为 `../data/cache/`。
> 报告输出到 `../output/`。

## 许可证

MIT License
