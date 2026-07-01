# 股息网格计算器

输入 A 股代码，自动获取 **TTM 股息 / 最新价 / 十年期国债收益率**，按固定股息率步进（默认 0.5%）生成买入/减仓网格。所有档位股息率均高于十年期无风险利率，提供相对无风险资产的息差安全垫。

> ⚠️ 数据自动获取，可能有延迟或错漏；股息为税前 TTM 口径；仅供参考，不构成投资建议。

## 核心逻辑

```
买入价 = 每股股息(TTM) / 目标股息率
```

股息率越高 → 买入价越低 → 越跌越买。现价所处股息率位置决定动作分区：

| 相对现价股息率 | 动作 |
|---|---|
| 低 ≥1.0pp（价更高） | 减仓/止盈区 |
| 低于现价 | 不加仓 |
| 高 >0 | 加仓 |
| 高 ≥1.5pp | 重点加仓 |
| 高 ≥3.0pp | 极端/深跌加仓 |

数据来源：baostock（分红 `query_dividend_data` / 行情 / 名称）。十年期国债自动源不稳定时回落到默认值（可手动覆盖）。数值计算全程 `decimal.Decimal`。

## 一、本地运行（立即可用）

```bash
# 仓库根目录，已激活 .venv
pip install streamlit baostock pandas requests   # 若未装
streamlit run dividend_grid_app.py
```

启动后浏览器打开 **http://localhost:8501** ，输入代码（如 `601318`）点「生成网格」。

命令行版（无界面，快速出表）：

```bash
python app/dividend_grid/cli.py --name 中国平安 --dividend 2.70 --price 53.48 --rf 1.73
```

## 二、公网部署（Streamlit Community Cloud，免费拿 URL）

Streamlit 无法由本地直接生成公网链接，需经托管平台。Community Cloud 免费且对接 GitHub，步骤：

1. **推送代码到 GitHub**（仓库需 push 到 github.com，可设为 private）。
2. 打开 https://share.streamlit.io ，用 GitHub 登录，点 **New app**。
3. 填写：
   - Repository：你的仓库
   - Branch：`main`
   - **Main file path：`dividend_grid_app.py`**（仓库根入口）
4. 展开 **Advanced settings → Python dependencies**：
   - ⚠️ 仓库根 `requirements.txt` 含 futu/faiss/mini-racer 等重依赖，云端构建会很慢甚至失败。
   - **指定本工具的最小依赖**：把 `app/dividend_grid/requirements.txt` 的内容贴进去，或在仓库根放一个只含
     `streamlit / baostock / pandas / requests` 的精简清单。
5. 点 **Deploy**，约 1-3 分钟后得到形如 `https://<your-app>.streamlit.app` 的公网链接。

部署后每次 `git push` 到该分支会自动重新部署。

## 文件结构

```
dividend_grid_app.py              # 部署/本地入口（仓库根）
app/dividend_grid/
├── grid.py                       # 核心网格计算（Decimal）
├── datasource.py                 # baostock 自动取数（TTM 股息/价格/名称）
├── cli.py                        # 命令行版
├── streamlit_app.py              # Web 界面
├── requirements.txt              # 最小部署依赖
└── tests/test_grid.py            # 单元测试
```

## TTM 口径说明

TTM = 过去 365 天内**已登记**的每股税前现金分红求和（按登记日+金额去重）。

举例：中国平安在 2025-06 ~ 2026-06 滚动窗口内登记了三笔（末期 1.62 + 中期 0.95 + 末期 1.75 = 4.32），TTM 股息为 4.32，与单一年度口径（2.70）不同，属正常现象。页面会列出每笔明细，便于核对。
