"""股息网格计算器 — Streamlit 线上工具

输入 A 股代码, 自动获取 TTM 股息/最新价/十年期国债收益率,
按 0.5% 股息率步进生成买入/减仓网格。

本地运行:
    streamlit run app/dividend_grid/streamlit_app.py
"""

import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.append(str(Path(__file__).parent.parent))
from dividend_grid.grid import build_grid, to_decimal
from dividend_grid.datasource import (
    fetch_snapshot,
    fetch_treasury_yield_pct,
    normalize_a_code,
    DEFAULT_TREASURY_YIELD_PCT,
)

_HUNDRED = Decimal("100")
_Q2 = Decimal("0.01")
_Q1 = Decimal("0.1")
_Q4 = Decimal("0.0001")


@st.cache_data(ttl=3600, show_spinner=False)
def _cached_snapshot(code: str):
    """缓存快照 1 小时, 避免重复请求 baostock"""
    snap = fetch_snapshot(code)
    # dataclass 不便直接缓存, 转成 dict
    return {
        "code": snap.code,
        "name": snap.name,
        "price": str(snap.price),
        "price_date": snap.price_date,
        "ttm_dividend": str(snap.ttm_dividend),
        "annual_dividend": str(snap.annual_dividend),
        "annual_year": snap.annual_year,
        "components": [(c.regist_date, str(c.cash_pre_tax)) for c in snap.components],
        "annual_components": [(c.regist_date, str(c.cash_pre_tax)) for c in snap.annual_components],
        "warnings": snap.warnings,
    }


def _pct(x, q=_Q2):
    return (to_decimal(x) * _HUNDRED).quantize(q)


def _build_dataframe(levels):
    """网格档位 → 展示用 DataFrame(字符串, 保留精度)"""
    data = []
    for lv in levels:
        ratio = f"{lv.ratio_vs_riskfree.quantize(_Q2)}x" if lv.ratio_vs_riskfree is not None else "∞"
        is_now = lv.action == "现价"
        data.append({
            "目标股息率": f"{_pct(lv.yield_rate, _Q1)}%",
            "买入价": f"{lv.price.quantize(_Q2)}",
            "较现价": f"{_pct(lv.pct_vs_current, _Q1):+}%",
            "性价比": ratio,
            "动作": ("▶ " if is_now else "") + lv.action,
        })
    return pd.DataFrame(data)


def main():
    st.set_page_config(page_title="股息网格计算器", page_icon="📈", layout="centered")
    st.title("📈 股息网格计算器")
    st.caption("输入 A 股代码 → 自动取 TTM 股息 / 最新价 / 十年期国债 → 生成 0.5% 步进网格")

    # ---------- 输入区 ----------
    col1, col2 = st.columns([2, 1])
    with col1:
        code_input = st.text_input("A 股代码", value="601318",
                                   help="支持 601318 / sh.601318 / 601318.SH / 000001 等格式")
    with col2:
        step_pct = st.selectbox("股息率步进", ["0.5", "0.25", "1.0"], index=0,
                                help="相邻档位的股息率间隔(%)")

    with st.expander("高级设置(网格上下边界 / 国债收益率覆盖)"):
        c1, c2, c3 = st.columns(3)
        low_pct = c1.text_input("最低股息率 %", value="4.0")
        high_pct = c2.text_input("最高股息率 %", value="9.0")
        rf_override = c3.text_input("十年期国债 % (留空=自动)", value="",
                                    help="自动获取失败时用默认值, 可在此手动覆盖")

    run = st.button("生成网格", type="primary", use_container_width=True)
    if run:
        st.session_state["dg_generated"] = True
    if not st.session_state.get("dg_generated"):
        st.info("输入代码后点击「生成网格」。数据来自 baostock(分红/行情), 仅供参考, 非投资建议。")
        return

    # ---------- 取数 ----------
    try:
        code = normalize_a_code(code_input)
    except ValueError as e:
        st.error(f"代码格式错误: {e}")
        return

    with st.spinner(f"正在获取 {code} 的分红与行情数据..."):
        try:
            snap = _cached_snapshot(code)
        except Exception as e:
            st.error(f"数据获取失败: {e}")
            return

    name = snap["name"]
    price = to_decimal(snap["price"])
    ttm = to_decimal(snap["ttm_dividend"])
    annual = to_decimal(snap["annual_dividend"])
    annual_year = snap["annual_year"]
    components = snap["components"]
    annual_components = snap["annual_components"]

    if ttm <= 0 and annual <= 0:
        st.warning("未查到现金分红记录, 无法计算股息网格。请确认该标的是否分红。")
        return

    # ---------- 口径选择(默认年度, 更稳) ----------
    ttm_yield = (ttm / price * _HUNDRED).quantize(_Q2) if ttm > 0 else Decimal("0")
    annual_yield = (annual / price * _HUNDRED).quantize(_Q2) if annual > 0 else Decimal("0")
    annual_label = (f"最近完整年报年度（{annual_year}年度 {annual.normalize()}元 → {annual_yield}%）"
                    if annual > 0 else "完整年报年度（无数据）")
    ttm_label = f"TTM 近12月（{ttm.normalize()}元 → {ttm_yield}%）" if ttm > 0 else "TTM（无数据）"

    options, default_idx = [], 0
    if annual > 0:
        options.append(("annual", annual_label))
    if ttm > 0:
        options.append(("ttm", ttm_label))
    # 默认选年度(若可用)
    labels = [o[1] for o in options]
    choice = st.radio("股息口径", labels, index=0, horizontal=False,
                      help="完整年报年度=最近一个已含年报末期分红的报告年度全年股息(中期+末期, "
                           "按预案公告月份判定报告年度, 稳定); "
                           "TTM=过去365天滚动合计(会跨报告期, 末期息登记日附近偏高)")
    sel_key = options[labels.index(choice)][0]
    dividend = annual if sel_key == "annual" else ttm
    div_components = annual_components if sel_key == "annual" else components

    # 国债收益率: 手动覆盖优先, 否则自动(失败回落默认)
    if rf_override.strip():
        rf_pct, rf_auto = to_decimal(rf_override), False
        rf_note = "手动输入"
    else:
        rf_pct, rf_auto = fetch_treasury_yield_pct()
        rf_note = "自动获取" if rf_auto else f"默认值(自动源不可用), 当前 {DEFAULT_TREASURY_YIELD_PCT}%"

    rf = rf_pct / _HUNDRED
    current_yield = dividend / price

    # ---------- 指标卡 ----------
    st.subheader(f"{name}（{code}）")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("最新价", f"{price.quantize(_Q2)}", help=f"收盘日 {snap['price_date']}")
    m2.metric("采用股息(税前)", f"{dividend.quantize(_Q4).normalize()}",
              help=f"口径: {'完整年报年度' if sel_key == 'annual' else 'TTM 近12月'}")
    m3.metric("当前股息率", f"{_pct(current_yield)}%")
    m4.metric("十年期国债", f"{rf_pct.quantize(_Q2)}%", help=rf_note)

    ratio_now = (current_yield / rf).quantize(_Q2) if rf > 0 else "∞"
    st.caption(f"当前性价比(股息率 ÷ 无风险利率) = **{ratio_now}x**　|　国债来源: {rf_note}"
               f"　|　另一口径: {ttm_label if sel_key == 'annual' else annual_label}")

    # 分红明细
    with st.expander(f"采用口径股息构成({len(div_components)} 笔, 求和 = {dividend.normalize()})",
                     expanded=True):
        df_comp = pd.DataFrame(
            [{"股权登记日": d, "每股税前(元)": v} for d, v in div_components]
        )
        st.dataframe(df_comp, hide_index=True, use_container_width=True)

    # ---------- 网格 ----------
    try:
        levels = build_grid(
            dividend=dividend, current_price=price, risk_free_rate=rf,
            step=to_decimal(step_pct) / _HUNDRED,
            low_yield=to_decimal(low_pct) / _HUNDRED,
            high_yield=to_decimal(high_pct) / _HUNDRED,
        )
    except ValueError as e:
        st.error(f"网格参数错误: {e}")
        return

    df = _build_dataframe(levels)

    def _highlight(row):
        if row["动作"].startswith("▶"):
            return ["background-color: #fff3cd"] * len(row)
        if "减仓" in row["动作"]:
            return ["background-color: #f8d7da"] * len(row)
        if "加仓" in row["动作"]:
            return ["background-color: #d1e7dd"] * len(row)
        return [""] * len(row)

    st.dataframe(df.style.apply(_highlight, axis=1),
                 hide_index=True, use_container_width=True)

    # ---------- 下载 ----------
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("下载 CSV", csv, file_name=f"dividend_grid_{code}.csv",
                       mime="text/csv", use_container_width=True)

    st.caption("⚠️ 数据自动获取(baostock), 可能延迟或错漏; 股息为税前口径; "
               "本工具仅供参考, 不构成投资建议。")


if __name__ == "__main__":
    main()
