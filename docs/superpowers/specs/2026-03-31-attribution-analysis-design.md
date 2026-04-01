# 策略归因分析框架设计文档

**版本**: v1.0
**日期**: 2026-03-31
**目标**: 实现 Alpha/Beta 分离分析，自动生成归因报告

---

## 一、需求背景

### 业务目标
- 分析 2025-2026 年实盘交易收益来源（2025年盈利15%，2026年回撤10%）
- 区分市场收益（Beta）和策略超额收益（Alpha）
- 为后续策略优化提供数据支撑

### 用户画像
- 短线交易风格（100+笔/年）
- 使用券商交割单记录交易
- 需要手动运行分析，输出终端 + HTML 报告

---

## 二、整体架构

### 2.1 目录结构

```
attribution_analysis/
├── data/
│   ├── trades.csv              # 标准格式交割单
│   ├── raw/                    # 券商原始导出文件
│   └── cache/                  # AKShare 数据缓存
├── scripts/
│   ├── convert_broker_data.py  # 券商数据转换脚本
│   └── attribution.py          # 核心分析脚本
├── templates/
│   └── report_template.html    # HTML 报告模板
├── output/
│   ├── YYYY-MM-DD_report.html  # 生成的报告
│   └── YYYY-MM-DD_data.json    # 中间数据
├── config.py                   # 配置文件
├── requirements.txt            # 依赖
└── README.md                   # 使用说明
```

### 2.2 技术栈

| 组件 | 技术选型 | 用途 |
|------|---------|------|
| 数据源 | AKShare | 获取 A 股行情、指数数据 |
| 数据处理 | pandas + numpy | 持仓重建、收益率计算 |
| 统计分析 | statsmodels | Alpha/Beta 回归分析 |
| 可视化 | pyecharts | 生成交互式 HTML 图表 |
| PDF 解析 | pdfplumber | 提取券商 PDF 交割单表格 |
| 图片识别 | 多模态 LLM（备选） | 截图识别交割单 |

---

## 三、数据格式定义

### 3.1 标准交割单格式（trades.csv）

```csv
date,market,code,name,direction,quantity,price,amount,brokerage_fee,stamp_duty,transfer_fee,other_fee,net_amount,remark
20260103,沪深市场,600519,贵州茅台,买入,100,1680.00,168000.00,50.40,0,16.80,0,-168067.20,
20260315,沪深市场,600519,贵州茅台,卖出,100,1780.00,178000.00,53.40,178.00,17.80,0,177750.80,
```

**字段说明**：

| 字段 | 类型 | 说明 | 示例 |
|------|------|------|------|
| date | str | 成交日期（YYYYMMDD） | 20260103 |
| market | str | 股票市场 | 沪深市场 |
| code | str | 证券代码 | 600519 |
| name | str | 证券名称 | 贵州茅台 |
| direction | str | 买入/卖出 | 买入 |
| quantity | int | 成交数量（股） | 100 |
| price | float | 成交均价（元） | 1680.00 |
| amount | float | 成交金额（元） | 168000.00 |
| brokerage_fee | float | 手续费（元） | 50.40 |
| stamp_duty | float | 印花税（元） | 0 |
| transfer_fee | float | 过户费（元） | 16.80 |
| other_fee | float | 其他费用（元） | 0 |
| net_amount | float | 实际收付金额（元，买入为负） | -168067.20 |
| remark | str | 备注 | |

### 3.2 数据导入方式

#### 方式1：PDF 解析（主路径）

券商导出 PDF 交割单，使用 pdfplumber 提取表格数据。

```python
# 使用方式
python scripts/convert_broker_data.py \
    --input data/raw/交割单.pdf \
    --output data/trades.csv
```

**实现**：
```python
import pdfplumber

def parse_broker_pdf(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        all_rows = []
        for page in pdf.pages:
            table = page.extract_table()
            if table:
                all_rows.extend(table[1:])  # 跳过表头
    # 自动识别列名映射，转换为标准格式
    return normalize_columns(all_rows)
```

**列名自动匹配**：
- 脚本自动识别 PDF 表头（如"成交日期"/"Starting Date"）
- 映射到标准字段名
- 支持中英文混合表头


#### 方式2：截图识别（备选）

直接截图券商 APP 页面，使用多模态 LLM 提取数据。

```python
# 使用方式
python scripts/convert_broker_data.py \
    --input data/raw/screenshot.png \
    --mode ocr \
    --output data/trades.csv
```

**实现**：
- 调用多模态 LLM API（DeepSeek-VL / 通义千问 VL / Claude）
- Prompt：提取表格中的交割单数据，输出 JSON 格式
- 自动转换为标准 CSV

**成本**：约 ¥0.1-0.5 / 张图片

**注意事项**：
- 截图需完整包含表头和数据行
- 识别结果会打印到终端供用户确认
- 建议用于少量数据补录，大批量数据优先用 PDF

---

## 四、核心分析逻辑

### 4.1 数据处理流程

```
券商PDF/截图 → 数据提取(pdfplumber/LLM) → 标准CSV → 持仓重建 → 收益率计算 → Alpha/Beta分离 → 报告生成
```

### 4.2 持仓重建算法

**输入**：交割单（按时间排序）
**输出**：每日持仓快照

```python
# 伪代码
positions = {}  # {code: {quantity, cost_basis}}
daily_snapshots = []

for trade in trades:
    if trade.direction == '买入':
        positions[trade.code].quantity += trade.quantity
        positions[trade.code].cost_basis += trade.net_amount
    else:  # 卖出
        positions[trade.code].quantity -= trade.quantity
        # 成本按 FIFO 计算

    # 每日收盘后记录快照
    if is_trading_day_end(trade.date):
        snapshot = calculate_portfolio_value(positions, market_prices)
        daily_snapshots.append(snapshot)
```

**处理特殊情况**：
- 分红：增加现金，不改变持仓数量
- 配股：增加持仓数量，增加成本
- 停牌：使用停牌前最后价格估值

### 4.3 收益率计算

**组合日收益率**：
```python
R_p[t] = (V[t] - V[t-1] - CF[t]) / V[t-1]
```
- `V[t]`: t 日组合市值
- `CF[t]`: t 日净现金流（买入为负，卖出为正）

**基准日收益率**（中证全指）：
```python
R_m[t] = (Index[t] - Index[t-1]) / Index[t-1]
```

**无风险利率**：
- 使用 3 个月国债收益率（年化 2.5%）
- 日化：`rf_daily = (1 + 0.025)^(1/252) - 1`

### 4.4 Alpha/Beta 分离（CAPM 模型）

**回归模型**：
```
R_p - rf = α + β * (R_m - rf) + ε
```

**实现**：
```python
import statsmodels.api as sm

# 计算超额收益率
excess_portfolio = portfolio_returns - risk_free_rate
excess_benchmark = benchmark_returns - risk_free_rate

# OLS 回归
X = sm.add_constant(excess_benchmark)
model = sm.OLS(excess_portfolio, X).fit()

alpha_daily = model.params[0]
beta = model.params[1]
r_squared = model.rsquared

# 年化 Alpha
alpha_annual = alpha_daily * 252
```

**输出指标**：
- **Alpha（年化）**：策略超额收益率
- **Beta**：市场敏感度（1.0 = 和市场同步）
- **R²**：模型拟合度（>0.7 说明市场因素解释力强）

### 4.5 结果解读逻辑

| 场景 | Alpha | Beta | 解读 |
|------|-------|------|------|
| 场景1 | +5% | 0.8 | 策略有效，且比市场波动小（防御性） |
| 场景2 | +5% | 1.2 | 策略有效，但承担了更高风险 |
| 场景3 | -2% | 1.0 | 策略无效，纯粹跟随市场 |
| 场景4 | +2% | 0.3 | 低相关策略，可能是行业轮动 |

**针对用户 2025-2026 年数据的诊断**：
- 如果 2025 年 Alpha > 0，Beta ≈ 1 → 策略有效
- 如果 2026 年 Alpha < 0，Beta > 1 → 市场下跌时策略失效且放大了损失
- 如果 2026 年 Alpha > 0，Beta ≈ 1 → 策略有效，回撤是市场拖累

---

## 五、报告输出设计

### 5.1 终端输出

```
========================================
策略归因分析报告
分析区间：2025-01-01 至 2026-03-31
========================================

【核心指标】
组合总收益率：     +2.5%
基准总收益率：     -8.0%
超额收益率：       +10.5%

Alpha（年化）：    +8.2%  ✓ 策略有效
Beta：             0.95   ✓ 市场敏感度正常
R²：               0.78   ✓ 模型拟合良好

夏普比率：         1.35
最大回撤：         -12.3%
年化波动率：       18.5%

【收益归因】
市场贡献（Beta）： -7.6%
策略贡献（Alpha）： +10.1%

【结论】
策略在下跌市场中表现优异，Alpha 显著为正。
2026 年回撤主要由市场下跌导致，策略本身有效。

详细报告已生成：output/2026-03-31_report.html
========================================
```

### 5.2 HTML 报告结构

**页面布局**：

```
┌─────────────────────────────────────┐
│  策略归因分析报告                    │
│  2025-01-01 至 2026-03-31           │
├─────────────────────────────────────┤
│  【概览指标卡片】                    │
│  ┌──────┐ ┌──────┐ ┌──────┐        │
│  │总收益│ │Alpha │ │Beta  │        │
│  │+2.5% │ │+8.2% │ │0.95  │        │
│  └──────┘ └──────┘ └──────┘        │
├─────────────────────────────────────┤
│  【净值曲线】                        │
│  组合 vs 基准 折线图                 │
├─────────────────────────────────────┤
│  【月度超额收益】                    │
│  柱状图                              │
├─────────────────────────────────────┤
│  【滚动 Beta】                       │
│  60日窗口 折线图                     │
├─────────────────────────────────────┤
│  【月度归因表格】                    │
│  月份 | 组合收益 | 基准收益 | Alpha │
├─────────────────────────────────────┤
│  【持仓分析】                        │
│  前5大持仓 + 行业分布饼图            │
└─────────────────────────────────────┘
```

**图表技术**：
- 使用 pyecharts 生成交互式图表
- 支持缩放、数据点悬停显示
- 响应式布局，支持移动端查看

---

## 六、配置文件设计

**config.py**：

```python
# 基准配置
BENCHMARK_INDEX = "000985"  # 中证全指代码
RISK_FREE_RATE = 0.025      # 年化无风险利率

# 数据缓存
CACHE_DIR = "data/cache"
CACHE_EXPIRY_DAYS = 7       # 缓存过期天数

# 分析参数
ROLLING_WINDOW = 60         # 滚动 Beta 窗口（交易日）
MIN_TRADING_DAYS = 30       # 最少交易日数（少于此数不分析）

# 报告配置
REPORT_TITLE = "策略归因分析报告"
OUTPUT_DIR = "output"

# 券商映射（用于数据转换）
BROKER_COLUMN_MAPPING = {
    "huatai": {
        "成交日期": "date",
        "证券代码": "code",
        "证券名称": "name",
        # ...
    },
    "citic": {
        # ...
    }
}
```

---

## 七、使用流程

### 7.1 初始化

```bash
# 1. 创建虚拟环境
cd /Users/5xgold/PythonProjects
python3 -m venv venv
source venv/bin/activate

# 2. 安装依赖
pip install -r attribution_analysis/requirements.txt

# 3. 准备数据目录
mkdir -p attribution_analysis/data/raw
mkdir -p attribution_analysis/data/cache
mkdir -p attribution_analysis/output
```

### 7.2 数据准备

```bash
# 1. 从券商导出交割单，放到 data/raw/

# 2. 转换为标准格式
python scripts/convert_broker_data.py \
    --input data/raw/交割单.xlsx \
    --broker huatai \
    --output data/trades.csv

# 3. 检查数据
head data/trades.csv
```

### 7.3 运行分析

```bash
# 运行归因分析
python scripts/attribution.py \
    --trades data/trades.csv \
    --start-date 2025-01-01 \
    --end-date 2026-03-31 \
    --output output/2026-03-31_report.html

# 查看报告
open output/2026-03-31_report.html
```

---

## 八、错误处理

### 8.1 数据质量检查

**检查项**：
- 交割单日期连续性（是否有缺失）
- 买卖数量匹配（卖出数量不能超过持仓）
- 价格异常值（涨跌幅 >20% 标记警告）
- 费用合理性（佣金率 0.01%-0.03%）

**处理策略**：
- 发现问题 → 打印警告 → 询问用户是否继续
- 严重错误（如卖出超持仓）→ 终止分析

### 8.2 AKShare 数据异常

**场景**：
- 网络超时
- 股票代码不存在
- 停牌期间无行情数据

**处理策略**：
- 网络超时 → 重试 3 次，失败后使用缓存
- 代码不存在 → 跳过该股票，记录日志
- 停牌 → 使用停牌前最后价格

### 8.3 计算异常

**场景**：
- 交易日数量不足（< 30 天）
- 回归模型不收敛

**处理策略**：
- 交易日不足 → 提示用户，不生成报告
- 模型不收敛 → 使用简单平均法估算 Alpha/Beta

---

## 九、后续扩展预留

### 9.1 层次 2：Brinson 归因

**新增模块**：`analysis/brinson.py`

**功能**：
- 资产配置效应（行业轮动贡献）
- 个股选择效应（选股能力贡献）

### 9.2 层次 3：因子归因

**新增模块**：`analysis/factor.py`

**功能**：
- 多因子回归（市值、价值、动量、质量）
- 因子暴露度分析

### 9.3 定时任务

**新增模块**：`scheduler.py`

**功能**：
- 每周末自动运行分析
- 发送报告到邮箱/企业微信

---

## 十、验收标准

### 10.1 功能验收

- [ ] 能正确转换券商交割单为标准格式
- [ ] 能从 AKShare 获取中证全指历史数据
- [ ] 能正确重建每日持仓
- [ ] 能计算组合日收益率
- [ ] 能完成 Alpha/Beta 回归分析
- [ ] 能生成终端输出和 HTML 报告
- [ ] HTML 报告包含所有必需图表

### 10.2 数据验收

**使用用户 2025-2026 年数据验证**：
- [ ] 2025 年总收益率 ≈ 15%（误差 <1%）
- [ ] 2026 年回撤 ≈ 10%（误差 <1%）
- [ ] Alpha 符号和师傅讨论结果一致
- [ ] Beta 在合理范围（0.5-1.5）

### 10.3 性能验收

- [ ] 100 笔交易数据处理时间 < 30 秒
- [ ] 报告生成时间 < 10 秒
- [ ] 内存占用 < 500MB

---

## 十一、风险与限制

### 11.1 已知限制

1. **数据源依赖**：依赖 AKShare 免费接口，可能有频率限制
2. **模型假设**：CAPM 模型假设市场有效，实际可能不成立
3. **成本计算**：未考虑融资融券成本、分红税等
4. **停牌处理**：停牌期间估值可能不准确

### 11.2 风险缓解

- 数据源：实现缓存机制，减少 API 调用
- 模型：在报告中说明假设和局限性
- 成本：后续版本补充
- 停牌：在报告中标注停牌期间

---

## 十二、时间估算

| 模块 | 工作量 | 说明 |
|------|--------|------|
| 数据转换脚本 | 0.5 天 | 支持 2-3 家券商格式 |
| 持仓重建 | 1 天 | 核心逻辑 + 边界处理 |
| 收益率计算 | 0.5 天 | 相对简单 |
| Alpha/Beta 分析 | 0.5 天 | 使用 statsmodels |
| 报告生成 | 1 天 | pyecharts 图表 + HTML 模板 |
| 测试与调试 | 1 天 | 用真实数据验证 |
| **总计** | **4.5 天** | 约 1 周完成 |

---

## 十三、参考资料

- [AKShare 文档](https://akshare.akfamily.xyz/)
- [CAPM 模型](https://en.wikipedia.org/wiki/Capital_asset_pricing_model)
- [Brinson 归因模型](https://en.wikipedia.org/wiki/Performance_attribution)
- [pyecharts 文档](https://pyecharts.org/)

---

**文档状态**：待审核
**下一步**：用户审核 → 编写实现计划 → 开始开发
