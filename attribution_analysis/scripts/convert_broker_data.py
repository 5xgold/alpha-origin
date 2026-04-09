"""券商数据转换脚本：PDF/截图 → 标准 CSV"""

import re
import sys
import argparse
import pdfplumber
import pandas as pd
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))
from config import COLUMN_MAPPING, STANDARD_COLUMNS


def parse_pdf(pdf_path):
    """解析 PDF 交割单 - 跨页提取所有交易数据

    交易数据从「客户资金明细」标题后开始，跨多页延续直到「客户持股清单」。
    数据行以日期 (2026...) 开头，按空格分割为 17 个字段。
    """
    all_rows = []
    in_section = False

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue

            # 遇到持股清单说明交易流水结束
            if '客户持股清单' in text and in_section:
                # 这一页可能前半部分还有交易数据，后半部分是持股清单
                lines = text.split('\n')
                for line in lines:
                    if '客户持股清单' in line:
                        break
                    if line.strip().startswith('2026'):
                        parts = line.strip().split()
                        if len(parts) >= 17:
                            all_rows.append(parts[:17])
                break

            if '客户资金明细' in text:
                in_section = True

            if in_section:
                lines = text.split('\n')
                for line in lines:
                    if line.strip().startswith('2026'):
                        parts = line.strip().split()
                        if len(parts) >= 17:
                            all_rows.append(parts[:17])

    if not all_rows:
        raise ValueError("PDF 中未找到客户资金明细数据")

    headers = ['date', 'market', 'account', 'currency', 'business_type', 'code', 'name',
               'quantity', 'price', 'inventory', 'amount', 'balance',
               'brokerage_fee', 'stamp_duty', 'transfer_fee', 'other_fee', 'remark']

    return headers, all_rows


def normalize_columns(headers, rows):
    """列名标准化"""
    df = pd.DataFrame(rows, columns=headers)

    # 检查必需列
    required = ["date", "code", "name", "quantity", "price", "amount"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"缺少必需列: {missing}")

    # 补充缺失列
    for col in STANDARD_COLUMNS:
        if col not in df.columns:
            df[col] = ""

    # 推断买卖方向（必须在 net_amount 之前）
    df["direction"] = df.apply(infer_direction, axis=1)

    # 计算 net_amount
    df["net_amount"] = df.apply(calculate_net_amount, axis=1)

    # 过滤掉非股票交易（场外开基、申购配号等）
    df = df[df['market'].isin(['上海', '深圳', '沪港通']) | df['direction'].isin(['分红', '扣税'])]
    df = df[df['direction'].isin(['买入', '卖出', '分红', '扣税'])]

    return df[STANDARD_COLUMNS]


def infer_direction(row):
    """推断买卖方向"""
    # 从业务类型推断
    business_type = str(row.get("business_type", ""))
    remark = str(row.get("remark", ""))

    # 分红/扣税识别（优先于买卖判断）
    if "股息红利税补缴" in business_type or "股息红利税补缴" in remark:
        return "扣税"
    if "股息红利发放" in business_type or "红利入账" in business_type or \
       "股息红利发放" in remark or "红利入账" in remark:
        return "分红"

    if "买" in business_type or "买" in remark or "Buy" in business_type:
        return "买入"
    if "卖" in business_type or "卖" in remark or "Sell" in business_type:
        return "卖出"

    # 从金额符号推断（买入为负）
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


def convert_pdf_to_csv(pdf_path, output_path):
    """主函数：PDF → CSV"""
    print(f"正在解析 PDF: {pdf_path}")
    headers, rows = parse_pdf(pdf_path)

    print(f"提取到 {len(rows)} 行数据")
    print(f"表头: {headers}")

    print("正在标准化列名...")
    df = normalize_columns(headers, rows)

    print(f"转换完成，共 {len(df)} 条交易记录")
    print(f"保存到: {output_path}")
    df.to_csv(output_path, index=False, encoding="utf-8-sig")

    # 打印前5行供用户确认
    print("\n前5行数据预览：")
    print(df.head().to_string())


def parse_shareholding(pdf_path):
    """解析 PDF 客户持股清单 → DataFrame[code, name, market, quantity, cost_price]

    处理逻辑：
    - 数据行以账户号开头（8位数字），格式：账户 代码 名称 币种 市场 库存数 可用数 参考成本 收盘价 收盘市值 累计盈亏
    - 港股名称可能跨行（名称单独一行 + 数据行 + 名称续行），需要合并
    - 跳过场外产品持仓（OTC）
    """
    holdings = []
    # 匹配数据行：账户号(8位) + 代码 + ... 数值字段
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

                # 进入场外产品区域后停止
                if '场外产品' in line or 'OTC' in line:
                    in_otc = True
                if '客户资产信息' in line or 'Details of Client Assets' in line:
                    break
                if in_otc:
                    i += 1
                    continue

                m = data_pattern.match(line)
                if m and m.group(1) != '70617488':
                    # 表头行等，跳过
                    i += 1
                    continue

                if m and m.group(1) == '70617488':
                    rest = m.group(3)
                    parts = rest.split()

                    # 正常行：名称 币种 市场 库存数 可用数 参考成本 收盘价 收盘市值 累计盈亏
                    # 至少需要 名称 + 币种 + 市场 + 6个数值 = 9 个 token
                    if len(parts) >= 9:
                        code = m.group(2)
                        # 从右边取6个数值字段
                        nums = parts[-6:]
                        market = parts[-7]
                        # 名称 = 中间部分（去掉币种和市场和数值）
                        # parts[0:-7] 去掉 币种(人民币) 市场 和 6个数值
                        name = ' '.join(parts[0:-8]) if len(parts) > 9 else parts[0]
                        # 币种在 parts[-8]

                        holdings.append({
                            'code': code,
                            'name': name,
                            'market': market,
                            'quantity': int(float(nums[0])),
                            'cost_price': float(nums[2]),
                        })
                    else:
                        # 港股情况：名称跨行，数据行缺名称
                        # 向上找名称行（纯中文，非分隔线/表头）
                        code = m.group(2)
                        # rest 里没有名称，直接是 "人民币 市场 数值..."
                        all_parts = [m.group(2)] + parts
                        # 尝试从上一行和下一行拼名称
                        name_parts = []
                        # 上一行
                        if i > 0:
                            prev = lines[i - 1].strip()
                            if prev and '---' not in prev and '客户' not in prev and not prev[0].isdigit():
                                name_parts.append(prev)
                        # 下一行
                        if i + 1 < len(lines):
                            nxt = lines[i + 1].strip()
                            if nxt and '---' not in nxt and '客户' not in nxt and not nxt[0].isdigit() and '以下' not in nxt:
                                name_parts.append(nxt)
                                i += 1  # 跳过续行

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


def convert_shareholding_to_csv(pdf_path, output_path):
    """PDF 持股清单 → holdings CSV"""
    print(f"正在解析持股清单: {pdf_path}")
    df = parse_shareholding(pdf_path)

    if df.empty:
        print("未找到持股数据")
        return

    print(f"提取到 {len(df)} 条持仓记录")
    print(df.to_string(index=False))

    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"\n保存到: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="券商数据转换")
    parser.add_argument("--input", required=True, help="输入文件路径（PDF）")
    parser.add_argument("--output", required=True, help="输出 CSV 路径")
    parser.add_argument("--holdings", action="store_true", help="提取持股清单（而非交割单）")

    args = parser.parse_args()

    try:
        if args.holdings:
            convert_shareholding_to_csv(args.input, args.output)
        else:
            convert_pdf_to_csv(args.input, args.output)
        print("\n✓ 转换成功")
    except Exception as e:
        print(f"\n✗ 转换失败: {e}")
        sys.exit(1)
