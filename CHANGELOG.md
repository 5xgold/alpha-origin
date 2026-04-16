# Changelog

## 2026-04-15

### feat(llm-digest): 模块3 — LLM 信息压缩

- 新增 `llm_digest/` 模块，覆盖三个场景：交易复盘、每日简报、财报摘要
- `llm_client.py`: OpenAI 兼容 API 封装，支持 DeepSeek/Qwen/本地模型通过 `base_url` 切换
- `trade_review.py`: 从 trades.csv 提取买卖记录，结合行情/归因/风控数据生成结构化复盘
- `daily_brief.py`: 汇总大盘/持仓/行业/风控/新闻数据，生成每日简报
- `earnings_summary.py`: pdfplumber 提取财报文本，长文本分块摘要，生成投资建议
- Jinja2 prompt 模板（`prompts/`），数据与提示词分离
- `shared/data_provider.py` 新增 `get_eastmoney_news()` 东方财富快讯接口
- `quickstart.sh` 新增 `brief` / `review` / `earnings` 命令
- `.env` 新增 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 配置
- `requirements.txt` 追加 `openai>=1.30.0`

### fix(llm-digest): 修复 LLM 工作流回归问题

- `shared/convert_broker_data.py`: 生成 `trades.csv` 时按 `direction` 统一 `quantity` 符号，修复卖出数量正负不一致导致的历史数据污染
- `trade_review.py`: 平仓判断改为基于方向归一后的成交数量，补上 `301073`、`300690`、`600390` 等被漏掉的已平仓标的
- `trade_review.py`: 代码过滤改为保留港股 5 位代码，修复 `00700`、`01810` 等港股无法手动复盘的问题
- `trade_review.py`: 对旧版 `trades.csv` 增加兼容归一逻辑，即使不重新解析 PDF 也能正确识别平仓记录
- `daily_brief.py`: `brief --date` 按目标日期选择对应的 `risk_report_*.md`，避免历史简报误读最新风控报告
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
