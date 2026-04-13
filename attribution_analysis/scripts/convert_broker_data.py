"""券商数据转换脚本：PDF → 标准 CSV（trades + holdings + cash_flows + asset_summary）"""

import re
import sys
import json
import shutil
import argparse
import pdfplumber
import pandas as pd
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from config import (
    STANDARD_COLUMNS, CACHE_DIR,
    EXTERNAL_FLOW_TYPES, COLLATERAL_FLOW_TYPES, MONETARY_FUND_CODE,
)
from shared.data_provider import get_stock_prices, _is_hk


# PDF 交易流水标准列名（17列）
TXN_COLUMNS = [
    'date', 'market', 'account', 'currency', 'business_type',
    'code', 'name', 'quantity', 'price', 'inventory',
    'amount', 'balance',
    'brokerage_fee', 'stamp_duty', 'transfer_fee', 'other_fee', 'remark'
]


def parse_pdf(pdf_path):
    """解析 PDF 交割单 - 跨页提取所有交易数据

    处理两种行格式：
    - 17字段：标准交易行（含证券代码和名称）
    - 15字段：无证券代码的行（港股通组合费、银证转账等）

    Returns:
        (headers, rows) — headers 为 TXN_COLUMNS，rows 为 list of lists
    """
    all_rows = []
    in_section = False

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue

            lines = text.split('\n')
            for line in lines:
                stripped = line.strip()

                if '客户持股清单' in stripped and in_section:
                    return TXN_COLUMNS, all_rows

                if '客户资金明细' in stripped:
                    in_section = True
                    continue

                if in_section and stripped.startswith('2026'):
                    parts = stripped.split()
                    if len(parts) >= 17:
                        all_rows.append(parts[:17])
                    elif len(parts) >= 15:
                        # 无证券代码/名称的行，补齐到17列
                        row = parts[:5] + ['', ''] + parts[5:15]
                        all_rows.append(row)

    if not all_rows:
        raise ValueError("PDF 中未找到客户资金明细数据")

    return TXN_COLUMNS, all_rows


def _build_raw_df(headers, rows):
    """构建原始交易 DataFrame（含全部17列，数值已转换）"""
    df = pd.DataFrame(rows, columns=headers)
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
    for col in ['quantity', 'price', 'inventory', 'amount', 'balance',
                'brokerage_fee', 'stamp_duty', 'transfer_fee', 'other_fee']:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    return df


def normalize_columns(headers, rows):
    """列名标准化 → STANDARD_COLUMNS（14列 trades）"""
    df = pd.DataFrame(rows, columns=headers)

    required = ["date", "code", "name", "quantity", "price", "amount"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"缺少必需列: {missing}")

    for col in STANDARD_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    df["direction"] = df.apply(infer_direction, axis=1)
    df["net_amount"] = df.apply(calculate_net_amount, axis=1)

    # 过滤掉非股票交易
    df = df[df['code'].notna() & (df['code'] != '')]
    df = df[df['market'].isin(['上海', '深圳', '沪港通']) | df['direction'].isin(['分红', '扣税'])]
    df = df[df['direction'].isin(['买入', '卖出', '分红', '扣税'])]

    return df[STANDARD_COLUMNS]


def infer_direction(row):
    """推断买卖方向"""
    business_type = str(row.get("business_type", ""))
    remark = str(row.get("remark", ""))

    if "股息红利税补缴" in business_type or "股息红利税补缴" in remark:
        return "扣税"
    if "股息红利发放" in business_type or "红利入账" in business_type or \
       "股息红利发放" in remark or "红利入账" in remark:
        return "分红"

    if "买" in business_type or "买" in remark or "Buy" in business_type:
        return "买入"
    if "卖" in business_type or "卖" in remark or "Sell" in business_type:
        return "卖出"

    amount = float(row.get("amount", 0) or 0)
    if amount < 0:
        return "买入"
    elif amount > 0:
        return "卖出"

    return "未知"


def calculate_net_amount(row):
    """计算实际收付金额"""
    amount = float(row.get("amount", 0))
    fee = float(row.get("brokerage_fee", 0))
    stamp = float(row.get("stamp_duty", 0))
    transfer = float(row.get("transfer_fee", 0))
    other = float(row.get("other_fee", 0))
    total_fee = fee + stamp + transfer + other

    if row.get("direction") == "买入":
        return -(amount + total_fee)
    else:
        return amount - total_fee


def extract_cash_flows(raw_df):
    """从原始交易流水提取外部资金流（银证转账 + 担保品划转）

    Returns:
        DataFrame[date, amount, type]
    """
    flows = []

    # 1. 银证转账
    bank_mask = raw_df['business_type'].isin(EXTERNAL_FLOW_TYPES)
    for _, row in raw_df.loc[bank_mask].iterrows():
        flows.append({
            'date': row['date'].strftime('%Y-%m-%d'),
            'amount': row['amount'],
            'type': row['business_type'],
        })

    # 2. 担保品划转 — 用市价估算
    collateral_mask = raw_df['business_type'].isin(COLLATERAL_FLOW_TYPES)
    collateral_txns = raw_df.loc[collateral_mask].copy()

    if not collateral_txns.empty:
        codes = [c for c in collateral_txns['code'].unique() if c and c != MONETARY_FUND_CODE]
        if codes:
            date_min = raw_df['date'].min().strftime('%Y%m%d')
            date_max = raw_df['date'].max().strftime('%Y%m%d')

            stock_prices = {}
            for code in codes:
                try:
                    df = get_stock_prices(code, date_min, date_max)
                    if df is not None and not df.empty:
                        # get_stock_prices 返回 DataFrame[date,open,close,...]
                        # 转为 Series[date → close] 方便查价
                        s = df.set_index('date')['close']
                        s.index = pd.to_datetime(s.index)
                        stock_prices[code] = s
                except Exception:
                    pass

            # 港币汇率（从沪港通交易推算）
            hk_trades = raw_df[
                (raw_df['market'] == '沪港通') &
                (raw_df['business_type'].isin(['证券买入', '证券卖出']))
            ]
            hkd_rates = {}
            for _, t in hk_trades.iterrows():
                qty = abs(t['quantity'])
                price_hkd = t['price']
                fees = t['brokerage_fee'] + t['stamp_duty'] + t['transfer_fee'] + t['other_fee']
                gross_cny = abs(t['amount']) - fees
                if qty > 0 and price_hkd > 0:
                    hkd_rates[t['date']] = gross_cny / (qty * price_hkd)

            for _, t in collateral_txns.iterrows():
                code = t['code']
                qty = abs(t['quantity'])
                date = t['date']
                biz = t['business_type']

                price = None
                if code in stock_prices and date in stock_prices[code].index:
                    price = stock_prices[code].loc[date]
                elif code in stock_prices and not stock_prices[code].empty:
                    valid = stock_prices[code][stock_prices[code].index <= date]
                    if not valid.empty:
                        price = valid.iloc[-1]

                if price is not None:
                    if _is_hk(str(code)):
                        rate = 1.0
                        for d in sorted(hkd_rates.keys()):
                            if d <= date:
                                rate = hkd_rates[d]
                        price *= rate
                    market_value = price * qty
                else:
                    buy_txns = raw_df[(raw_df['code'] == code) &
                                     (raw_df['business_type'] == '证券买入') &
                                     (raw_df['date'] <= date)]
                    if not buy_txns.empty:
                        avg_price = abs(buy_txns['amount'].sum()) / buy_txns['quantity'].sum()
                        market_value = avg_price * qty
                    else:
                        market_value = 0

                amount = -market_value if biz == '担保品划出' else market_value
                flows.append({
                    'date': date.strftime('%Y-%m-%d'),
                    'amount': amount,
                    'type': biz,
                })

    if not flows:
        return pd.DataFrame(columns=['date', 'amount', 'type'])

    result = pd.DataFrame(flows)
    result = result.groupby(['date', 'type'])['amount'].sum().reset_index()
    result = result.sort_values('date').reset_index(drop=True)
    return result


def parse_shareholding(pdf_path):
    """解析 PDF 客户持股清单 → DataFrame[code, name, market, quantity, cost_price]"""
    holdings = []
    data_pattern = re.compile(r'^(\d{8})\s+(\S+)\s+(.+)$')

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if '客户持股清单' not in text and 'Shareholding' not in text:
                continue

            lines = text.split('\n')
            in_otc = False
            i = 0
            while i < len(lines):
                line = lines[i].strip()

                if '场外产品' in line or 'OTC' in line:
                    in_otc = True
                if '客户资产信息' in line or 'Details of Client Assets' in line:
                    break
                if in_otc:
                    i += 1
                    continue

                m = data_pattern.match(line)
                if m and m.group(1) != '70617488':
                    i += 1
                    continue

                if m and m.group(1) == '70617488':
                    rest = m.group(3)
                    parts = rest.split()

                    if len(parts) >= 9:
                        code = m.group(2)
                        nums = parts[-6:]
                        market = parts[-7]
                        name = ' '.join(parts[0:-8]) if len(parts) > 9 else parts[0]

                        holdings.append({
                            'code': code,
                            'name': name,
                            'market': market,
                            'quantity': int(float(nums[0])),
                            'cost_price': float(nums[2]),
                        })
                    else:
                        code = m.group(2)
                        name_parts = []
                        if i > 0:
                            prev = lines[i - 1].strip()
                            if prev and '---' not in prev and '客户' not in prev and not prev[0].isdigit():
                                name_parts.append(prev)
                        if i + 1 < len(lines):
                            nxt = lines[i + 1].strip()
                            if nxt and '---' not in nxt and '客户' not in nxt and not nxt[0].isdigit() and '以下' not in nxt:
                                name_parts.append(nxt)
                                i += 1

                        name = ''.join(name_parts) if name_parts else ''
                        nums = parts[-6:]
                        market = parts[-7] if len(parts) >= 7 else ''

                        if market in ('上海', '深圳', '沪港通'):
                            holdings.append({
                                'code': code,
                                'name': name,
                                'market': market,
                                'quantity': int(float(nums[0])),
                                'cost_price': float(nums[2]),
                            })

                i += 1

    return pd.DataFrame(holdings)


def parse_asset_summary(pdf_path):
    """解析 PDF 客户资产信息 → dict

    Returns:
        dict: {fund_balance, available_capital, total_market_value, total_assets}
    """
    pattern = re.compile(
        r'资金余额.*?[：:]\s*([\d,.]+).*?'
        r'可用资金.*?[：:]\s*([\d,.]+).*?'
        r'市值合计.*?[：:]\s*([\d,.]+).*?'
        r'资产合计.*?[：:]\s*([\d,.]+)',
        re.DOTALL
    )

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if '客户资产信息' not in text and 'Details of Client Assets' not in text:
                continue

            m = pattern.search(text)
            if m:
                return {
                    'fund_balance': float(m.group(1).replace(',', '')),
                    'available_capital': float(m.group(2).replace(',', '')),
                    'total_market_value': float(m.group(3).replace(',', '')),
                    'total_assets': float(m.group(4).replace(',', '')),
                }

    return {}


def export_all(pdf_path, output_dir, force_refresh=False):
    """一次性输出 trades.csv + holdings.csv + cash_flows.csv"""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if force_refresh:
        cache_dir = Path(CACHE_DIR)
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            print(f"已清空行情缓存: {cache_dir}")

    print(f"正在解析 PDF: {pdf_path}")
    headers, rows = parse_pdf(pdf_path)
    print(f"提取到 {len(rows)} 行原始数据")

    raw_df = _build_raw_df(headers, rows)

    # trades.csv
    trades_path = output_dir / "trades.csv"
    print("正在生成 trades.csv...")
    trades_df = normalize_columns(headers, rows)
    trades_df.to_csv(trades_path, index=False, encoding="utf-8-sig")
    print(f"  → {trades_path} ({len(trades_df)} 条交易)")

    # holdings.csv
    holdings_path = output_dir / "holdings.csv"
    print("正在生成 holdings.csv...")
    holdings_df = parse_shareholding(pdf_path)
    holdings_df.to_csv(holdings_path, index=False, encoding="utf-8-sig")
    print(f"  → {holdings_path} ({len(holdings_df)} 条持仓)")

    # cash_flows.csv
    cash_flows_path = output_dir / "cash_flows.csv"
    print("正在生成 cash_flows.csv...")
    cash_flows_df = extract_cash_flows(raw_df)
    cash_flows_df.to_csv(cash_flows_path, index=False, encoding="utf-8-sig")
    print(f"  → {cash_flows_path} ({len(cash_flows_df)} 条资金流)")

    # asset_summary.json
    asset_path = output_dir / "asset_summary.json"
    print("正在解析资产信息...")
    asset_summary = parse_asset_summary(pdf_path)
    if asset_summary:
        with open(asset_path, 'w', encoding='utf-8') as f:
            json.dump(asset_summary, f, ensure_ascii=False, indent=2)
        print(f"  → {asset_path} (总资产: ¥{asset_summary['total_assets']:,.0f})")
    else:
        print("  ⚠ 未能解析客户资产信息")

    return trades_df, holdings_df, cash_flows_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="券商数据转换：PDF → 标准 CSV")
    parser.add_argument("--input", required=True, help="输入 PDF 文件路径")
    parser.add_argument("--output-dir", required=True, help="输出目录（生成 trades + holdings + cash_flows）")
    parser.add_argument("--force-refresh", action="store_true",
                        help="清空行情缓存后重新执行")

    args = parser.parse_args()

    try:
        export_all(args.input, args.output_dir, force_refresh=args.force_refresh)
        print("\n✓ 转换成功")
    except Exception as e:
        print(f"\n✗ 转换失败: {e}")
        sys.exit(1)
