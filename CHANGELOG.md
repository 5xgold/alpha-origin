# Changelog

## Unreleased

### feat(dividend-grid): 股息网格计算器

- 新增 `app/dividend_grid/` 模块：给定每股股息、现价、无风险利率(十年期国债)，按固定股息率步进生成买入/减仓网格
  - `grid.py`：核心计算，买入价 = 每股股息 / 目标股息率（股息率越高→价越低→越跌越买）；`GridLevel` 记录目标股息率/买入价/较现价幅度/相对无风险利率性价比/动作标签；现价行自动插入到对应股息率位置
  - 数值计算全程使用 `decimal.Decimal`（不用 float）：`to_decimal()` 统一入口，从 float 转时经 `str()` 避免误差；股息率刻度用整数步 × Decimal 步长生成；CLI 参数以 str 接收后转 Decimal；无风险利率为 0 时性价比为 `None`
  - 动作分区：相对现价股息率 ≥+3.0pp 极端/深跌加仓、≥+1.5pp 重点加仓、>现价 加仓、低于现价 不加仓、低 ≥1.0pp 减仓/止盈区
  - `cli.py`：命令行入口，参数 `--name/--dividend/--price/--rf/--step/--low/--high`（利率类参数用百分数），用 `quantize` 控制输出精度，对齐表格
  - `tests/test_grid.py`：15 个用例覆盖输出均为 Decimal、价-息率精确相等、升降序、现价插入、网格高于无风险利率、性价比、档位计数、动作分区、极端低价、零利率、str/float/Decimal 入参一致、非法参数
- 线上工具（输入 A 股代码自动建网格）：
  - `datasource.py`：baostock 自动取数。`normalize_a_code` 支持 601318/sh.601318/601318.SH/000001 等格式；`fetch_ttm_dividends` 取过去 365 天已登记现金分红(按登记日+金额去重)；`fetch_annual_dividend` 取最近一个完整自然年到账分红(稳定口径，规避 TTM 在末期息登记日附近含两个年度末期息而偏高的问题)；`fetch_latest_price` 取最新不复权收盘；`fetch_stock_name`；`fetch_treasury_yield_pct` 国债收益率(自动源不稳定时回落默认 1.73%，可手动覆盖)
  - `streamlit_app.py`：Web 界面。输入代码 → 自动取数 → 双口径(自然年到账/TTM，默认年度)可选 → 0.5% 步进网格表(加仓绿/减仓红/现价黄高亮) + 分红明细 + CSV 下载；`@st.cache_data` 缓存 1h；`st.session_state` 保持口径切换状态
  - `dividend_grid_app.py`(仓库根)：本地/Streamlit Cloud 部署入口
  - `requirements.txt`(模块级)：最小部署依赖(streamlit/baostock/pandas/requests)，规避云端安装仓库全量重依赖
  - `README.md`：本地运行 + Streamlit Community Cloud 公网部署指南；TTM 与自然年口径说明
  - `tests/test_datasource.py`：代码规范化用例(合法/非法)
  - 根 `requirements.txt` 增加 `streamlit==1.58.0`
- fix：年度口径改为「按报告年度归集」，修正跨报告期混算 bug
  - 问题：baostock `query_dividend_data` 的 `year` 字段按**实施自然年**归类，非报告年度。原逻辑把不同报告年度的分红（如招行 2024 年度末期 2.0 + 2025 年度中期 1.013）混加成 3.013，语义错误
  - 改用**预案公告月份**判定报告年度：1-4 月公告→上一年度年报分红，5-12 月公告→当年中期分红（`_infer_report_year`）
  - 「完整年度」定义收紧：必须已含**年报末期分红**（`kind==年报`）且全部已登记才算完整；仅有中期（年报未公告）视为不完整并跳过，避免低估。归集逻辑抽为纯函数 `_select_complete_annual`
  - 修复后：招行(600036) 取 2024 年度 = 2.0 元(5.43%)；平安(601318) 取 2025 年度 = 2.70 元(5.50%)
  - `DividendComponent` 增加 `announce_date/report_year/kind` 字段；UI 口径标签「自然年到账」→「最近完整年报年度」
  - `tests/test_datasource.py` 增加报告年度判定 + 完整年度归集用例(招行/平安/未登记/无年报场景)，共 48 个测试
- 数据样例：中国平安(601318) 2025 年度全年股息 2.70 元(中期 0.95+末期 1.75)、当前股息率约 5.50%；招商银行(600036) 2024 年度 2.0 元、约 5.43%

- 新增 `research/joinquant/kdj_oversold_bounce.py`：在聚宽研究环境统计全 A 股 J<阈值买入后 3 日反弹达标概率
  - KDJ 计算与本地 `app/pattern_finder/core/feature_engine.py` 的 `calc_kdj` 一致（9 日，K/D 用 EMA alpha=1/3，J=3K-2D）
  - 信号去重：默认 `FIRST_CROSS_ONLY` 只取首次下穿阈值当日，避免连续 J<阈值多日重复计数
  - 达标口径可切换：`high_touch`（T+1~T+3 最高价触及）/ `close`（T+3 收盘价）
  - 大小盘按信号日总市值划分（默认 ≥500 亿为大盘 +2%，否则中小盘 +3.95%），市值取自 `valuation.market_cap`
  - 股票池过滤：剔除次新股（默认 120 天）/停牌/信号日涨停（买不进）；上市退市日期逐票校验
  - 参数集中在 `CONFIG`，支持 `SAMPLE_STOCKS` 抽样快速验证逻辑、`J_THRESHOLD` 敏感性扫描
  - 分阶段对比：`STAGES` 配置命名市场阶段（牛/熊/震荡等，区间可重叠），`ALSO_BY_YEAR` 额外按自然年汇总；一次性按最大区间采集信号后按信号日归组，避免重复拉数；`run_study()` 返回 `df / summary / stage_summary` 三件套，并打印各阶段【整体】达标概率横向对比表
  - 自动市场温度分段 `AUTO_REGIME`：用基准指数（默认沪深300）相对年线 MA250 的位置+斜率客观判定每个信号日的牛/熊/震荡，免手工填日期；与 `STAGES`/自然年并列汇总
  - "0 信号"可观测性修复：主循环异常不再静默吞掉，新增采集诊断计数器（异常数/无行情/行情过短/J<阈值交易日数/原始信号/过滤后保留）与异常样例打印；`get_price` 字段缺失（high_limit/paused）兜底；信号日索引统一 `to_datetime` 避免比较失效；新增 `debug_one(code)` 单票逐步调试函数（异常直抛不吞），定位卡点
  - 趋势过滤 `TREND_FILTER`：在 J<阈值 基础上叠加多头排列（MA20>MA60）且 MA20 未拐头向下，滤掉下跌趋势中的假超卖；"拐头向下"定义为 MA20 连续 `MA20_DOWN_STREAK` 天（默认 2）下行，单天微跌不算；可开关，均线周期与连跌天数可配；诊断新增"趋势过滤后"计数，`debug_one` 同步打印该步
  - MACD 过滤 `MACD_FILTER`：叠加要求 DIF 快线（EMA12-EMA26）> 0（零轴上方，中期偏多）；可开关，快慢线周期可配；诊断新增"MACD过滤后"计数，`debug_one` 同步打印
  - 对照开关 `COMPARE_MODE`（默认开）：基础信号取纯 J<阈值首次下穿，趋势/MACD 转为逐记录 `pass_trend/pass_macd/pass_all` 标记（同批候选子集，对比公平）；`_summarize` 额外打印「纯J<阈值 → +趋势 → +MACD → +全部过滤」并排达标率与较纯信号增益(pp)，量化过滤增益
  - J 阈值默认放宽至 13（配合趋势/MACD 过滤补回样本）
  - 修复 `datetime.now()` 报错：聚宽 `from jqdata import *` 会用模块覆盖 `datetime` 名字，改用 `pd.Timestamp.now()` 并移除 `from datetime import datetime`
- 选型：本地 baostock 单票串行+限速不适合全市场多年统计，改用聚宽研究环境（内置全市场行情与市值数据）

### refactor(project): 源码模块收敛到 app/ 目录

- 将 `attribution_analysis/`、`pattern_finder/`、`risk_control/`、`shared/`、`watchlist_signals/` 五个源码模块统一迁移到 `app/` 下（`git mv` 保留历史）
- 仓库根目录保留运行时资源：`data/`、`output/`、`portfolio.toml`、`.env`、`docs/`、`scripts/`、`quickstart.sh`、`.venv`
- 修正资源路径锚点（指向仓库根目录的 `output/` / `portfolio.toml` / `data/cache/` 各 +1 层 `.parent`）：
  - `shared/config.py`、`shared/store.py`：拆分 `_SRC_ROOT`（app/）与 `_REPO_ROOT`（仓库根）
  - `attribution_analysis/config.py`、`pattern_finder/config.py`：`OUTPUT_DIR`
  - `risk_control/{agent_price_cache,data_dependencies}.py`、`scripts/risk_report.py`、`backtest/report.py`、`signals/state.py`
  - `watchlist_signals/state.py`、`shared/portfolio_config.py`
  - `sys.path` 锚点指向新源码根 `app/`，无需修改（自动随模块层级修正）
- 新增仓库根 `conftest.py`：将 `app/` 注入 `sys.path`，支持在仓库根目录运行 `pytest`
- 更新 `quickstart.sh`（`AA_DIR`/`RC_DIR`/`shared` 路径、`cd app/pattern_finder`）；修正各模块 `quickstart.sh` 的 venv 与 `output/` 相对路径
- 更新 `scripts/init_project.sh` 数据目录路径与 `.gitignore`（`app/{attribution_analysis,risk_control}/data/`）
- 更新文档：`README.md`、`docs/data-directory-structure.md`、`docs/risk-data-dependency-graph.md`、`docs/signal-system-design.md`、`docs/configuration-guide.md`
- 验证：49 个测试用例全部通过，资源路径锚点全部解析到仓库根目录

### feat(risk-control): 风控回测框架

- 新增 `risk_control/backtest/` 模块：逐日模拟风控信号并执行交易
  - `engine.py`：核心模拟循环，复用现有 `calc_stop_take_levels` / `run_all_signals`
  - `executor.py`：信号→仓位变动执行逻辑（止损全卖/止盈1/3/熔断减仓）
  - `params.py`：参数覆盖上下文管理器 + 笛卡尔积扫描编排
  - `metrics.py`：回测指标计算（回撤减少/信号准确率/误杀率/收益影响）
  - `report.py`：JSON + Markdown 报告生成
  - `scenarios.py`：回测输入构造（从 portfolio.toml / 单股 what-if）
  - `run.py`：CLI 入口
- 新增 `./quickstart.sh backtest [start] [end] [--sweep]` 命令
- 新增 `risk_control/tests/test_backtest.py`：14 个测试用例，覆盖执行器/引擎/指标
- 支持参数扫描：默认扫描 ATR 止损倍数 [1.0, 1.5, 2.0, 2.5, 3.0] × 移动止损倍数 [1.0, 1.5, 2.0, 2.5]
- 输出到 `output/backtest_*.json` 和 `output/backtest_*.md`

### docs: 清理 README 与代码不一致

- 删除已移除的 llm_digest 模块相关内容（目录结构、命令示例、模块描述、技术栈）
- 删除重复的架构图，保留包含模块5的版本并更新执行层描述（claw 自动调度）
- 更新 Phase 2 路线图状态：P1 风控补数 ✅、P2 自动调度 ✅
- 项目结构图新增 signals/、data_dependencies.py、pattern_finder/、watchlist_signals/
- 修正模块数量描述（五大→三大活跃模块）

### refactor(risk-control): 风控运行前生成补数需求

- 新增 `risk_control/data_dependencies.py`，输出风控所需持仓 OHLCV、市场指数 close、入场保护 low 的补数清单
- 新增 `./quickstart.sh risk-data`，供 AI/agent 在跑风控前检查本地 cache 缺口
- 新增 `./quickstart.sh risk-merge`，将 agent incoming JSON 合并为按标的维护的长期增量 CSV cache，日期倒序保存
- `./quickstart.sh risk` 增加 strict 数据检查，关键行情缺失时先停止并输出 requirements JSON
- 清理项目内每日复盘生成器与图表入口，每日复盘正文改由外部 prompt 模板生成，项目内只保留风控信号

### fix(data-provider): baostock 超时保护，防止风控链路 SIGTERM

- `_ensure_bs_login()` 设置 socket 超时（默认 30s，可通过 `BS_TIMEOUT` 环境变量配置）
- 首次 login 失败后标记 `_bs_unavailable`，后续调用立即跳过，不再重复超时等待
- `_fetch_a_stock_prices` / `get_benchmark_prices` / `get_stock_sector` / `get_index_constituents` 均增加 `_run_with_timeout` 二级保护
- 新增 `_fetch_neodata_index_kline`：NeoData 指数历史 K 线，作为 baostock 之前的数据源
- 新增数据降级追踪：`get_data_degradations()` / `clear_data_degradations()`

## v0.8.0 - 2026-04-30

### refactor: 删除 llm_digest 模块，新增数据访问层

- **删除 `llm_digest/` 整个模块**：包括脚本（daily_review / trade_review / earnings_summary）、prompt 模板、配置、测试
- 移除 `openai` 依赖
- 新增 `shared/store.py` 数据访问层：统一内部数据读写接口，模块间不再直接读文件路径
  - `get_trades()` / `get_today_trades()` — 交易记录
  - `get_portfolio()` / `get_account()` — 持仓与账户
  - `get_attribution_report()` / `get_risk_signals_for()` — 报告读取
  - `save_output()` / `save_risk_snapshot()` — 统一输出

## v0.7.1 - 2026-04-30

### feat(llm-digest): 新增每日复盘流程

- 新增 `llm_digest/scripts/daily_review.py`：按”新闻→大盘→热点→持仓→次日计划”模板生成每日复盘（选股环节暂由手动执行，不纳入自动流程）
- LLM 调用改为可选：始终输出渲染好的 prompt 文件（`*_prompt.md`），LLM 不可用时自动跳过，支持喂给任意大模型 agent
- 修复 `risk_report.py` JSON 序列化不支持 `numpy.bool_` 的问题
- 新增 `llm_digest/prompts/daily_review.md`：输出结构贴合晚间复盘和第二天交易计划
- `risk_report.py` 抽出 `build_risk_snapshot()`，并导出 `risk_snapshot_*.json` 供复盘/图表复用
- 每日复盘接入：
  - 东方财富快讯（新闻/政策/风向）
  - 申万行业近 5/20 日强度（热点/持续性）
  - 今日成交汇总（净买入/净卖出/调仓）
  - 当前持仓与风控信号（持仓关键点/观察列表）
- `quickstart.sh` / `llm_digest/quickstart.sh` 新增 `daily-review` 命令
- 新增 `daily-pack` 命令：一键生成每日复盘 + 图表
- 新增 `llm_digest/tests/test_daily_review.py` 回归测试，覆盖今日成交汇总口径

## v0.7.0 - 2026-04-29

### feat(risk): 信号插件系统 + 分级预警 + 多空区间

- 新增信号插件框架（`risk_control/signals/`）：注册表模式，策略可插拔
- 6 个策略插件：3 个迁移（止损/止盈/移动止损）+ 3 个新增
  - `dynamic_stop_upgrade`: 动态止损升级（保本→成本+8%→紧移动止损）
  - `holding_period`: 持仓周期管理（资金停滞/长期亏损/趋势走弱）
  - `add_position`: 金字塔加仓策略（支撑位+仓位约束）
- 分级预警体系：👀关注 / ⚠️警告 / 🔴危险，持续触发自动升级
- 轻量状态追踪（`data/cache/risk_state.json`）：首次/持续触发标记
- 市场多空区间配置：多头手松、空头手紧，乘数调节止损/止盈/熔断参数
- 风控报告新增"信号系统"区块，按级别分组展示
- 新增设计文档 `docs/signal-system-design.md`
- `risk_calc.py` 新增 `calc_ma()` / `calc_support_levels()` 辅助函数

## v0.6.3 - 2026-04-29

### feat(risk): 个股仓位上限改为熟悉程度评估

- 替换二元 conviction 模型为四维度熟悉程度评估
- 四维度：商业模式 / 股东态度 / 估值位置 / 技术趋势
- 分级映射：0-1项→12%, 2项→15%, 3项→18%, 4项→22%
- 风控报告新增熟悉程度概览表
- 向后兼容 conviction = true（等同极高熟悉）
- 个股行情缓存迁移到 data/cache/stocks/ 子目录
- 修复 quickstart.sh 总权益读取（从 portfolio.toml 自动读取）

## v0.6.2 - 2026-04-28

### docs(structure): 规范数据目录结构

- 创建 docs/data-directory-structure.md：数据目录规范文档
- 明确数据分类规则：
  - 共享数据（/data/）：行情缓存、基准数据，通过 shared.data_provider 访问
  - 模块专属（{module}/data/）：模块特定输入/输出，模块内部访问
- 删除空的缓存目录：attribution_analysis/data/cache, pattern_finder/data/cache
- 更新 README.md：添加数据目录结构说明

## v0.6.1 - 2026-04-27

### feat(config): 统一配置文件管理

- 创建 portfolio.toml：持仓配置文件（TOML 格式）
- 创建 shared/portfolio_config.py：TOML → CSV 转换工具
- 更新 .gitignore：忽略 portfolio.toml（私密数据）
- 创建 portfolio.toml.example：持仓配置示例
- 更新 quickstart.sh：添加 sync-portfolio 命令
- 更新 requirements.txt：添加 tomli（Python <3.11）
- 创建 docs/configuration-guide.md：配置文件使用指南
- 更新 README.md：添加配置文件说明

## v0.6.0 - 2026-04-27

### feat(pattern-finder): 集成形态相似检索模块（模块5）

- 重构为标准模块结构：pattern-finder → pattern_finder（Python 命名规范）
- 合并 features/similarity/backtest → core/（统一核心逻辑）
- 创建 pattern_finder/config.py：导入 shared.config，定义模块参数
- 重构 data/loader.py：使用 shared.data_provider 替代 akshare/tushare 直接调用
- 创建 pattern_finder/quickstart.sh：支持 build/query/scan/demo 命令
- 使用共享缓存目录：/data/cache/pattern_finder/
- 输出报告到统一目录：/output/
- 更新 requirements.txt：添加 scikit-learn>=1.3.0，注释可选依赖（akshare/faiss-cpu/dtaidistance）
- 创建 pattern_finder/README.md：完整模块文档（工作流程/配置/算法/输出解读/FAQ）
- 更新主 README.md：添加模块5章节，更新系统架构图，调整快速开始命令

### feat(data-provider): 支持复权参数，默认前复权

- shared.data_provider.get_stock_prices() 新增 adjust 参数（qfq/hfq/""）
- 默认使用前复权（qfq），适用于量化回测、技术分析、收益计算
- baostock: adjustflag="1"（前复权）/ "2"（后复权）/ "3"（不复权）
- FutuOpenD: AuType.QFQ（前复权）/ HFQ（后复权）/ NONE（不复权）
- 缓存文件名包含复权方式，避免混淆
- pattern_finder/data/loader.py: 所有数据加载函数支持 adjust 参数
- 保留 akshare/tushare 备用数据源，支持复权参数

## 2026-04-17

### feat(roadmap): 新增 Phase 2 路线图

- 新增 Phase 2 路线图文档（docs/roadmap-phase2.md）：自动化 + 实时化规划，涵盖券商 API、自动调度、信息日报、事件提醒四大方向
- 更新 README.md：路线图章节增加 Phase 2 规划摘要及链接，项目结构补充 roadmap-phase2.md

## 2026-04-15

### feat(llm-digest): 模块3 — LLM 信息压缩

- 新增 `llm_digest/` 模块，覆盖两个场景：交易复盘、财报摘要
- `llm_client.py`: OpenAI 兼容 API 封装，支持 DeepSeek/Qwen/本地模型通过 `base_url` 切换
- `trade_review.py`: 从 trades.csv 提取买卖记录，结合行情/归因/风控数据生成结构化复盘
- `earnings_summary.py`: pdfplumber 提取财报文本，长文本分块摘要，生成投资建议
- Jinja2 prompt 模板（`prompts/`），数据与提示词分离
- `shared/data_provider.py` 新增 `get_eastmoney_news()` 东方财富快讯接口
- `quickstart.sh` 新增 `review` / `earnings` 命令
- `.env` 新增 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 配置
- `requirements.txt` 追加 `openai>=1.30.0`

### fix(llm-digest): 修复 LLM 工作流回归问题

- `shared/convert_broker_data.py`: 生成 `trades.csv` 时按 `direction` 统一 `quantity` 符号，修复卖出数量正负不一致导致的历史数据污染
- `trade_review.py`: 平仓判断改为基于方向归一后的成交数量，补上 `301073`、`300690`、`600390` 等被漏掉的已平仓标的
- `trade_review.py`: 代码过滤改为保留港股 5 位代码，修复 `00700`、`01810` 等港股无法手动复盘的问题
- `trade_review.py`: 对旧版 `trades.csv` 增加兼容归一逻辑，即使不重新解析 PDF 也能正确识别平仓记录
- `quickstart.sh` / `attribution_analysis/quickstart.sh`: 依赖安装标记改为基于 `requirements.txt` 哈希，新增依赖后会自动重新安装

### refactor(project)

- move `convert_broker_data.py` and `pdf_portfolio.py` from `attribution_analysis/scripts/` to `shared/` for cross-module reuse
- unify report output under root `output/` and update `.gitignore` to ignore the shared output directory
- update attribution and risk-control quickstart scripts to call shared PDF parsers and write reports to the unified output path
- adjust attribution config and risk report persistence paths to use the repository-level output directory
- refresh top-level and module READMEs to reflect the new shared script layout and output locations

## 2026-04-14

### fix(risk-control)

- prevent positions with missing quotes and zero cost from being silently valued at zero
- validate portfolio pricing inputs before generating the risk report
- compute circuit breaker triggers from window drawdown instead of raw period return
- derive anomaly actions from unique signal types to avoid pair-count amplification
- raise runtime errors for benchmark fetch failures so callers can degrade gracefully
- add regression tests for valuation fallback, circuit breaker logic, anomaly escalation, and benchmark failures
