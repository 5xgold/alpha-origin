# CSV 数据格式规范

## trades.csv — 交易流水

由 `convert_broker_data.py` 从 PDF 对账单「客户资金明细」解析生成。

| 列名 | 类型 | 说明 | 示例 |
|------|------|------|------|
| date | str | 交易日期 (YYYYMMDD) | 20260115 |
| market | str | 市场 | 上海 / 深圳 / 沪港通 |
| code | str | 证券代码 | 601318 |
| name | str | 证券名称 | 中国平安 |
| direction | str | 交易方向 | 买入 / 卖出 / 分红 / 扣税 |
| quantity | float | 成交数量 | 100 |
| price | float | 成交均价 | 45.50 |
| amount | float | 成交金额（买入为负） | -4550.00 |
| brokerage_fee | float | 佣金 | 5.00 |
| stamp_duty | float | 印花税 | 0.00 |
| transfer_fee | float | 过户费 | 0.46 |
| other_fee | float | 其他费用 | 0.00 |
| net_amount | float | 实际收付金额 | -4555.46 |
| remark | str | 备注 | 证券买入 |

共 14 列，对应 `config.STANDARD_COLUMNS`。

---

## holdings.csv — 期末持仓快照

由 `convert_broker_data.py` 从 PDF 对账单「客户持股清单」解析生成。

| 列名 | 类型 | 说明 | 示例 |
|------|------|------|------|
| code | str | 证券代码 | 601318 |
| name | str | 证券名称 | 中国平安 |
| market | str | 市场 | 上海 / 深圳 / 沪港通 |
| quantity | int | 持仓数量 | 200 |
| cost_price | float | 参考成本价 | 45.50 |

共 5 列。

---

## cash_flows.csv — 外部资金流

由 `convert_broker_data.py` 从 PDF 原始流水中提取银证转账和担保品划转。

| 列名 | 类型 | 说明 | 示例 |
|------|------|------|------|
| date | str | 日期 (YYYY-MM-DD) | 2026-01-15 |
| amount | float | 金额（转入为正，转出为负） | 50000.00 |
| type | str | 类型 | 银行转存 / 银行转取 / 担保品划出 / 担保品划入 |

共 3 列。同一天同类型的多笔流水会按日汇总。

### 用途说明

`cash_flows.csv` 会同时影响两类指标：

- 账户净值口径：会计入净流入/净流出，影响真实账户净值曲线
- 策略收益口径：会被剔除，用于计算 TWR / Alpha / Beta / Sharpe

如果不提供该文件：

- 策略收益口径仍可运行，但默认认为无外部资金流
- 账户净值口径会退化为仅基于交易现金变化的近似结果

---

## 数据来源映射

| CSV 文件 | PDF 来源段落 | 解析函数 |
|----------|-------------|---------|
| trades.csv | 客户资金明细（17列标准行 + 15列无代码行） | `parse_pdf()` → `normalize_columns()` |
| holdings.csv | 客户持股清单 | `parse_shareholding()` |
| cash_flows.csv | 客户资金明细中的银证转账 + 担保品划转 | `extract_cash_flows()` |
