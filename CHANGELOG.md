# Changelog

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
