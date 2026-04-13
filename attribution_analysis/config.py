"""归因分析模块配置"""

import sys
from pathlib import Path

# 确保项目根目录在 sys.path
sys.path.append(str(Path(__file__).parent.parent))
from shared.config import (  # noqa: E402, F401 — re-export for backward compat
    TS_TOKEN, FUTU_HOST, FUTU_PORT,
    CACHE_DIR, CACHE_EXPIRY_DAYS, SECTOR_CACHE_DAYS, SECTOR_CLASSIFICATION,
    parse_benchmark_config,
)

# 基准配置（支持单一基准或复合基准）
BENCHMARK_INDEX = [
    {"index": "000300", "weight": 0.65},    # 沪深300
    {"index": "HK.800000", "weight": 0.35}, # 恒生指数
]

RISK_FREE_RATE = 0.018      # 年化无风险利率 1.8%

# 分析参数
ROLLING_WINDOW = 60         # 滚动 Beta 窗口（交易日）
MIN_TRADING_DAYS = 5        # 最少交易日数

# 报告配置
REPORT_TITLE = "策略归因分析报告"
OUTPUT_DIR = "output"

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
    "成交日期": "date", "Starting Date": "date", "日期": "date",
    "股票市场": "market", "Stock Market": "market", "市场": "market",
    "证券代码": "code", "Securities Code": "code", "代码": "code",
    "证券名称": "name", "Securities Name": "name", "名称": "name",
    "成交数量": "quantity", "Transaction Amount": "quantity", "数量": "quantity",
    "成交均价": "price", "Transaction Average Price": "price", "价格": "price",
    "成交金额": "amount", "Transaction Amount": "amount", "金额": "amount",
    "手续费": "brokerage_fee", "Brokerage Fee": "brokerage_fee",
    "印花税": "stamp_duty", "Stamp Duty": "stamp_duty",
    "过户费": "transfer_fee", "Transfer Fee": "transfer_fee",
    "其他费用": "other_fee", "Other Expenses": "other_fee",
    "备注": "remark", "Remark": "remark",
}
