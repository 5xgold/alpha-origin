# 配置文件说明

项目使用两个配置文件统一管理所有手动配置：

## 1. .env - API 密钥和服务配置

存放敏感信息，不提交到 Git。

```env
# Tushare API（可选，用于数据源）
TS_TOKEN=your_tushare_token_here

# FutuOpenD（可选，用于港股数据）
FUTU_HOST=127.0.0.1
FUTU_PORT=11111

# 可选：如果你会把 prompt 喂给外部 agent / 大模型，再配置对应密钥
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=your_api_key_here
LLM_MODEL=deepseek-chat
```

## 2. portfolio.toml - 持仓配置

存放当前持仓数据，不提交到 Git。

```toml
[[holdings]]
code = "600519"
name = "贵州茅台"
market = "上海"
quantity = 100
cost_price = 1800.0
# 必填：trade_plan.stop_loss_strategy = "entry_day_low_guard" 依赖 buy_date
buy_date = "20240102"
trade_plan = {status = "active", stop_loss_strategy = "entry_day_low_guard", plan_note = "跌破买入日低点下5tick执行"}
risk_rules = {stop_loss_params = {buffer_ticks = 5}, trailing_stop_atr_multiplier = 2.5}

[[holdings]]
code = "000001"
name = "平安银行"
market = "深圳"
quantity = 1000
cost_price = 12.5

[[watchlist]]
code = "300750"
name = "宁德时代"
market = "深圳"
target_buy_price = 180.0
breakout_price = 205.0
signal_rules = {preferred_strategy = "target_buy"}
notes = "回调或突破后再看"
```

### 字段说明

- `code`: 股票代码
  - A股：6位数字（如 600519）
  - 港股：5位数字（如 00700）
- `name`: 股票名称（用于报告展示）
- `market`: 交易市场
  - `上海` - 上交所
  - `深圳` - 深交所
  - `沪港通` - 港股通（沪）
  - `深港通` - 港股通（深）
- `quantity`: 持仓数量（股）
- `cost_price`: 成本价（元）
- `buy_date`: 买入日期，格式 `YYYYMMDD`
  - 当 `trade_plan.stop_loss_strategy = "entry_day_low_guard"` 时必填
  - 如果没写 `trade_plan`，而是直接在 `risk_rules.stop_loss_strategy` 指定该策略，也同样必填
  - 建议 agent 生成 `[[holdings]]` 时显式输出，避免策略校验失败
- `trade_plan`: 可选，已确认的交易计划
  - `status`: 计划状态，默认 `active`
  - `stop_loss_strategy`: 当前确认采用的止损策略，风控模块优先读取这一层
    - 支持 `atr` / `entry_day_low_guard` / `equity_risk_budget`
  - `plan_note`: 人类可读备注，方便每日复盘展示
- `risk_rules`: 可选，自定义风控覆盖参数，推荐使用内联 table，例如 `risk_rules = {...}`
  - `stop_loss_strategy = "atr" | "entry_day_low_guard" | "equity_risk_budget"`：兼容旧写法；如果 `trade_plan.stop_loss_strategy` 存在，则以后者为准
  - `stop_loss_atr_multiplier`
  - `stop_loss_params = {buffer_ticks = 5}`：推荐新写法，策略私有参数放这里
  - `entry_day_low_buffer_ticks = 5`：兼容旧写法；仅 `entry_day_low_guard` 使用
  - `price_tick = 0.01` 或 `stop_loss_params.price_tick = 0.01`（可选，默认 0.01）
  - `trailing_stop_atr_multiplier`
  - `take_profit_tiers = [{trigger_pct = 0.12, sell_ratio = 0.3}, ...]`

### `[[watchlist]]` 字段说明

- `code` / `name` / `market`: 与持仓一致
- `target_buy_price`: 回调到该价格及以下时提示关注买点
- `breakout_price`: 向上突破该价格时提示关注买点
- `signal_rules`: 预留给观察列表插件的自定义参数
- `notes`: 观察逻辑备注，供每日复盘和 agent 使用

## 初始化配置

### 首次使用

```bash
# 1. 复制示例文件
cp portfolio.toml.example portfolio.toml

# 2. 编辑 portfolio.toml，填入你的实际持仓
vim portfolio.toml

# 3. 同步到 CSV（供各模块使用）
./quickstart.sh sync-portfolio
```

### 更新持仓

每次修改 `portfolio.toml` 后，运行同步命令：

```bash
./quickstart.sh sync-portfolio
```

这会将 TOML 配置转换为 CSV 格式，供风控模块和形态检索模块使用。

## 配置文件位置

```
PythonProjects/
├── .env                      # API 密钥（不提交）
├── portfolio.toml            # 持仓配置（不提交）
├── portfolio.toml.example    # 持仓示例（提交到 Git）
├── risk_control/data/
│   └── portfolio.csv         # 自动生成，不需要手动编辑
```

## 为什么分两个文件？

1. **.env** - 敏感信息
   - API 密钥、服务地址
   - 通用的环境变量格式
   - 多个项目可能共享

2. **portfolio.toml** - 业务数据
   - 持仓信息（非敏感但私密）
   - 结构化数据，易读易写
   - 支持注释和扩展

## 注意事项

⚠️ **不要提交到 Git**

`.gitignore` 已配置忽略这两个文件：
```
.env
portfolio.toml
```

⚠️ **备份建议**

这两个文件包含重要配置，建议：
- 定期备份到安全位置
- 或使用私有 Git 仓库单独管理

⚠️ **同步提醒**

修改 `portfolio.toml` 后记得运行：
```bash
./quickstart.sh sync-portfolio
```

否则风控模块会使用旧的持仓数据。

## 自动同步（可选）

如果希望每次运行风控前自动同步，可以在 `risk_control/quickstart.sh` 开头添加：

```bash
# 自动同步持仓配置
if [ -f "../portfolio.toml" ]; then
    python3 ../shared/portfolio_config.py
fi
```
