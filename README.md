# 量化组合研究与风险管理平台

从真实券商对账单出发，完成**交易与资金流重建、策略收益归因、组合风控、历史回放和研究报告生成**。项目面向个人实盘复盘与量化研究，重点关注收益口径、时序边界和结果可解释性，而不只是生成买卖信号。

```text
券商 PDF / 持仓配置 / 行情数据
              │
              ▼
     交易、持仓与资金流重建
              │
        ┌─────┴─────┐
        ▼           ▼
  TWR 与收益归因   三层组合风控
 Alpha/Beta       仓位/止损/异常
 Brinson 行业归因       │
        │           ▼
        └─────► 历史回放与参数分析
                    │
                    ▼
             Markdown / JSON 报告
```

## 核心亮点

| 能力 | 实现 |
|---|---|
| 真实账户复盘 | 解析券商 PDF 中的成交、费用、持仓和外部资金流，重建账户价值与策略净值 |
| 可解释收益归因 | 使用 TWR 剔除出入金影响，计算市场模型 Alpha/Beta、Sharpe、最大回撤和 Brinson 行业归因 |
| 时序一致的风控回放 | 每个交易日只读取当日及以前的数据，收盘后产生信号，下一交易日开盘执行 |
| 三层风险控制 | 覆盖仓位与行业集中度、ATR/权益风险预算止损、分批止盈、移动止损、组合熔断和异常检测 |
| 工程化数据访问 | 统一 A 股/港股行情接口、复权口径、缓存、多数据源回退和运行前数据缺口检查 |
| 可扩展信号系统 | 通过 registry/state 机制接入持仓与观察列表策略，隔离信号计算、状态和报告输出 |
| 自动化验证 | 97 项测试覆盖归因、风控回放、数据依赖和股息网格等核心逻辑 |

## 功能模块

| 模块 | 用途 | 入口 |
|---|---|---|
| 策略归因分析 | 重建账户与策略净值，输出 Alpha/Beta、Brinson 行业归因和收益拆解 | `./quickstart.sh all` / `./quickstart.sh attr` |
| 风控系统 | 检查仓位、止损止盈、组合回撤和异常信号，支持历史回放与参数扫描 | `./quickstart.sh risk` / `./quickstart.sh backtest` |
| 形态相似检索 | 使用余弦相似度和 DTW 检索历史技术形态，统计相似案例的后验表现 | `./quickstart.sh pattern ...` |
| 股息网格计算器 | 根据股息、现价和无风险利率生成价格网格，提供 CLI 与 Streamlit 页面 | `streamlit run dividend_grid_app.py` |

## 研究口径

- 策略收益使用时间加权收益率（TWR），将外部资金流与投资收益分开。
- 行情研究默认使用前复权序列；股息网格获取最新真实价格时使用不复权行情。
- 风控回放使用 T 日收盘信息生成信号，并在 T+1 开盘执行，避免以已知收盘信号回填当日成交。
- 当前风控回放属于**现有组合的历史情景回放**，不等同于 point-in-time 股票池上的完整策略回测；交易成本、涨跌停、停牌订单延续等成交约束仍在完善。

> 本项目用于研究、复盘和风险辅助，不构成投资建议。历史结果不代表未来表现。

## 快速开始

### 1. 初始化环境

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 准备配置

```bash
cp portfolio.toml.example portfolio.toml
vim portfolio.toml
```

`portfolio.toml` 是风控和持仓相关功能的主要配置文件：

| 配置段 | 说明 |
|---|---|
| `[account]` | 账户总权益等账户级配置 |
| `[[holdings]]` | 当前持仓，供风控、持仓复盘和信号检查使用 |
| `[[watchlist]]` | 观察列表，供买入信号或外部 agent 使用 |

如需使用额外数据源，可按需配置 `.env`。配置详情见 [docs/configuration-guide.md](docs/configuration-guide.md)。

### 3. 运行常用任务

```bash
# 解析 PDF 对账单并生成归因报告
./quickstart.sh all data/raw/对账单.pdf 2026-01-01 2026-03-31

# 仅解析 PDF
./quickstart.sh parse data/raw/对账单.pdf

# 仅运行归因分析
./quickstart.sh attr 2026-01-01 2026-03-31

# 检查风控所需行情数据是否齐备
./quickstart.sh risk-data

# 运行风控检查，总权益默认读取 portfolio.toml
./quickstart.sh risk

# 手动指定总权益运行风控
./quickstart.sh risk 500000

# 运行风控回测
./quickstart.sh backtest 2025-01-01 2025-12-31

# 构建形态样本库并查询股票
./quickstart.sh pattern build 600519,000001
./quickstart.sh pattern query 600519
```

## 模块使用

### 策略归因分析

归因模块读取券商 PDF 解析后的交易、持仓和资金流水数据，输出账户净值、策略收益、Alpha/Beta 回归和行业归因结果。

```bash
./quickstart.sh parse data/raw/对账单.pdf
./quickstart.sh attr 2026-01-01 2026-03-31
```

常见输入输出：

| 路径 | 说明 |
|---|---|
| `app/attribution_analysis/data/trades.csv` | PDF 解析后的交易记录 |
| `app/attribution_analysis/data/holdings.csv` | PDF 解析后的持仓快照 |
| `app/attribution_analysis/data/cash_flows.csv` | 外部资金流水 |
| `output/report.md` | 归因报告 |

详细说明见 [app/attribution_analysis/README.md](app/attribution_analysis/README.md)。

### 风控系统

风控模块只读取 `portfolio.toml` 和本地行情缓存，不依赖归因模块的中间结果。运行前会检查持仓、指数、入场保护等行情数据是否齐备；缺数时会尝试按缺口补齐。

```bash
./quickstart.sh risk-data
./quickstart.sh risk
```

支持的主要检查：

| 类别 | 内容 |
|---|---|
| 仓位管理 | 单票仓位、行业集中度、总仓位建议 |
| 止损止盈 | ATR 止损、权益风险预算止损、分批止盈、移动止损 |
| 组合风控 | 日/周/月回撤熔断 |
| 异常检测 | 波动率突变、流动性枯竭、相关性过高 |
| 信号插件 | 持仓周期、动态止损升级、加仓策略等可插拔信号 |

输出文件默认写入 `output/`，数据需求和行情缓存写入 `data/cache/`。详细说明见 [app/risk_control/README.md](app/risk_control/README.md)。

### 形态相似检索

形态检索模块基于技术指标和时间序列相似度，从历史样本中查找与当前股票形态接近的案例，并统计样本表现。

```bash
./quickstart.sh pattern build 600519,000001,000858
./quickstart.sh pattern query 600519
./quickstart.sh pattern scan
```

模块特性：

| 能力 | 说明 |
|---|---|
| 特征窗口 | 默认 60 日观察窗口 |
| 验证窗口 | 默认 20 日持有期表现 |
| 技术指标 | MA、MACD、RSI、KDJ、布林带、ATR、OBV |
| 相似度 | 余弦相似度 + DTW 时间序列匹配 |
| 报告 | 胜率、盈亏比、收益分布、分年度表现 |

详细说明见 [app/pattern_finder/README.md](app/pattern_finder/README.md)。

### 股息网格计算器

股息网格模块根据每股股息、当前价格和十年期国债收益率，按固定股息率步进生成买入/减仓价格网格。

Web 页面：

```bash
streamlit run dividend_grid_app.py
```

命令行：

```bash
python app/dividend_grid/cli.py --name 中国平安 --dividend 2.70 --price 53.48 --rf 1.73
```

详细说明见 [app/dividend_grid/README.md](app/dividend_grid/README.md)。

## 数据和目录

```text
PythonProjects/
├── quickstart.sh                  # 统一命令入口
├── portfolio.toml.example         # 持仓配置示例
├── portfolio.toml                 # 本地持仓配置，不提交
├── dividend_grid_app.py           # 股息网格 Streamlit 入口
├── data/
│   ├── raw/                       # 原始输入文件
│   └── cache/                     # 行情和运行缓存
├── output/                        # 统一报告输出目录
├── docs/                          # 项目文档
└── app/
    ├── attribution_analysis/      # 策略归因分析
    ├── risk_control/              # 风控系统
    ├── pattern_finder/            # 形态相似检索
    ├── dividend_grid/             # 股息网格计算器
    ├── shared/                    # 公共配置、行情、数据访问层
    └── watchlist_signals/         # 观察列表信号策略
```

数据目录规范见 [docs/data-directory-structure.md](docs/data-directory-structure.md)。

## 输出文件

| 路径 | 说明 |
|---|---|
| `output/report.md` | 策略归因报告 |
| `output/risk_*` | 风控检查结果和结构化快照 |
| `output/backtest_*` | 风控回测报告 |
| `output/risk_data_requirements_*.json` | 风控运行前的数据需求和缺口状态 |
| `data/cache/agent_prices/` | 风控长期行情缓存 |

## 测试

```bash
pytest
```

也可以只运行单个模块测试：

```bash
pytest app/risk_control/tests
pytest app/pattern_finder/tests
pytest app/dividend_grid/tests
```

## 相关文档

| 文档 | 内容 |
|---|---|
| [docs/configuration-guide.md](docs/configuration-guide.md) | `portfolio.toml` 和环境配置 |
| [docs/data-directory-structure.md](docs/data-directory-structure.md) | 数据目录规范 |
| [docs/risk-data-dependency-graph.md](docs/risk-data-dependency-graph.md) | 风控数据依赖 |
| [docs/signal-system-design.md](docs/signal-system-design.md) | 风控信号插件设计 |
| [docs/macro-indicators-guide.md](docs/macro-indicators-guide.md) | 宏观指标观察口径 |

## 技术栈

| 类别 | 工具 |
|---|---|
| 语言 | Python |
| 数据处理 | pandas、numpy |
| 统计分析 | statsmodels |
| 行情数据 | baostock、futu-api、东方财富等 |
| PDF 解析 | pdfplumber |
| 可视化和页面 | pyecharts、Streamlit |
