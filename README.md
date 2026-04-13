# 量化投资工具集

AI 增强的个人量化投资系统，三大模块覆盖归因→风控→信息的完整闭环（环境分类模块搁置）。

> 核心原则：**归因 > 增强**（先搞清楚策略为什么有效）/ **生存 > 收益**（50%精力花在风控）/ **简单 > 复杂**（XGBoost > 深度学习）

## 快速开始

```bash
# 1. 环境准备（首次）
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 配置数据源
cp attribution_analysis/.env .env   # 编辑填入 TS_TOKEN / FUTU_HOST / FUTU_PORT

# 3. 一键全流程（PDF → 归因 → 风控）
./quickstart.sh all data/raw/对账单.pdf 2026-01-01 2026-03-31

# 或分阶段调用
./quickstart.sh parse data/raw/对账单.pdf          # 仅解析 PDF → CSV + 资产信息
./quickstart.sh attr 2026-01-01 2026-03-31          # 仅归因分析
./quickstart.sh risk                                 # 仅风控（总权益自动从 PDF 读取）
./quickstart.sh risk 500000                          # 手动指定总权益
```

## 系统架构

```
┌─────────────────────────────────────────────────────┐
│                  策略信号层（主驾驶）      │
│                       ↓ 买卖信号                     │
├─────────────────────────────────────────────────────┤
│              AI 增强层（本项目构建的核心）             │
│  ┌──────────┐ ┌──────────┐ ┌───────────────────┐   │
│  │ 模块1    │ │ 模块4    │ │ 模块2             │   │
│  │ 策略归因 │ │ (搁置)   │ │ 风控引擎          │   │
│  │·Alpha/β  │ │          │ │·仓位管理(事前)    │   │
│  │·Brinson  │ │          │ │·止损止盈(事中)    │   │
│  │·因子归因 │ │          │ │·异常检测(事后)    │   │
│  └──────────┘ └──────────┘ └───────────────────┘   │
│  ┌──────────────────────────────────────────────┐   │
│  │ 模块3: LLM 信息压缩                          │   │
│  │ 财报摘要 · 每日简报 · 交易复盘               │   │
│  └──────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────┤
│                  执行与复盘层                        │
│  交易执行(分批/TWAP) · 实时监控(P&L) · 归因分析     │
└─────────────────────────────────────────────────────┘
```

## 四大模块

### ✅ 模块1：策略归因分析 — 搞清楚钱从哪来

分析实盘交易的收益来源，区分 Alpha 和 Beta。

- PDF 交割单自动解析（东方证券）
- 持仓重建 & 每日市值计算
- Alpha/Beta 回归分析（CAPM）
- Brinson 行业归因（BHB 模型）
- 多数据源行情：A股 baostock / 港股 FutuOpenD → Yahoo → 东方财富

```bash
cd attribution_analysis
./quickstart.sh data/raw/对账单.pdf 2026-01-01 2026-03-31
```

详见 [attribution_analysis/README.md](attribution_analysis/README.md)

### ✅ 模块2：风控系统 — 三道防线，活下来最重要

收盘后运行日线级别风控检查，用于晚间复盘和制定第二天交易计划。设计上预留日内数据扩展接口。

```bash
cd risk_control
./quickstart.sh 500000
```

#### 第一道：仓位管理（事前）
- 单只股票 ≤ 15%，单一行业 ≤ 30%
- 总仓位根据沪深300实现波动率动态调整（<15%→80% / 15-25%→60% / 25-35%→40% / >35%→20%）

#### 第二道：止损止盈（事中）
- 个股止损 = 成本 - 2×ATR
- 分批止盈：涨15%减1/3，涨30%再减1/3，剩余移动止损
- 组合回撤熔断：日>3% 预警 / 周>5% 减仓50% / 月>8% 清仓

#### 第三道：异常检测（事后）

| 信号 | 触发条件 |
|------|---------|
| 波动率突变 | 短期波动率 / 长期波动率 > 2.5 |
| 流动性枯竭 | 量比 < 0.3 |
| 相关性过高 | 持仓间相关性 > 0.85 |
| 外部冲击 | Phase 2 接入新闻 API |

触发逻辑：1 个信号预警 / 2 个减仓 50% / 3 个清仓

详见 [risk_control/README.md](risk_control/README.md)

### 🔲 模块3：LLM 信息压缩 — 从刷1小时到读2分钟

不是用 LLM 预测市场，而是压缩处理信息的时间。

| 场景 | 触发 | 效果 |
|------|------|------|
| 财报摘要 | 持仓公司发布财报 | 每份读30分钟 → 扫30秒 |
| 每日简报 | 每天 8:30 开盘前 | 刷1小时新闻 → 读2分钟简报 |
| 交易复盘 | 每笔交易平仓后 | 凭记忆复盘 → 数据驱动结构化复盘 |

技术栈：Go 爬虫/RSS 聚合 + DeepSeek API + 企业微信/Telegram 推送

### ~~模块4：环境分类模型~~ — 暂不开发

> **搁置原因**：市场上已有成熟指标判断资金多空情况，宏观层面关注货币宽松/收紧即可，无需自建模型。若未来自由身开发系统时再考虑。
>
> 替代方案：[宏观指标观测指南](docs/macro-indicators-guide.md) — 用公开指标 + 投票制综合判断仓位

## 路线图

| 月份 | 模块 | 验收标准 | 状态 |
|------|------|---------|------|
| 1-2月 | 策略归因分析 | Alpha/Beta 分离结果与师傅验证一致 | ✅ 已完成 |
| 2-4月 | 风控系统 | 回测最大回撤减少 30%+ | ✅ Phase 1 完成 |
| 3-5月 | LLM 信息压缩 | 每天 8:30 收到简报 | 🔲 |
| 5-7月 | ~~环境分类模型~~ | ~~过去 5 年状态分类准确率 >70%~~ | 💤 搁置 |
| 7-9月 | 系统整合 | 信号→增强→风控→执行→复盘全流程 | 🔲 |
| 9-12月 | 实盘验证 | 夏普比率↑ 最大回撤↓ | 🔲 |

## 数据源

| 市场 | 数据源 | 备注 |
|------|--------|------|
| A股行情 | baostock | 不复权 |
| 港股行情 | FutuOpenD → Yahoo Finance → 东方财富 | 多源 fallback，不复权 |
| 指数/行业 | baostock + 东方财富 | 成分股 & 申万行业指数 |

## 项目结构

```
PythonProjects/
├── .env                            # 数据源配置（TS_TOKEN/FUTU_HOST/FUTU_PORT）
├── .venv/                          # 共享虚拟环境
├── requirements.txt                # 全局依赖
├── quickstart.sh                   # 一键运行：PDF → 归因 → 风控
├── shared/                         # 公共模块
│   ├── config.py                   # 公共配置（数据源/缓存/外部服务）
│   └── data_provider.py            # 多数据源行情（baostock/futu/yfinance/eastmoney）
├── data/
│   └── cache/                      # 行情数据缓存（两模块共用）
├── attribution_analysis/           # 模块1：策略归因分析 ✅
│   ├── scripts/
│   │   ├── attribution.py          # 核心归因分析（Alpha/Beta + 报告生成）
│   │   ├── brinson.py              # Brinson 行业归因（BHB 模型）
│   │   ├── convert_broker_data.py  # PDF 交割单 → 标准 CSV
│   │   └── pdf_portfolio.py        # PDF 持仓提取
│   ├── config.py                   # 归因专属配置（基准/报告）
│   ├── quickstart.sh
│   ├── data/
│   │   ├── raw/                    # 原始 PDF 对账单
│   │   ├── trades.csv              # 交割单
│   │   ├── holdings.csv            # 持仓快照
│   │   └── asset_summary.json      # 账户资产（总权益/市值/现金）
│   └── output/                     # 归因报告
├── risk_control/                   # 模块2：风控系统 ✅
│   ├── scripts/
│   │   ├── risk_report.py          # 主入口：风控检查报告
│   │   ├── risk_calc.py            # 底层计算（ATR/波动率/相关性/回撤）
│   │   ├── position_check.py       # 第一道防线：仓位管理
│   │   ├── stop_loss.py            # 第二道防线：止损止盈 + 熔断
│   │   └── anomaly_detect.py       # 第三道防线：异常检测
│   ├── config.py                   # 风控专属参数
│   ├── quickstart.sh
│   ├── data/
│   │   └── portfolio.csv           # 当前持仓
│   └── output/                     # 风控报告
└── docs/
    ├── quant-transformation-plan.md
    ├── macro-indicators-guide.md
    └── superpowers/
```

## 技术栈

- 策略/分析：Python 3.14 / pandas / statsmodels / XGBoost
- 数据管道/风控引擎：Go
- LLM：DeepSeek API
- 行情：baostock / futu-api / yfinance
- 可视化：pyecharts
- 数据库：ClickHouse(行情) + PostgreSQL(业务)

完整转型计划详见 [docs/quant-transformation-plan.md](docs/quant-transformation-plan.md)
