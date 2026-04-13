"""从券商 PDF 对账单完整解析组合资产，计算 TWR 收益率

支持东方证券汇总对账单格式：
- 客户资金明细：全量交易流水（含银证转账、货币基金、港股通费用等）
- 客户持股清单：期末持仓
- 客户资产信息：资产汇总
"""

import sys
from pathlib import Path
import pdfplumber
import pandas as pd
import numpy as np

sys.path.append(str(Path(__file__).parent.parent))
from config import MONETARY_FUND_CODE, EXTERNAL_FLOW_TYPES
from shared.data_provider import get_stock_prices, _is_hk


# PDF 交易流水标准列名（17列）
TXN_COLUMNS = [
    'date', 'market', 'account', 'currency', 'business_type',
    'code', 'name', 'quantity', 'price', 'inventory',
    'amount', 'balance',  # amount=变动金额, balance=资金后余额
    'brokerage_fee', 'stamp_duty', 'transfer_fee', 'other_fee', 'remark'
]

# 不含证券代码/名称的业务类型（15字段行）
NO_CODE_BIZ_TYPES = {'港股通组合费收取', '银行转存', '银行转取', '利息归本'}


def parse_all_transactions(pdf_path):
    """解析 PDF 全部交易流水（客户资金明细）

    处理两种行格式：
    - 17字段：标准交易行（含证券代码和名称）
    - 15字段：无证券代码的行（港股通组合费、银证转账等）

    Returns:
        DataFrame with TXN_COLUMNS
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
                    # 持股清单开始，交易流水结束
                    return _build_txn_df(all_rows)

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

    return _build_txn_df(all_rows)


def _build_txn_df(rows):
    """构建交易 DataFrame"""
    df = pd.DataFrame(rows, columns=TXN_COLUMNS)
    df['date'] = pd.to_datetime(df['date'], format='%Y%m%d')
    for col in ['quantity', 'price', 'inventory', 'amount', 'balance',
                'brokerage_fee', 'stamp_duty', 'transfer_fee', 'other_fee']:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    return df


def extract_external_flows(all_txns):
    """提取外部资金流（银证转账）

    Returns:
        DataFrame[date, amount] — 转入为正，转出为负
    """
    mask = all_txns['business_type'].isin(EXTERNAL_FLOW_TYPES)
    flows = all_txns.loc[mask, ['date', 'amount']].copy()
    # 按日汇总（同一天可能有多笔转账）
    flows = flows.groupby('date')['amount'].sum().reset_index()
    return flows


def _extract_hkd_cny_rates(all_txns):
    """从沪港通交易推算每日 HKD→CNY 汇率

    Returns:
        pd.Series indexed by date, values are HKD→CNY exchange rates
    """
    hk_trades = all_txns[
        (all_txns['market'] == '沪港通') &
        (all_txns['business_type'].isin(['证券买入', '证券卖出']))
    ].copy()

    rates = {}
    for _, t in hk_trades.iterrows():
        qty = abs(t['quantity'])
        price_hkd = t['price']
        fees = t['brokerage_fee'] + t['stamp_duty'] + t['transfer_fee'] + t['other_fee']
        gross_cny = abs(t['amount']) - fees
        if qty > 0 and price_hkd > 0:
            rate = gross_cny / (qty * price_hkd)
            date = t['date']
            rates[date] = rate  # 同一天多笔取最后一笔

    if not rates:
        return pd.Series(dtype=float)

    rate_series = pd.Series(rates).sort_index()
    return rate_series


def build_daily_portfolio(all_txns, start_date, end_date):
    """从交易流水重建每日组合资产

    Returns:
        DataFrame[date, stock_value, cash, mf_value, total_value]
    """
    start_dt = pd.to_datetime(start_date)
    end_dt = pd.to_datetime(end_date)

    # === 1. 每日现金余额（从资金后余额直接取） ===
    daily_cash = all_txns.groupby('date')['balance'].last()

    # 期初现金：第一笔交易前的余额
    first_row = all_txns.iloc[0]
    initial_cash = first_row['balance'] - first_row['amount']
    print(f"  期初现金余额: {initial_cash:.2f}")

    # === 2. 货币基金份额跟踪 ===
    mf_txns = all_txns[all_txns['code'] == MONETARY_FUND_CODE].copy()
    mf_daily_shares = _track_monetary_fund(mf_txns)
    print(f"  货币基金: 期初 {mf_daily_shares.iloc[0]:.0f} 份 → 期末 {mf_daily_shares.iloc[-1]:.0f} 份")

    # === 3. 股票持仓跟踪 ===
    initial_positions, stock_positions = _track_stock_positions(all_txns)

    # === 4. 港股汇率 ===
    hkd_rates = _extract_hkd_cny_rates(all_txns)
    if not hkd_rates.empty:
        print(f"  港币汇率: {hkd_rates.iloc[0]:.4f} → {hkd_rates.iloc[-1]:.4f}")

    # === 5. 获取股票价格，计算每日市值 ===
    all_codes = set(initial_positions.keys())
    for positions in stock_positions.values():
        all_codes.update(positions.keys())
    # 排除货币基金和空代码
    all_codes.discard(MONETARY_FUND_CODE)
    all_codes.discard('')

    print(f"  获取 {len(all_codes)} 只股票/ETF 行情...")
    stock_prices = {}
    for code in all_codes:
        # 先获取分析期间的价格
        prices = get_stock_prices(code, start_dt.strftime('%Y%m%d'), end_dt.strftime('%Y%m%d'))
        if not prices.empty:
            ps = prices.set_index('date')['close']
            # 再获取期初前的价格（用于 day 0，往前找20天确保覆盖假期）
            pre_start = (start_dt - pd.Timedelta(days=20)).strftime('%Y%m%d')
            pre_end = (start_dt - pd.Timedelta(days=1)).strftime('%Y%m%d')
            pre_prices = get_stock_prices(code, pre_start, pre_end)
            if not pre_prices.empty:
                pre_ps = pre_prices.set_index('date')['close']
                ps = pd.concat([pre_ps, ps]).sort_index()
                ps = ps[~ps.index.duplicated(keep='last')]
            stock_prices[code] = ps

    # === 6. 辅助函数：计算持仓市值（港股自动转换为人民币） ===
    def _get_hkd_rate(date):
        """获取指定日期的港币汇率（前向填充）"""
        if hkd_rates.empty:
            return 0.9  # 默认汇率
        valid = hkd_rates[hkd_rates.index <= date]
        if not valid.empty:
            return valid.iloc[-1]
        return hkd_rates.iloc[0]

    def _calc_stock_value(positions, date):
        value = 0.0
        missing = []
        for code, qty in positions.items():
            price = None
            if code in stock_prices and date in stock_prices[code].index:
                price = stock_prices[code].loc[date]
            elif code in stock_prices and not stock_prices[code].empty:
                valid = stock_prices[code][stock_prices[code].index <= date]
                if not valid.empty:
                    price = valid.iloc[-1]

            if price is not None:
                # 港股价格是港币，需要转换
                if _is_hk(str(code)):
                    price *= _get_hkd_rate(date)
                value += price * qty
            else:
                missing.append(code)
        return value, missing

    # === 7. 构建每日资产（含 day 0 期初值） ===
    txn_dates = sorted(all_txns['date'].unique())
    first_txn_date = txn_dates[0]

    # 期初货基份额
    initial_mf = mf_daily_shares.iloc[0] if not mf_daily_shares.empty else 0

    # Day 0：期初值（第一个交易日开盘前，用前一个交易日的收盘价）
    day0_date = first_txn_date - pd.Timedelta(days=1)
    # 找前一个交易日的日期（用于获取收盘价）
    pre_trade_date = None
    for code in initial_positions:
        if code in stock_prices:
            valid = stock_prices[code][stock_prices[code].index < first_txn_date]
            if not valid.empty:
                pre_trade_date = valid.index[-1]
                break
    if pre_trade_date is None:
        pre_trade_date = day0_date

    # 对于期初价格缺失的股票，用交易数据估算
    # 从第一笔交易的成交价推算前一天的价格
    for code in list(initial_positions.keys()):
        if code not in stock_prices or pre_trade_date not in stock_prices.get(code, pd.Series()).index:
            # 找该股票的第一笔交易价格
            code_txns = all_txns[(all_txns['code'] == str(code)) &
                                 (all_txns['business_type'].isin(['证券买入', '证券卖出']))]
            if not code_txns.empty:
                first_txn = code_txns.iloc[0]
                est_price = first_txn['price']
                if code in stock_prices:
                    stock_prices[code].loc[pre_trade_date] = est_price
                    stock_prices[code] = stock_prices[code].sort_index()
                else:
                    stock_prices[code] = pd.Series({pre_trade_date: est_price})

    initial_stock_value, missing_codes = _calc_stock_value(initial_positions, pre_trade_date)
    if missing_codes:
        print(f"  警告: 以下股票无行情数据，市值缺失: {', '.join(missing_codes)}")
    initial_total = initial_stock_value + initial_cash + initial_mf * 1.0

    print(f"  期初总资产: {initial_total:.0f} "
          f"(股票 {initial_stock_value:.0f} + "
          f"现金 {initial_cash:.0f} + "
          f"货基 {initial_mf:.0f})")

    # 构建交易日序列
    trading_days = pd.bdate_range(start_dt, end_dt, freq='B')
    all_dates = sorted(set(trading_days) | set(pd.to_datetime(txn_dates)))
    all_dates = [d for d in all_dates if start_dt <= d <= end_dt]

    # Day 0 用前一天的日期标记
    day0_date = first_txn_date - pd.Timedelta(days=1)
    daily_values = [{
        'date': day0_date,
        'stock_value': initial_stock_value,
        'cash': initial_cash,
        'mf_value': initial_mf * 1.0,
        'total_value': initial_total,
    }]

    prev_cash = initial_cash
    prev_mf_shares = initial_mf
    prev_positions = initial_positions

    for date in all_dates:
        # 现金
        if date in daily_cash.index:
            cash = daily_cash.loc[date]
        else:
            cash = prev_cash

        # 货币基金
        if date in mf_daily_shares.index:
            mf_shares = mf_daily_shares.loc[date]
        else:
            mf_shares = prev_mf_shares
        mf_value = mf_shares * 1.0  # NAV ≈ 1.0

        # 股票市值
        if date in stock_positions:
            positions = stock_positions[date]
        else:
            positions = prev_positions

        stock_value, _ = _calc_stock_value(positions, date)

        total = stock_value + cash + mf_value
        daily_values.append({
            'date': date,
            'stock_value': stock_value,
            'cash': cash,
            'mf_value': mf_value,
            'total_value': total,
        })

        prev_cash = cash
        prev_mf_shares = mf_shares
        prev_positions = positions

    result = pd.DataFrame(daily_values)
    if not result.empty:
        print(f"  期末总资产: {result.iloc[-1]['total_value']:.0f} "
              f"(股票 {result.iloc[-1]['stock_value']:.0f} + "
              f"现金 {result.iloc[-1]['cash']:.0f} + "
              f"货基 {result.iloc[-1]['mf_value']:.0f})")

    # === 8. 计算担保品划转的市值（作为外部资金流） ===
    collateral_flows = _calc_collateral_flows(all_txns, stock_prices, _get_hkd_rate)

    return result, stock_prices, collateral_flows


def _calc_collateral_flows(all_txns, stock_prices, get_hkd_rate_fn):
    """计算担保品划转的市值，作为外部资金流

    担保品划出 = 资产流出（负数），担保品划入 = 资产流入（正数）
    """
    collateral_txns = all_txns[
        all_txns['business_type'].isin(['担保品划出', '担保品划入'])
    ].copy()

    if collateral_txns.empty:
        return pd.DataFrame(columns=['date', 'amount'])

    flows = []
    for _, t in collateral_txns.iterrows():
        code = t['code']
        qty = abs(t['quantity'])
        date = t['date']
        biz = t['business_type']

        # 获取当日市价
        price = None
        if code in stock_prices and date in stock_prices[code].index:
            price = stock_prices[code].loc[date]
        elif code in stock_prices and not stock_prices[code].empty:
            valid = stock_prices[code][stock_prices[code].index <= date]
            if not valid.empty:
                price = valid.iloc[-1]

        if price is not None:
            # 港股转换汇率
            if _is_hk(str(code)):
                price *= get_hkd_rate_fn(date)
            market_value = price * qty
        else:
            # 无市价，用买入成本估算
            buy_txns = all_txns[(all_txns['code'] == code) &
                                (all_txns['business_type'] == '证券买入') &
                                (all_txns['date'] <= date)]
            if not buy_txns.empty:
                avg_price = abs(buy_txns['amount'].sum()) / buy_txns['quantity'].sum()
                market_value = avg_price * qty
            else:
                market_value = 0

        # 划出 = 负（资产流出），划入 = 正（资产流入）
        if biz == '担保品划出':
            flows.append({'date': date, 'amount': -market_value})
        else:  # 担保品划入
            flows.append({'date': date, 'amount': market_value})

    result = pd.DataFrame(flows)
    result = result.groupby('date')['amount'].sum().reset_index()

    total = result['amount'].sum()
    print(f"  担保品划转净额: {total:+.0f} (划出{len(collateral_txns[collateral_txns['business_type']=='担保品划出'])}笔, "
          f"划入{len(collateral_txns[collateral_txns['business_type']=='担保品划入'])}笔)")

    return result


def _track_monetary_fund(mf_txns):
    """跟踪货币基金每日份额

    从交易流水的 inventory 列获取每笔交易后的份额。
    """
    if mf_txns.empty:
        return pd.Series(dtype=float)

    # inventory 列就是交易后的库存份额
    daily_shares = mf_txns.groupby('date')['inventory'].last()

    # 推算期初份额：第一笔交易前的份额
    first = mf_txns.iloc[0]
    biz = first['business_type']
    if '申购' in biz:
        initial = first['inventory'] - first['quantity']
    elif '赎回' in biz:
        initial = first['inventory'] + abs(first['quantity'])
    elif '红利' in biz:
        initial = first['inventory'] - first['quantity']
    else:
        initial = first['inventory']

    # 在第一个交易日之前插入期初值
    first_date = mf_txns['date'].min()
    pre_date = first_date - pd.Timedelta(days=1)
    daily_shares.loc[pre_date] = initial
    daily_shares = daily_shares.sort_index()

    return daily_shares


def _track_stock_positions(all_txns):
    """从交易流水重建每日股票持仓

    使用 inventory（库存数）列直接获取每笔交易后的持仓。

    Returns:
        (initial_positions, daily_positions)
        initial_positions: {code: qty} 期初持仓（第一笔交易前）
        daily_positions: {date: {code: qty}} 每日收盘持仓
    """
    # 筛选有证券代码的交易（排除货币基金和无代码行）
    stock_txns = all_txns[
        (all_txns['code'] != '') &
        (all_txns['code'] != MONETARY_FUND_CODE) &
        (all_txns['market'].isin(['上海', '深圳', '沪港通']))
    ].copy()

    # 按日期和代码，取最后一笔的 inventory
    positions = {}  # {code: qty} 当前持仓
    daily_positions = {}  # {date: {code: qty}}

    # 先推算期初持仓
    # 对于每只股票，找第一笔交易，从 inventory 反推期初持仓
    initial_positions = {}
    for code in stock_txns['code'].unique():
        code_txns = stock_txns[stock_txns['code'] == code].sort_values('date')
        first = code_txns.iloc[0]
        biz = first['business_type']
        inv = int(first['inventory'])
        qty = int(abs(first['quantity']))

        if '买入' in biz:
            initial = inv - qty
        elif '卖出' in biz:
            initial = inv + qty
        elif '担保品划出' in biz:
            initial = inv + qty
        elif '担保品划入' in biz:
            initial = inv - qty
        elif '股息红利税补缴' in biz:
            initial = inv  # 税补缴不影响持仓数量
        else:
            initial = inv

        if initial > 0:
            initial_positions[code] = initial

    positions = dict(initial_positions)

    # 逐日更新持仓
    for date in sorted(stock_txns['date'].unique()):
        day_txns = stock_txns[stock_txns['date'] == date].sort_index()
        for _, txn in day_txns.iterrows():
            code = txn['code']
            inv = int(txn['inventory'])
            positions[code] = inv

        # 保存当日持仓快照（只保留 qty > 0 的）
        daily_positions[date] = {c: q for c, q in positions.items() if q > 0}

    return initial_positions, daily_positions


def calculate_twr(daily_values, external_flows):
    """计算时间加权收益率 (TWR)

    在每个外部资金流日期切分子区间，链式相乘。

    Args:
        daily_values: DataFrame[date, total_value]
        external_flows: DataFrame[date, amount]

    Returns:
        float: TWR
    """
    if daily_values.empty or len(daily_values) < 2:
        return 0.0

    dv = daily_values.set_index('date')['total_value']
    flow_dates = set(external_flows['date']) if not external_flows.empty else set()
    flow_map = {}
    if not external_flows.empty:
        for _, row in external_flows.iterrows():
            flow_map[row['date']] = row['amount']

    dates = sorted(dv.index)
    twr = 1.0

    for i in range(1, len(dates)):
        prev_date = dates[i - 1]
        curr_date = dates[i]
        prev_value = dv.loc[prev_date]
        curr_value = dv.loc[curr_date]

        # 如果当天有外部资金流，调整前一天的值
        flow = flow_map.get(curr_date, 0)
        adjusted_prev = prev_value + flow

        if adjusted_prev > 0:
            daily_return = curr_value / adjusted_prev
            twr *= daily_return

    return twr - 1.0
