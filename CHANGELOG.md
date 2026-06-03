# Changelog

## Unreleased

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
