# Claw 每日复盘接入建议

目标不是把交易逻辑写进 `claw`，而是让 `claw` 调用项目内已经稳定的能力。

当前约束：Python 脚本产出确定性事实和规则结论，`claw` 只做复盘编辑、一致性检查和推送。

## 推荐分层

1. 项目内负责:
   - 持仓同步
   - 风控信号计算
   - 观察列表买点检查
   - 生成结构化 JSON / prompt / 基础报告
2. `claw` 负责:
   - 定时触发
   - 读取 `output/daily_review_YYYYMMDD.json`
   - 基于 `output/daily_review_YYYYMMDD_prompt.md` 生成更自然的日报
   - 按 `ai_review_contract` 检查是否遗漏危险信号、数据降级、日期口径
   - 推送给你

## 稳定性边界

`claw` 不应该做这些事：

- 不重新抓行情、新闻、成交额、涨跌停。
- 不重新计算止损、止盈、观察名单买点。
- 不把观察名单信号升级为确定性买入建议。
- 不新增 `next_actions` 之外的明日交易动作。
- 不把缓存/降级数据描述成实时最新数据。

`claw` 应该做这些事：

- 使用 `daily_review_YYYYMMDD.json` 里的 `structured` 作为唯一事实源。
- 使用 `ai_review_contract.required_checks` 做一致性检查。
- 如果 `data_degradations` 非空，在“今日结论”里说明数据质量影响。
- 输出中所有“今日/当日”都按 `review_date` 解释，不按机器人运行时间解释。
- 持仓处理必须覆盖 `portfolio.danger_names` 和 `portfolio.warning_names`。

## 最小调用方式

```bash
./quickstart.sh sync-portfolio
./quickstart.sh daily-review
```

如果需要图表:

```bash
./quickstart.sh daily-pack
```

## 推荐机器人流程

1. 在项目根目录执行 `./quickstart.sh daily-review`。
2. 从脚本 stdout 读取本次生成的 `Prompt` 和 `JSON` 路径。
3. 优先读取 JSON，确认：
   - `review_date` 是目标复盘日期
   - `data_degradations` 是否为空
   - `ai_review_contract.required_checks` 是否存在
   - `structured.next_actions` 是否存在
4. 把 `daily_review_YYYYMMDD_prompt.md` 作为最终输入发给大模型。
5. 大模型输出后，机器人做一次轻量校验：
   - 是否包含 5 个固定章节
   - 是否提到所有 `ai_review_contract.evidence_targets`
   - 是否新增了 `ai_review_contract.forbidden` 禁止的动作

如果校验失败，机器人不要重新抓数据，应该用同一份 prompt 要求模型重写。

## 常见不稳定来源

- 行情源超时或降级：`baostock`、东方财富、FutuOpenD 都可能失败，脚本会尽量 fallback 并写入 `data_degradations`。
- 日期误解：机器人或模型把运行日期当成复盘日期；以 `review_date` 为准。
- 状态变化：观察名单信号会读写本地 state，连续运行可能改变“首次触发/持续触发”的表达。
- 环境不一致：必须在项目根目录运行，并使用 `quickstart.sh` 激活 `.venv`。
- AI 自由发挥：如果模型新增交易建议，按 `ai_review_contract.forbidden` 视为失败，需要重写。

## 为什么暂时不先做成独立 skill

- 买卖信号、止盈止损、观察名单规则会持续变化，放在项目代码里更容易测试和版本化。
- `claw` skill 更适合封装调用流程，不适合承载核心交易规则。
- 结构化 JSON 一旦稳定，以后不管换 `claw`、别的 agent，还是自己写 webhook，都能复用。

## 建议的后续增强

- 在 `[[watchlist]]` 上继续增加自定义规则字段，比如 `max_position`、`thesis`、`risk_point`
- 观察名单信号已经独立成 `watchlist_signals/`，后续新增买点规则时优先按插件扩展
- 增加消息推送层，只推送“危险持仓 + 触发买点”的摘要版
- 增加机器人侧输出校验脚本，自动检查 5 个章节、证据对象和禁止项
