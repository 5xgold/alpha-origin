# Claw 每日复盘接入建议

目标不是把交易逻辑写进 `claw`，而是让 `claw` 调用项目内已经稳定的能力。

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
   - 推送给你

## 最小调用方式

```bash
./quickstart.sh sync-portfolio
./quickstart.sh daily-review
```

如果需要图表:

```bash
./quickstart.sh daily-pack
```

## 为什么暂时不先做成独立 skill

- 买卖信号、止盈止损、观察名单规则会持续变化，放在项目代码里更容易测试和版本化。
- `claw` skill 更适合封装调用流程，不适合承载核心交易规则。
- 结构化 JSON 一旦稳定，以后不管换 `claw`、别的 agent，还是自己写 webhook，都能复用。

## 建议的后续增强

- 在 `[[watchlist]]` 上继续增加自定义规则字段，比如 `max_position`、`thesis`、`risk_point`
- 观察名单信号已经独立成 `watchlist_signals/`，后续新增买点规则时优先按插件扩展
- 增加消息推送层，只推送“危险持仓 + 触发买点”的摘要版
