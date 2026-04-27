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

# LLM 服务（用于信息压缩模块）
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

[[holdings]]
code = "000001"
name = "平安银行"
market = "深圳"
quantity = 1000
cost_price = 12.5
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
