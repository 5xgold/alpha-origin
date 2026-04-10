"""全局配置文件"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# 基准配置
BENCHMARK_INDEX = "000300"  # 沪深300
RISK_FREE_RATE = 0.018      # 年化无风险利率 1.8%

# 数据缓存
CACHE_DIR = "data/cache"
CACHE_EXPIRY_DAYS = 7
SECTOR_CACHE_DAYS = 30          # 行业分类缓存（不常变）
SECTOR_CLASSIFICATION = "sw"    # 申万行业分类

# 分析参数
ROLLING_WINDOW = 60         # 滚动 Beta 窗口（交易日）
MIN_TRADING_DAYS = 5       # 最少交易日数

# 报告配置
REPORT_TITLE = "策略归因分析报告"
OUTPUT_DIR = "output"

# 外部服务
TS_TOKEN = os.getenv("TS_TOKEN", "")
FUTU_HOST = os.getenv("FUTU_HOST", "127.0.0.1")
FUTU_PORT = int(os.getenv("FUTU_PORT", "11111"))

# 货币基金配置
MONETARY_FUND_CODE = "007864"       # 东方红货币C
EXTERNAL_FLOW_TYPES = {"银行转存", "银行转取"}
COLLATERAL_FLOW_TYPES = {"担保品划出", "担保品划入"}

# 标准列名
STANDARD_COLUMNS = [
    "date", "market", "code", "name", "direction",
    "quantity", "price", "amount", "brokerage_fee",
    "stamp_duty", "transfer_fee", "other_fee",
    "net_amount", "remark"
]

# PDF 列名映射（中英文）
COLUMN_MAPPING = {
    # 日期
    "成交日期": "date",
    "Starting Date": "date",
    "日期": "date",
    # 市场
    "股票市场": "market",
    "Stock Market": "market",
    "市场": "market",
    # 代码
    "证券代码": "code",
    "Securities Code": "code",
    "代码": "code",
    # 名称
    "证券名称": "name",
    "Securities Name": "name",
    "名称": "name",
    # 数量
    "成交数量": "quantity",
    "Transaction Amount": "quantity",
    "数量": "quantity",
    # 价格
    "成交均价": "price",
    "Transaction Average Price": "price",
    "价格": "price",
    # 金额
    "成交金额": "amount",
    "Transaction Amount": "amount",
    "金额": "amount",
    # 费用
    "手续费": "brokerage_fee",
    "Brokerage Fee": "brokerage_fee",
    "印花税": "stamp_duty",
    "Stamp Duty": "stamp_duty",
    "过户费": "transfer_fee",
    "Transfer Fee": "transfer_fee",
    "其他费用": "other_fee",
    "Other Expenses": "other_fee",
    # 备注
    "备注": "remark",
    "Remark": "remark",
}
