# 数据流架构

## 总览

```
PDF 对账单
    │
    ▼
convert_broker_data.py --input PDF --output-dir data/
    │
    ├── data/trades.csv      (STANDARD_COLUMNS, 14列)
    ├── data/holdings.csv    (code,name,market,quantity,cost_price)
    └── data/cash_flows.csv  (date,amount,type)

    ▼
attribution.py --trades data/trades.csv \
               --holdings data/holdings.csv \
               --cash-flows data/cash_flows.csv
    │
    ├── load_trades() → rebuild_positions() → calculate_portfolio_value()
    ├── get_benchmark_prices()
    ├── calculate_returns() → alpha_beta_analysis()
    ├── calculate_twr() (if cash_flows provided)
    ├── brinson_analysis()
    └── output/report.md
```

## 职责划分

| 脚本 | 职责 | 输入 | 输出 |
|------|------|------|------|
| `convert_broker_data.py` | PDF 解析 → 标准数据文件 | PDF 对账单 | trades.csv, holdings.csv, cash_flows.csv |
| `attribution.py` | 归因分析（纯 CSV 消费） | CSV 数据文件 | 分析报告 (.md) |
| `pdf_portfolio.py` | PDF 解析库（被 convert 引用） | — | — |
| `brinson.py` | Brinson 归因计算 | 持仓快照 + 行情 | 归因结果 |
| `data_provider.py` | 行情数据获取（多源 fallback） | 股票代码 + 日期 | 价格序列 |

## 设计原则

- **解析与分析解耦**：`convert_broker_data.py` 负责 PDF → CSV，`attribution.py` 只消费 CSV，不感知 PDF
- **一次解析，多次分析**：PDF 解析跑一次产出标准数据文件，分析可反复运行
- **标准中间格式**：所有数据通过 CSV 传递，格式见 [csv-format.md](csv-format.md)
