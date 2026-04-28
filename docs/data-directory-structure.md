# 数据目录规范

## 目录结构

```
PythonProjects/
├── data/                           # 共享数据（所有模块可访问）
│   ├── cache/                      # 行情数据缓存（baostock/FutuOpenD）
│   │   ├── 600519_20200101_20241231_qfq.csv
│   │   ├── pattern_finder/         # 形态检索样本库
│   │   └── ...
│   └── raw/                        # 原始输入文件（PDF对账单等）
│
├── attribution_analysis/data/      # 归因分析专属数据
│   ├── trades.csv                  # 交易记录（从PDF解析）
│   ├── holdings.csv                # 持仓快照（从PDF解析）
│   ├── cash_flows.csv              # 资金流水（从PDF解析）
│   ├── asset_summary.json          # 账户资产摘要
│   └── cache/                      # 模块专属缓存（如有）
│
├── risk_control/data/              # 风控专属数据
│   └── portfolio.csv               # 当前持仓（从portfolio.toml同步）
│
├── llm_digest/data/                # LLM专属数据
│   └── earnings/                   # 财报PDF存放目录
│
└── pattern_finder/data/            # 形态检索专属数据
    ├── cache/                      # 样本库缓存（实际存在 /data/cache/pattern_finder/）
    └── raw/                        # 自定义CSV数据（可选）
```

## 数据分类规则

### 1. 共享数据（/data/）

**存放内容：**
- 行情数据缓存（K线、成交量）
- 基准指数数据
- 行业分类数据
- 原始输入文件（PDF对账单）

**访问方式：**
- 通过 `shared.data_provider` 统一访问
- 通过 `shared.config.CACHE_DIR` 获取路径

**特点：**
- 多个模块共享，避免重复下载
- 统一缓存策略（过期时间、清理规则）
- 不包含模块特定的业务数据

### 2. 模块专属数据（{module}/data/）

**存放内容：**
- 模块特定的输入/输出文件
- 模块内部状态数据
- 模块专属缓存（如果不适合共享）

**访问方式：**
- 模块内部直接访问
- 其他模块不应直接读取

**特点：**
- 模块独立，互不干扰
- 可以有不同的数据格式和结构
- 删除模块时可以一并删除

## 具体模块说明

### attribution_analysis/data/

**用途：** 归因分析的输入数据（从PDF解析）

**文件：**
- `trades.csv` - 交易记录（买入/卖出）
- `holdings.csv` - 持仓快照
- `cash_flows.csv` - 资金流水（转入/转出）
- `asset_summary.json` - 账户资产摘要（总权益/市值/现金）

**生成方式：**
```bash
python shared/convert_broker_data.py --input data/raw/对账单.pdf --output-dir attribution_analysis/data
```

**依赖：** 仅归因分析模块使用

### risk_control/data/

**用途：** 风控模块的输入数据

**文件：**
- `portfolio.csv` - 当前持仓（从 portfolio.toml 同步）

**生成方式：**
```bash
python shared/portfolio_config.py
# 或
./quickstart.sh sync-portfolio
```

**依赖：** 仅风控模块使用

### llm_digest/data/

**用途：** LLM 模块的输入数据

**文件：**
- `earnings/` - 财报 PDF 存放目录

**依赖：** 仅 LLM 模块使用

### pattern_finder/data/

**用途：** 形态检索的自定义数据（可选）

**文件：**
- `raw/` - 自定义 CSV 数据（如果不使用 shared.data_provider）
- `cache/` - 实际存储在 `/data/cache/pattern_finder/`（符号链接或配置指向）

**依赖：** 仅形态检索模块使用

## 数据流转

```
1. 原始数据输入
   data/raw/对账单.pdf
   ↓
2. PDF 解析
   shared/convert_broker_data.py
   ↓
3. 模块专属数据
   attribution_analysis/data/trades.csv
   attribution_analysis/data/holdings.csv
   ↓
4. 持仓同步
   portfolio.toml → risk_control/data/portfolio.csv
   ↓
5. 行情数据获取
   shared.data_provider → data/cache/*.csv
   ↓
6. 各模块独立运行
   - attribution_analysis: 读取 trades.csv + 行情数据
   - risk_control: 读取 portfolio.csv + 行情数据
   - pattern_finder: 读取行情数据 + 样本库
```

## .gitignore 规则

```gitignore
# 共享数据目录（不提交）
data/

# 模块专属数据目录（不提交）
attribution_analysis/data/
risk_control/data/
llm_digest/data/
pattern_finder/data/

# 输出目录（不提交）
output/
```

## 最佳实践

### ✅ 应该做的

1. **共享行情数据** - 使用 `shared.data_provider`，自动缓存到 `/data/cache/`
2. **模块隔离** - 模块专属数据放在模块内部 `data/` 目录
3. **配置分离** - 配置文件（.env, portfolio.toml）放在项目根目录
4. **输出统一** - 所有报告输出到 `/output/` 目录

### ❌ 不应该做的

1. **跨模块直接访问** - 不要在 risk_control 中直接读取 `attribution_analysis/data/trades.csv`
2. **重复缓存** - 不要在模块内部重复缓存行情数据
3. **混淆数据类型** - 不要把模块专属数据放到共享 `/data/` 目录
4. **硬编码路径** - 不要硬编码绝对路径，使用 `shared.config` 获取

## 数据清理

### 清理共享缓存
```bash
# 清理所有行情缓存
rm -rf data/cache/*.csv

# 清理特定模块缓存
rm -rf data/cache/pattern_finder/
```

### 清理模块数据
```bash
# 清理归因分析数据（需要重新解析PDF）
rm -rf attribution_analysis/data/*

# 清理风控数据（需要重新同步持仓）
rm -rf risk_control/data/portfolio.csv
```

### 清理输出报告
```bash
# 清理所有报告
rm -rf output/*
```

## 迁移指南

如果需要调整数据目录结构：

1. **备份数据**
   ```bash
   tar -czf backup_$(date +%Y%m%d).tar.gz data/ */data/
   ```

2. **更新代码中的路径引用**
   - 检查所有 `pd.read_csv()` 调用
   - 检查所有 `Path()` 构造
   - 更新 `shared.config` 中的路径常量

3. **更新 .gitignore**
   - 确保新的数据目录被忽略

4. **更新文档**
   - README.md
   - 各模块的 README.md
   - 本文档

## 常见问题

### Q: 为什么不把所有数据都放在根目录 /data/ 下？

A: 因为模块专属数据（如 trades.csv）只有特定模块使用，放在模块内部更清晰，删除模块时也更方便。

### Q: 如果多个模块都需要某个数据怎么办？

A: 如果真的需要共享，应该：
1. 将数据提升到 `/data/` 目录
2. 创建 `shared/` 下的工具函数统一访问
3. 更新所有模块使用新的访问方式

### Q: pattern_finder 的样本库应该放哪里？

A: 样本库是缓存性质的数据，应该放在 `/data/cache/pattern_finder/`，通过 `pattern_finder/config.py` 中的 `LIBRARY_CACHE_DIR` 配置。

### Q: 临时文件应该放哪里？

A: 使用系统临时目录（`/tmp/` 或 `tempfile.mkdtemp()`），不要放在项目目录中。
