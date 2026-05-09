"""统一行情数据获取模块

数据源：
- A股行情 → baostock（默认前复权）
- 港股行情 → FutuOpenD（默认前复权）
- 指数行情/行业分类/指数成分股 → baostock

复权说明：
- 默认使用前复权（qfq），适用于量化回测、技术分析、收益计算
- 可通过 adjust 参数指定：qfq（前复权）/ hfq（后复权）/ ""（不复权）
"""

import sys
import os
import json
import atexit
import time
import queue
import threading
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import baostock as bs
import requests
from risk_control.agent_price_cache import read_cached_series as read_agent_price_series


sys.path.append(str(Path(__file__).parent.parent))
from shared.config import CACHE_DIR, CACHE_EXPIRY_DAYS, SECTOR_CACHE_DAYS, FUTU_HOST, FUTU_PORT, TS_TOKEN

# ============================================================
# baostock 生命周期
# ============================================================
_bs_logged_in = False
_bs_unavailable = False  # 标记 baostock 不可用，避免重复超时

# baostock 调用超时（秒）— 影响 connect + recv
_BS_TIMEOUT = int(os.getenv("BS_TIMEOUT", "30"))
_BS_LOGIN_RETRIES = int(os.getenv("BS_LOGIN_RETRIES", "3"))

# ============================================================
# 数据降级追踪
# ============================================================
_data_degradations = []  # [(source, scope, reason)]
_ALLOW_EXTERNAL_MARKET_DATA = True


def _record_degradation(source: str, scope: str, reason: str = ""):
    """记录数据降级事件"""
    entry = (source, scope, reason)
    if entry not in _data_degradations:
        _data_degradations.append(entry)


def get_data_degradations() -> list:
    """获取本次会话的数据降级记录，供报告使用"""
    return list(_data_degradations)


def clear_data_degradations():
    """清空降级记录（每次生成报告前调用）"""
    _data_degradations.clear()


@contextmanager
def local_market_data_only():
    """禁止外部行情刷新，只读取本地 agent/stocks/benchmark cache。"""
    global _ALLOW_EXTERNAL_MARKET_DATA
    previous = _ALLOW_EXTERNAL_MARKET_DATA
    _ALLOW_EXTERNAL_MARKET_DATA = False
    try:
        yield
    finally:
        _ALLOW_EXTERNAL_MARKET_DATA = previous


def latest_baostock_available_date(now=None) -> str:
    """返回 baostock 默认已入库的最近 A 股交易日（YYYYMMDD）。

    baostock 当日行情通常 17:30 后入库；周末默认回退到上一个工作日。
    这里不内置节假日日历，遇到节假日时 baostock 查询会自然返回最近可用历史数据。
    """
    current = now or datetime.now()
    cutoff_passed = (current.hour, current.minute) >= (17, 30)
    candidate = current
    if current.weekday() >= 5 or not cutoff_passed:
        candidate = current - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate = candidate - timedelta(days=1)
    return candidate.strftime("%Y%m%d")


def _ensure_bs_login():
    global _bs_logged_in, _bs_unavailable
    if _bs_unavailable:
        raise RuntimeError("baostock 不可用（本次会话已标记跳过）")
    if not _bs_logged_in:
        import socket as _socket
        # 设置全局 socket 超时，让 baostock 的 connect/recv 不会无限阻塞
        prev_timeout = _socket.getdefaulttimeout()
        _socket.setdefaulttimeout(_BS_TIMEOUT)
        last_error = None
        try:
            for attempt in range(1, max(_BS_LOGIN_RETRIES, 1) + 1):
                try:
                    result = bs.login()
                    if result.error_code == '0':
                        break
                    last_error = RuntimeError(f"baostock login 失败: {result.error_msg}")
                except Exception as e:
                    last_error = e
                if attempt < max(_BS_LOGIN_RETRIES, 1):
                    time.sleep(1.0 * attempt)
            else:
                result = None

            if result is None or result.error_code != '0':
                msg = str(last_error) if last_error else "未知错误"
                _bs_unavailable = True
                _record_degradation("baostock", "全部", f"login 失败: {msg}")
                raise RuntimeError(f"baostock login 失败: {msg}")
        except RuntimeError:
            raise
        except Exception as e:
            _bs_unavailable = True
            _record_degradation("baostock", "全部", f"连接超时 ({_BS_TIMEOUT}s)")
            raise RuntimeError(f"baostock login 异常 (timeout={_BS_TIMEOUT}s): {e}") from e
        finally:
            _socket.setdefaulttimeout(prev_timeout)

        # login 成功后，给已创建的 socket 也设置超时
        try:
            import baostock.common.context as bs_ctx
            sock = getattr(bs_ctx, "default_socket", None)
            if sock is not None:
                sock.settimeout(_BS_TIMEOUT)
        except Exception:
            pass
        _bs_logged_in = True
        atexit.register(bs.logout)


def _bs_logout():
    global _bs_logged_in
    if not _bs_logged_in:
        return
    try:
        bs.logout()
    finally:
        _bs_logged_in = False


@contextmanager
def baostock_session():
    """显式管理 baostock 会话，适合批量查询后立即登出。"""
    _ensure_bs_login()
    try:
        yield
    finally:
        _bs_logout()


# ============================================================
# 内部工具函数
# ============================================================


def _run_with_timeout(fn, timeout=None):
    """在 daemon 线程中执行 fn()，超时后主线程立即返回。

    不能使用 ThreadPoolExecutor 的 context manager：baostock 卡在 socket recv
    时，executor shutdown 会等待工作线程结束，导致“软超时”仍然卡住主流程。
    """
    global _bs_unavailable
    if timeout is None:
        timeout = _BS_TIMEOUT

    result_queue = queue.Queue(maxsize=1)

    def _target():
        try:
            result_queue.put(("ok", fn()), block=False)
        except Exception as e:
            result_queue.put(("error", e), block=False)

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        _bs_unavailable = True
        _record_degradation("baostock", "全部", f"调用硬超时 ({timeout}s)")
        raise TimeoutError(f"baostock 调用硬超时 ({timeout}s)")

    status, payload = result_queue.get_nowait()
    if status == "error":
        raise payload
    return payload


def _to_bs_code(code_str):
    """A股代码 → baostock 格式 (sh.600519 / sz.000001)"""
    code_str = code_str.zfill(6)
    if code_str[0] in ('6', '9'):
        return f"sh.{code_str}"
    # 5开头的 ETF 属于上海交易所 (510xxx, 512xxx, 513xxx, 515xxx, 516xxx, 518xxx, 520xxx, 560xxx, 561xxx, 562xxx, 563xxx)
    if code_str[0] == '5':
        return f"sh.{code_str}"
    return f"sz.{code_str}"


def _to_bs_date(date_str):
    """'20250101' → '2025-01-01'"""
    d = str(date_str).replace("-", "")
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}"


def _is_hk(code_str):
    """5位纯数字 → 港股"""
    return len(code_str) == 5 and code_str.isdigit()


def _cache_valid(cache_file, expiry_days):
    """检查缓存文件是否有效"""
    if not cache_file.exists():
        return False
    cache_time = datetime.fromtimestamp(cache_file.stat().st_mtime)
    return datetime.now() - cache_time < timedelta(days=expiry_days)


def _read_cached_frame(cache_file):
    """读取缓存文件。"""
    return pd.read_csv(cache_file, parse_dates=['date'])


def _load_latest_matching_cache(pattern, subdir=None):
    """按修改时间回退到最近一次可用缓存。"""
    search_dir = Path(CACHE_DIR) / subdir if subdir else Path(CACHE_DIR)
    matches = sorted(search_dir.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    for cache_file in matches:
        try:
            df = _read_cached_frame(cache_file)
            if df is not None and not df.empty:
                return df, cache_file
        except Exception:
            continue
    return None, None


def _benchmark_cache_dir() -> Path:
    path = Path(CACHE_DIR) / "benchmarks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _benchmark_series_key(benchmark_index: str) -> str:
    return str(benchmark_index).replace(".", "_")


def _benchmark_series_path(benchmark_index: str) -> Path:
    return _benchmark_cache_dir() / f"{_benchmark_series_key(benchmark_index)}.csv"


def _empty_benchmark_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount"])


def _normalize_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return _empty_benchmark_df()

    normalized = df.copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume", "amount"]:
        if col in normalized.columns:
            normalized[col] = pd.to_numeric(normalized[col], errors="coerce")
        else:
            normalized[col] = pd.NA

    normalized = normalized.dropna(subset=["date", "close"])
    normalized = normalized[["date", "open", "high", "low", "close", "volume", "amount"]]
    normalized = normalized.drop_duplicates(subset=["date"], keep="last")
    normalized = normalized.sort_values("date").reset_index(drop=True)
    return normalized


def _load_benchmark_series(benchmark_index: str) -> pd.DataFrame:
    path = _benchmark_series_path(benchmark_index)
    if not path.exists():
        return _empty_benchmark_df()
    return _normalize_ohlcv_frame(pd.read_csv(path, parse_dates=["date"]))


def _save_benchmark_series(df: pd.DataFrame, benchmark_index: str):
    normalized = _normalize_ohlcv_frame(df)
    if normalized.empty:
        return
    normalized.to_csv(_benchmark_series_path(benchmark_index), index=False)


def _merge_benchmark_series(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if existing is None or existing.empty:
        return _normalize_ohlcv_frame(incoming)
    if incoming is None or incoming.empty:
        return _normalize_ohlcv_frame(existing)
    combined = pd.concat([existing, incoming], ignore_index=True)
    return _normalize_ohlcv_frame(combined)


def _seed_benchmark_series_from_legacy_cache(benchmark_index: str) -> pd.DataFrame:
    existing = _load_benchmark_series(benchmark_index)
    legacy_pattern = f"benchmark_{_benchmark_series_key(benchmark_index)}_*.csv"
    legacy_files = sorted(Path(CACHE_DIR).glob(legacy_pattern), key=lambda path: path.stat().st_mtime)
    merged = existing
    for cache_file in legacy_files:
        try:
            frame = _normalize_ohlcv_frame(pd.read_csv(cache_file, parse_dates=["date"]))
        except Exception:
            continue
        if not frame.empty:
            merged = _merge_benchmark_series(merged, frame)

    if not merged.empty and (existing.empty or len(merged) != len(existing)):
        _save_benchmark_series(merged, benchmark_index)

    return merged


def _slice_benchmark_series(df: pd.DataFrame, start_date: str, end_date: str) -> pd.DataFrame:
    if df is None or df.empty:
        return _empty_benchmark_df()
    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date)
    sliced = df[(df["date"] >= start_ts) & (df["date"] <= end_ts)].reset_index(drop=True)
    return _normalize_ohlcv_frame(sliced)


def _find_missing_benchmark_dates(df: pd.DataFrame, start_date: str, end_date: str) -> list[str]:
    """找出请求区间内缺失的工作日，用于单日补数。"""
    if df is None or df.empty:
        return []

    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date)
    actual_dates = {ts.normalize() for ts in pd.to_datetime(df["date"])}
    expected_dates = pd.bdate_range(start=start_ts, end=end_ts)
    missing = [ts.strftime("%Y%m%d") for ts in expected_dates if ts.normalize() not in actual_dates]
    return missing


# ============================================================
# ETF 行业分类（从 brinson.py 迁移）
# ============================================================

ETF_SECTOR_MAP = {
    "券商": "非银金融", "证券": "非银金融", "保险": "非银金融", "金融": "非银金融",
    "银行": "银行",
    "医药": "医药生物", "医疗": "医药生物", "生物": "医药生物", "创新药": "医药生物",
    "白酒": "食品饮料", "食品": "食品饮料", "消费": "食品饮料",
    "军工": "国防军工", "国防": "国防军工",
    "新能源": "电力设备", "光伏": "电力设备", "锂电": "电力设备", "储能": "电力设备",
    "电力": "公用事业",
    "芯片": "电子", "半导体": "电子", "电子": "电子",
    "计算机": "计算机", "软件": "计算机", "信息技术": "计算机", "云计算": "计算机",
    "互联网": "传媒", "传媒": "传媒", "游戏": "传媒",
    "通信": "通信", "5G": "通信",
    "地产": "房地产", "房地产": "房地产",
    "建筑": "建筑装饰", "建材": "建筑装饰",
    "钢铁": "钢铁",
    "煤炭": "煤炭",
    "有色": "有色金属", "稀土": "有色金属",
    "化工": "基础化工",
    "汽车": "汽车", "新能源车": "汽车",
    "家电": "家用电器",
    "农业": "农林牧渔", "养殖": "农林牧渔", "猪": "农林牧渔",
    "机械": "机械设备", "机器人": "机械设备",
    "交通": "交通运输", "物流": "交通运输", "航运": "交通运输",
    "纺织": "纺织服饰",
    "商贸": "商贸零售",
    "环保": "环保",
    "石油": "石油石化", "石化": "石油石化",
    "美容": "美容护理",
}

BROAD_ETF_KEYWORDS = [
    "沪深300", "中证500", "中证1000", "上证50", "创业板", "科创",
    "红利", "价值", "成长", "MSCI", "恒生", "纳斯达克", "标普",
]


def _classify_etf(name):
    """根据 ETF 名称推断行业"""
    if not name:
        return "指数"
    for kw in BROAD_ETF_KEYWORDS:
        if kw in name:
            return "指数"
    for kw, sector in ETF_SECTOR_MAP.items():
        if kw in name:
            return sector
    return "指数"


# ============================================================
# 多数据源架构
# ============================================================

_EMPTY_PRICE_DF = pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])


def _normalize_price_adjust(adjust):
    text = str(adjust or "raw").strip().lower()
    if text in ("", "none", "raw", "no_adjust", "unadjusted", "3"):
        return "raw"
    if text in ("qfq", "forward", "forward_adjusted", "2"):
        return "qfq"
    if text in ("hfq", "backward", "backward_adjusted", "1"):
        return "hfq"
    return text


def _agent_price_cache_dir() -> Path:
    path = Path(CACHE_DIR) / "agent_prices"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_agent_price_rows(rows, require_ohlcv=True, adjust=None):
    if not rows:
        return _EMPTY_PRICE_DF.copy()
    df = pd.DataFrame(rows)
    if "date" not in df.columns or "close" not in df.columns:
        return _EMPTY_PRICE_DF.copy()
    df = df.copy()
    if adjust is not None:
        wanted_adjust = _normalize_price_adjust(adjust)
        if "adjust" not in df.columns:
            df["adjust"] = "qfq"
        df["adjust"] = df["adjust"].map(_normalize_price_adjust)
        df = df[df["adjust"] == wanted_adjust]
        if df.empty:
            return _EMPTY_PRICE_DF.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    if require_ohlcv:
        for col in ["open", "high", "low"]:
            if col not in df.columns:
                df[col] = df["close"]
            df[col] = pd.to_numeric(df[col], errors="coerce")
        if "volume" not in df.columns:
            df["volume"] = 0
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    else:
        for col in ["open", "high", "low"]:
            if col not in df.columns:
                df[col] = df["close"]
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(df["close"])
        if "volume" not in df.columns:
            df["volume"] = 0
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)
    df = df.dropna(subset=["date", "close"]).sort_values("date")
    cols = ["date", "open", "high", "low", "close", "volume"]
    return df[cols].drop_duplicates(subset=["date"]).reset_index(drop=True)


def _load_agent_cached_series(code, start_date, end_date, section="prices", require_ohlcv=True, adjust="qfq"):
    cached_adjust = adjust if section == "prices" else None
    cached = read_agent_price_series(section, code, start_date, end_date, adjust=cached_adjust)
    if cached is not None and not cached.empty:
        return _normalize_agent_price_rows(
            cached.to_dict(orient="records"),
            require_ohlcv=require_ohlcv,
            adjust=cached_adjust,
        )

    cache_dir = _agent_price_cache_dir()
    frames = []
    code_str = str(code).strip()
    for path in sorted(cache_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = payload.get(section, {}).get(code_str, [])
        if not rows and not code_str.startswith("0"):
            rows = payload.get(section, {}).get(code_str.zfill(6), [])
        if rows:
            df = _normalize_agent_price_rows(rows, require_ohlcv=require_ohlcv, adjust=cached_adjust)
            if not df.empty:
                frames.append(df)
    if not frames:
        return _EMPTY_PRICE_DF.copy()
    merged = pd.concat(frames, ignore_index=True)
    merged = merged.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)
    sd = pd.to_datetime(start_date)
    ed = pd.to_datetime(end_date)
    sliced = merged[(merged["date"] >= sd) & (merged["date"] <= ed)].reset_index(drop=True)
    return sliced


def _agent_cache_covers_request(df, start_date, end_date, min_coverage=1.0):
    if df is None or df.empty:
        return False
    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date)
    latest = df["date"].max()
    if latest < end_ts:
        return False
    expected_days = max(len(pd.bdate_range(start=start_ts, end=end_ts)), 1)
    return len(df) >= max(1, int(expected_days * min_coverage))


# 数据源注册表：market → [(name, fetcher_fn), ...]
# 按优先级排列，第一个成功即返回
_SOURCE_REGISTRY = {
    'a_stock': [
        ('baostock', '_fetch_a_stock_prices'),
    ],
    'etf': [
        ('baostock', '_fetch_a_stock_prices'),
    ],
    'hk_stock': [
        ('futu', '_fetch_hk_futu'),
    ],
}


def _fetch_with_fallback(sources, code_str, start_date, end_date, adjust="qfq"):
    """按优先级尝试多个数据源，第一个成功即返回"""
    last_error = None
    for name, fn_name in sources:
        try:
            fn = globals()[fn_name]
            df = fn(code_str, start_date, end_date, adjust)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            last_error = e
            print(f"  数据源 {name} 获取 {code_str} 失败: {e}")
            continue
    if last_error:
        raise last_error
    return _EMPTY_PRICE_DF.copy()


# ============================================================
# 公开 API：股票行情
# ============================================================

def get_stock_prices(code, start_date, end_date, adjust="qfq"):
    """获取股票历史行情（带缓存、多数据源 fallback）

    A股/ETF → baostock, 港股 → FutuOpenD
    返回 DataFrame[date, open, close, high, low, volume]

    Args:
        code: 股票代码
        start_date: 开始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        adjust: 复权方式
                - "qfq" 前复权（默认，推荐用于量化回测）
                - "hfq" 后复权
                - "" 不复权（实盘下单用）
    """
    code_str = str(code).strip()
    is_hk = _is_hk(code_str)
    if not is_hk:
        code_str = code_str.zfill(6)

    agent_df = _load_agent_cached_series(
        code_str,
        start_date,
        end_date,
        section="prices",
        require_ohlcv=True,
        adjust=adjust,
    )
    if _agent_cache_covers_request(agent_df, start_date, end_date, min_coverage=1.0):
        return agent_df

    # 缓存文件名包含复权方式，存放在 stocks/ 子目录
    adjust_suffix = adjust if adjust else "raw"
    stocks_cache_dir = Path(CACHE_DIR) / "stocks"
    stocks_cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = stocks_cache_dir / f"{code_str}_{start_date}_{end_date}_{adjust_suffix}.csv"

    if _cache_valid(cache_file, CACHE_EXPIRY_DAYS):
        cached_df = _read_cached_frame(cache_file)
        if not cached_df.empty:
            return cached_df

    try:
        if not _ALLOW_EXTERNAL_MARKET_DATA:
            raise RuntimeError("外部行情刷新已禁用")

        # ETF 检测（深交所 15/16 开头，或沪市 5 开头带 ETF 关键字时走 ETF 路径）
        is_etf = code_str[:2] in ("15", "16") or (code_str[0] == "5" and len(code_str) == 6)
        market = 'hk_stock' if is_hk else ('etf' if is_etf else 'a_stock')
        sources = _SOURCE_REGISTRY[market]
        df = _fetch_with_fallback(sources, code_str, start_date, end_date, adjust)
        if df is None or df.empty:
            raise RuntimeError(f"获取 {code_str} 行情失败")

        df['date'] = pd.to_datetime(df['date'])
        df.to_csv(cache_file, index=False)
        return df
    except Exception as e:
        if cache_file.exists():
            cached_df = _read_cached_frame(cache_file)
            if not cached_df.empty:
                _record_degradation("stock_prices", code_str, "回退到缓存")
                print(f"警告: 获取 {code_str} 实时行情失败，回退到已有缓存: {cache_file.name}")
                return cached_df

        fallback_df, fallback_file = _load_latest_matching_cache(f"{code_str}_*_*_{adjust_suffix}.csv", subdir="stocks")
        if fallback_df is None:
            fallback_df, fallback_file = _load_latest_matching_cache(f"{code_str}_*_*_*.csv", subdir="stocks")
        if fallback_df is not None:
            _record_degradation("stock_prices", code_str, "回退到历史缓存")
            print(f"警告: 获取 {code_str} 实时行情失败，回退到最近缓存: {fallback_file.name}")
            return fallback_df

        _record_degradation("stock_prices", code_str, "无数据")
        print(f"警告: 获取 {code_str} 行情失败（所有数据源）: {e}")
        return _EMPTY_PRICE_DF.copy()


def _fetch_a_stock_prices(code_str, start_date, end_date, adjust="qfq"):
    """baostock 获取 A 股行情

    Args:
        adjust: 复权方式
                - "qfq" 前复权（adjustflag="2"）
                - "hfq" 后复权（adjustflag="1"）
                - "" 不复权（adjustflag="3"）
    """
    _ensure_bs_login()
    bs_code = _to_bs_code(code_str)

    # 转换复权参数
    adjust_map = {
        "qfq": "2",  # 前复权
        "hfq": "1",  # 后复权
        "": "3",     # 不复权
    }
    adjustflag = adjust_map.get(adjust, "2")  # 默认前复权

    def _query():
        rs = bs.query_history_k_data_plus(
            bs_code,
            "date,open,high,low,close,volume",
            start_date=_to_bs_date(start_date),
            end_date=_to_bs_date(end_date),
            frequency="d",
            adjustflag=adjustflag,
        )
        if rs.error_code != '0':
            raise RuntimeError(f"baostock query 失败: {rs.error_msg}")
        rows = []
        while (rs.error_code == '0') and rs.next():
            rows.append(rs.get_row_data())
        return rows

    rows = _run_with_timeout(_query)
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    df = df.dropna(subset=["close"])
    df['date'] = pd.to_datetime(df['date'])
    return df.sort_values('date').reset_index(drop=True)


def _fetch_hk_futu(code_str, start_date, end_date, adjust="qfq"):
    """FutuOpenD 获取港股行情

    Args:
        adjust: 复权方式
                - "qfq" 前复权（AuType.QFQ）
                - "hfq" 后复权（AuType.HFQ）
                - "" 不复权（AuType.NONE）
    """
    from futu import OpenQuoteContext, KLType, AuType

    # 转换复权参数
    adjust_map = {
        "qfq": AuType.QFQ,   # 前复权
        "hfq": AuType.HFQ,   # 后复权
        "": AuType.NONE,     # 不复权
    }
    autype = adjust_map.get(adjust, AuType.QFQ)  # 默认前复权

    ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    try:
        ret, df, _ = ctx.request_history_kline(
            f'HK.{code_str}',
            ktype=KLType.K_DAY,
            autype=autype,
            start=_to_bs_date(start_date),
            end=_to_bs_date(end_date),
        )
        if ret != 0 or df is None or df.empty:
            return _EMPTY_PRICE_DF.copy()

        df = df.rename(columns={'time_key': 'date'})
        df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
        df['date'] = pd.to_datetime(df['date'])
        return df.sort_values('date').reset_index(drop=True)
    finally:
        ctx.close()


def _fetch_hk_index_futu(futu_code, start_date, end_date):
    """FutuOpenD 获取港股指数行情（如 HK.800000 恒生指数）

    Args:
        futu_code: 完整 Futu 代码（如 'HK.800000'）
        start_date: 开始日期 (YYYYMMDD)
        end_date: 结束日期 (YYYYMMDD)

    Returns:
        DataFrame[date, open, high, low, close, volume]
    """
    cache_file = Path(CACHE_DIR) / f"benchmark_{futu_code.replace('.', '_')}_{start_date}_{end_date}.csv"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    series_df = _seed_benchmark_series_from_legacy_cache(futu_code)
    sliced = _slice_benchmark_series(series_df, start_date, end_date)
    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date)
    today_ts = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
    required_end = min(end_ts, today_ts)

    if not sliced.empty and sliced["date"].min() <= start_ts and sliced["date"].max() >= required_end:
        return sliced

    if _cache_valid(cache_file, CACHE_EXPIRY_DAYS):
        cached_df = _read_cached_frame(cache_file)
        if not cached_df.empty:
            series_df = _merge_benchmark_series(series_df, cached_df)
            _save_benchmark_series(series_df, futu_code)
            return _slice_benchmark_series(series_df, start_date, end_date)

    from futu import OpenQuoteContext, KLType, AuType

    ctx = OpenQuoteContext(host=FUTU_HOST, port=FUTU_PORT)
    try:
        ret, df, _ = ctx.request_history_kline(
            futu_code,
            ktype=KLType.K_DAY,
            autype=AuType.NONE,
            start=_to_bs_date(start_date),
            end=_to_bs_date(end_date),
        )
        if ret != 0 or df is None or df.empty:
            print(f"错误: FutuOpenD 获取 {futu_code} 失败 (ret={ret})")
            return _EMPTY_PRICE_DF.copy()

        df = df.rename(columns={'time_key': 'date'})
        df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
        df['date'] = pd.to_datetime(df['date'])
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['close']).sort_values('date').reset_index(drop=True)

        df.to_csv(cache_file, index=False)
        series_df = _merge_benchmark_series(series_df, df)
        _save_benchmark_series(series_df, futu_code)
        return _slice_benchmark_series(series_df, start_date, end_date)
    finally:
        ctx.close()

# ============================================================
# 公开 API：基准指数行情
# ============================================================

def get_benchmark_prices(benchmark_index, start_date, end_date):
    """获取基准指数行情（优先持久化时序缓存，不足时再补数）

    返回 DataFrame[date, open, close, high, low, volume]
    """
    agent_df = _load_agent_cached_series(str(benchmark_index), start_date, end_date, section="indices", require_ohlcv=False)
    if _agent_cache_covers_request(agent_df, start_date, end_date, min_coverage=0.7):
        return agent_df

    series_df = _seed_benchmark_series_from_legacy_cache(benchmark_index)
    sliced = _slice_benchmark_series(series_df, start_date, end_date)
    start_ts = pd.to_datetime(start_date)
    end_ts = pd.to_datetime(end_date)
    today_ts = pd.to_datetime(datetime.now().strftime("%Y-%m-%d"))
    required_end = min(end_ts, today_ts)

    if not sliced.empty and sliced["date"].min() <= start_ts and sliced["date"].max() >= required_end:
        return sliced

    cache_file = Path(CACHE_DIR) / f"benchmark_{benchmark_index}_{start_date}_{end_date}.csv"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if _cache_valid(cache_file, CACHE_EXPIRY_DAYS):
        cached_df = _read_cached_frame(cache_file)
        if not cached_df.empty:
            series_df = _merge_benchmark_series(series_df, cached_df)
            _save_benchmark_series(series_df, benchmark_index)
            sliced = _slice_benchmark_series(series_df, start_date, end_date)
            missing_dates = _find_missing_benchmark_dates(sliced, start_date, end_date)
            if not missing_dates:
                return sliced

    sliced = _slice_benchmark_series(series_df, start_date, end_date)
    missing_dates = _find_missing_benchmark_dates(sliced, start_date, end_date)
    if not _ALLOW_EXTERNAL_MARKET_DATA:
        if not sliced.empty:
            return sliced
        raise RuntimeError(f"本地缓存缺少基准指数 {benchmark_index} 数据")

    if missing_dates and len(missing_dates) <= 10:
        for missing_date in missing_dates:
            try:
                day_df = _empty_benchmark_df()
                _ensure_bs_login()

                def _query_day(bm_idx=benchmark_index, md=missing_date):
                    for prefix in ['sh', 'sz']:
                        bs_code = f"{prefix}.{bm_idx}"
                        rs = bs.query_history_k_data_plus(
                            bs_code,
                            "date,open,high,low,close,volume,amount",
                            start_date=_to_bs_date(md),
                            end_date=_to_bs_date(md),
                            frequency="d",
                        )
                        rows = []
                        while (rs.error_code == '0') and rs.next():
                            rows.append(rs.get_row_data())
                        if rows:
                            return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
                    return _empty_benchmark_df()

                day_df = _run_with_timeout(_query_day, timeout=15)
                day_df = _normalize_ohlcv_frame(day_df)
                if not day_df.empty:
                    series_df = _merge_benchmark_series(series_df, day_df)
            except Exception:
                continue

        if not series_df.empty:
            _save_benchmark_series(series_df, benchmark_index)
            sliced = _slice_benchmark_series(series_df, start_date, end_date)
            if not _find_missing_benchmark_dates(sliced, start_date, end_date):
                return sliced

    try:
        _ensure_bs_login()

        # 尝试 sh/sz 两个前缀
        def _query_range(bm_idx=benchmark_index, sd=start_date, ed=end_date):
            for prefix in ['sh', 'sz']:
                bs_code = f"{prefix}.{bm_idx}"
                rs = bs.query_history_k_data_plus(
                    bs_code,
                    "date,open,high,low,close,volume,amount",
                    start_date=_to_bs_date(sd),
                    end_date=_to_bs_date(ed),
                    frequency="d",
                )
                if rs.error_code != '0':
                    continue
                rows = []
                while (rs.error_code == '0') and rs.next():
                    rows.append(rs.get_row_data())
                if rows:
                    return pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
            return None

        df = _run_with_timeout(_query_range)

        if df is None or df.empty:
            raise RuntimeError(f"获取基准指数 {benchmark_index} 失败")

        for col in ["open", "high", "low", "close", "volume", "amount"]:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df['date'] = pd.to_datetime(df['date'])
        df = df.dropna(subset=["close"])

        if df.empty:
            raise RuntimeError(f"指数 {benchmark_index} 在 {start_date}~{end_date} 无数据")

        df.to_csv(cache_file, index=False)
        series_df = _merge_benchmark_series(series_df, df)
        _save_benchmark_series(series_df, benchmark_index)
        return _slice_benchmark_series(series_df, start_date, end_date)
    except Exception:
        if cache_file.exists():
            cached_df = _read_cached_frame(cache_file)
            if not cached_df.empty:
                _record_degradation("benchmark", benchmark_index, "回退到缓存")
                print(f"警告: 获取基准 {benchmark_index} 实时行情失败，回退到已有缓存: {cache_file.name}")
                series_df = _merge_benchmark_series(series_df, cached_df)
                _save_benchmark_series(series_df, benchmark_index)
                return cached_df

        series_df = _load_benchmark_series(benchmark_index)
        sliced = _slice_benchmark_series(series_df, start_date, end_date)
        if not sliced.empty:
            _record_degradation("benchmark", benchmark_index, "回退到本地时序缓存")
            print(f"警告: 获取基准 {benchmark_index} 实时行情失败，回退到本地时序缓存")
            return sliced

        fallback_df, fallback_file = _load_latest_matching_cache(f"benchmark_{benchmark_index}_*.csv")
        if fallback_df is not None:
            _record_degradation("benchmark", benchmark_index, "回退到历史缓存")
            print(f"警告: 获取基准 {benchmark_index} 实时行情失败，回退到最近缓存: {fallback_file.name}")
            series_df = _merge_benchmark_series(series_df, fallback_df)
            _save_benchmark_series(series_df, benchmark_index)
            return fallback_df
        raise


def get_composite_benchmark_prices(benchmark_components, start_date, end_date):
    """获取复合基准的合成价格序列

    逐个获取各成分指数价格，对齐交易日历后加权合成。

    Args:
        benchmark_components: parse_benchmark_config() 返回的列表
        start_date: 开始日期 (YYYYMMDD)
        end_date: 结束日期 (YYYYMMDD)

    Returns:
        DataFrame[date, close]（归一化合成价格，与 get_benchmark_prices 兼容）
    """
    import numpy as np

    price_series = {}  # {index: Series(date→close)}

    for comp in benchmark_components:
        idx = comp["index"]
        source = comp["source"]

        if source == "futu":
            df = _fetch_hk_index_futu(idx, start_date, end_date)
        else:
            df = get_benchmark_prices(idx, start_date, end_date)

        if df is None or df.empty:
            print(f"错误: 无法获取成分指数 {idx} 的数据")
            sys.exit(1)

        s = df.set_index('date')['close'].astype(float)
        price_series[idx] = s

    # 对齐交易日历：取并集 + forward-fill 处理 A/H 不同假期
    all_dates = sorted(set().union(*(s.index for s in price_series.values())))
    aligned = pd.DataFrame(index=pd.DatetimeIndex(all_dates))

    for idx, s in price_series.items():
        aligned[idx] = s
    aligned = aligned.ffill().bfill()

    # 归一化到 1.0 后加权合成
    composite = pd.Series(0.0, index=aligned.index)
    for comp in benchmark_components:
        idx = comp["index"]
        weight = comp["weight"]
        normalized = aligned[idx] / aligned[idx].iloc[0]
        composite += weight * normalized

    # 转换为与 get_benchmark_prices 兼容的 DataFrame 格式
    result = pd.DataFrame({
        'date': composite.index,
        'close': composite.values,
    }).reset_index(drop=True)

    # 用第一个成分的首日 close 作为基数，使合成价格有实际量纲
    first_idx = benchmark_components[0]["index"]
    base_price = float(price_series[first_idx].iloc[0])
    result['close'] = result['close'] * base_price

    return result

# 国标行业（baostock）→ 申万一级行业映射
_GB_TO_SW = {
    # 农林牧渔
    "A01农业": "农林牧渔", "A02林业": "农林牧渔", "A03牧业": "农林牧渔",
    "A04渔业": "农林牧渔", "A05农、林、牧、渔服务业": "农林牧渔",
    # 基础化工
    "C25石油加工、炼焦和核燃料加工业": "基础化工",
    "C26化学原料和化学制品制造业": "基础化工",
    "C28化学纤维制造业": "基础化工",
    "C29橡胶和塑料制品业": "基础化工",
    "C30非金属矿物制品业": "建筑材料",
    # 钢铁
    "C31黑色金属冶炼和压延加工业": "钢铁",
    # 有色金属
    "C32有色金属冶炼和压延加工业": "有色金属",
    # 机械设备
    "C34通用设备制造业": "机械设备",
    "C35专用设备制造业": "机械设备",
    # 电力设备
    "C38电气机械和器材制造业": "电力设备",
    # 电子
    "C39计算机、通信和其他电子设备制造业": "电子",
    "C40仪器仪表制造业": "电子",
    # 汽车
    "C36汽车制造业": "汽车",
    "C37铁路、船舶、航空航天和其他运输设备制造业": "国防军工",
    # 家用电器
    "C33金属制品业": "家用电器",
    # 食品饮料
    "C13农副食品加工业": "食品饮料", "C14食品制造业": "食品饮料",
    "C15酒、饮料和精制茶制造业": "食品饮料",
    # 纺织服饰
    "C17纺织业": "纺织服饰", "C18纺织服装、服饰业": "纺织服饰",
    "C19皮革、毛皮、羽毛及其制品和制鞋业": "纺织服饰",
    # 轻工制造
    "C20木材加工和木、竹、藤、棕、草制品业": "轻工制造",
    "C21家具制造业": "轻工制造", "C22造纸和纸制品业": "轻工制造",
    "C23印刷和记录媒介复制业": "轻工制造",
    "C24文教、工美、体育和娱乐用品制造业": "轻工制造",
    # 医药生物
    "C27医药制造业": "医药生物",
    # 公用事业
    "D44电力、热力生产和供应业": "公用事业",
    "D45燃气生产和供应业": "公用事业",
    "D46水的生产和供应业": "公用事业",
    # 交通运输
    "G53铁路运输业": "交通运输", "G54道路运输业": "交通运输",
    "G55水上运输业": "交通运输", "G56航空运输业": "交通运输",
    "G57管道运输业": "交通运输", "G58装卸搬运和运输代理业": "交通运输",
    "G59仓储业": "交通运输", "G60邮政业": "交通运输",
    # 房地产
    "K70房地产业": "房地产",
    # 商贸零售
    "F51批发业": "商贸零售", "F52零售业": "商贸零售",
    # 社会服务
    "H61住宿业": "社会服务", "H62餐饮业": "社会服务",
    "O77生态保护和环境治理业": "环保",
    "N78公共设施管理业": "社会服务",
    "R86新闻和出版业": "传媒", "R87广播、电视、电影和影视录音制作业": "传媒",
    "R88文化艺术业": "传媒", "R89体育": "社会服务",
    "R90娱乐业": "传媒",
    # 银行
    "J66货币金融服务": "银行",
    # 非银金融
    "J67资本市场服务": "非银金融", "J68保险业": "非银金融",
    "J69其他金融业": "非银金融",
    # 计算机
    "I63电信、广播电视和卫星传输服务": "通信",
    "I64互联网和相关服务": "计算机",
    "I65软件和信息技术服务业": "计算机",
    # 建筑装饰
    "E47房屋建筑业": "建筑装饰", "E48土木工程建筑业": "建筑装饰",
    "E49建筑安装业": "建筑装饰", "E50建筑装饰和其他建筑业": "建筑装饰",
    # 煤炭
    "B06煤炭开采和洗选业": "煤炭",
    # 石油石化
    "B07石油和天然气开采业": "石油石化",
    "B08黑色金属矿采选业": "钢铁", "B09有色金属矿采选业": "有色金属",
    "B10非金属矿采选业": "建筑材料", "B11开采辅助活动": "石油石化",
    # 综合
    "S90综合": "综合",
    # 其他制造
    "C41其他制造业": "轻工制造",
    "C42废弃资源综合利用业": "环保",
    "C43金属制品、机械和设备修理业": "机械设备",
    # 美容护理
    "C16烟草制品业": "食品饮料",
}


def _map_gb_to_sw(gb_sector):
    """将国标行业分类映射到申万一级行业"""
    if not gb_sector or gb_sector == "其他":
        return "其他"

    # 精确匹配
    if gb_sector in _GB_TO_SW:
        return _GB_TO_SW[gb_sector]

    # 前缀匹配（baostock 返回的可能带或不带编号）
    for gb_key, sw_name in _GB_TO_SW.items():
        if gb_sector in gb_key or gb_key in gb_sector:
            return sw_name

    return "其他"


def get_stock_sector(code, name=""):
    """获取个股申万一级行业（带缓存）

    A股 → baostock industry → 映射到申万, 港股 → "境外", ETF → 名称推断
    """
    code_str = str(code).strip()

    if _is_hk(code_str):
        return "境外"

    code_str = code_str.zfill(6)

    # ETF 判断
    etf_prefixes = ("51", "15", "16", "56", "58", "59")
    if code_str[:2] in etf_prefixes:
        return _classify_etf(name)

    # 查缓存
    cache_file = Path(CACHE_DIR) / "sectors" / f"{code_str}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if _cache_valid(cache_file, SECTOR_CACHE_DAYS):
        data = json.loads(cache_file.read_text())
        sector = data.get("sector", "其他")
        # 如果缓存的是国标行业名，重新映射
        if any(c.isdigit() for c in sector[:3]):
            sector = _map_gb_to_sw(sector)
            data["sector"] = sector
            cache_file.write_text(json.dumps(data, ensure_ascii=False))
        return sector

    # baostock 获取行业（返回国标分类）
    try:
        _ensure_bs_login()

        def _query_industry(cs=code_str):
            rs = bs.query_stock_industry()
            gb = "其他"
            while (rs.error_code == '0') and rs.next():
                row = rs.get_row_data()
                # row: [updateDate, code, code_name, industry, industryClassification]
                if len(row) >= 4 and row[1].endswith(cs):
                    gb = row[3] if row[3] else "其他"
                    break
            return gb

        gb_sector = _run_with_timeout(_query_industry, timeout=20)

        sector = _map_gb_to_sw(gb_sector)
        cache_file.write_text(json.dumps({"sector": sector, "code": code_str, "gb_sector": gb_sector}, ensure_ascii=False))
        return sector
    except Exception as e:
        print(f"  警告: 获取 {code_str} 行业失败: {e}")
        return "其他"


# ============================================================
# 公开 API：申万行业指数收益率
# ============================================================

# 申万2021版一级行业 → Tushare ts_code
_SW_L1_TUSHARE = {
    "农林牧渔": "801010.SI", "基础化工": "801030.SI", "钢铁": "801040.SI",
    "有色金属": "801050.SI", "电子": "801080.SI", "家用电器": "801110.SI",
    "食品饮料": "801120.SI", "纺织服饰": "801130.SI", "轻工制造": "801140.SI",
    "医药生物": "801150.SI", "公用事业": "801160.SI", "交通运输": "801170.SI",
    "房地产": "801180.SI", "商贸零售": "801200.SI", "社会服务": "801210.SI",
    "综合": "801230.SI", "建筑材料": "801710.SI", "建筑装饰": "801720.SI",
    "电力设备": "801730.SI", "国防军工": "801740.SI", "计算机": "801750.SI",
    "传媒": "801760.SI", "通信": "801770.SI", "银行": "801780.SI",
    "非银金融": "801790.SI", "汽车": "801880.SI", "机械设备": "801890.SI",
    "煤炭": "801950.SI", "石油石化": "801960.SI", "环保": "801970.SI",
    "美容护理": "801980.SI",
}

# 申万一级行业 → 东方财富板块代码（fallback）
_SW_SECTOR_CODES_EM = {
    "农林牧渔": "BK0474", "基础化工": "BK0479", "钢铁": "BK0478",
    "有色金属": "BK0480", "电子": "BK0459", "汽车": "BK0481",
    "家用电器": "BK0465", "食品饮料": "BK0477", "纺织服饰": "BK0471",
    "轻工制造": "BK0469", "医药生物": "BK0465", "公用事业": "BK0458",
    "交通运输": "BK0456", "房地产": "BK0451", "商贸零售": "BK0467",
    "社会服务": "BK0468", "银行": "BK0475", "非银金融": "BK0473",
    "综合": "BK0485", "建筑材料": "BK0463", "建筑装饰": "BK0464",
    "电力设备": "BK0459", "国防军工": "BK0461", "计算机": "BK0460",
    "传媒": "BK0457", "通信": "BK0462", "煤炭": "BK0476",
    "石油石化": "BK0482", "环保": "BK0484", "美容护理": "BK0483",
    "机械设备": "BK0466",
}


def _get_sw_sector_returns_tushare(start_date, end_date):
    """Tushare sw_daily 获取申万一级行业收益率（主源）

    用 trade_date 批量拉取，只需 2 次 API 调用（起始日 + 结束日）
    用 close 自算收益率，不依赖接口 pct_change
    """
    import tushare as ts

    if not TS_TOKEN:
        raise ValueError("TS_TOKEN 未配置")

    ts.set_token(TS_TOKEN)
    pro = ts.pro_api()

    start_fmt = start_date.replace("-", "")
    end_fmt = end_date.replace("-", "")

    # 拉取起始日和结束日附近的全量行业数据
    # sw_daily(trade_date=xxx) 返回当天所有申万行业（含一二三级）
    df_start = pro.sw_daily(trade_date=start_fmt)
    df_end = pro.sw_daily(trade_date=end_fmt)

    # 如果精确日期没数据（非交易日），向前/后搜索最近交易日
    if df_start is None or df_start.empty:
        # 向后找 5 天
        from datetime import datetime, timedelta
        dt = datetime.strptime(start_fmt, '%Y%m%d')
        for i in range(1, 6):
            d = (dt + timedelta(days=i)).strftime('%Y%m%d')
            df_start = pro.sw_daily(trade_date=d)
            if df_start is not None and not df_start.empty:
                break

    if df_end is None or df_end.empty:
        # 向前找 5 天
        from datetime import datetime, timedelta
        dt = datetime.strptime(end_fmt, '%Y%m%d')
        for i in range(1, 6):
            d = (dt - timedelta(days=i)).strftime('%Y%m%d')
            df_end = pro.sw_daily(trade_date=d)
            if df_end is not None and not df_end.empty:
                break

    if df_start is None or df_start.empty or df_end is None or df_end.empty:
        raise ValueError("无法获取起始/结束日的申万行业数据")

    # 筛选一级行业：ts_code 在 _SW_L1_TUSHARE 中
    l1_codes = set(_SW_L1_TUSHARE.values())
    # 反向映射 ts_code → sector_name
    code_to_name = {v: k for k, v in _SW_L1_TUSHARE.items()}

    start_map = {}  # ts_code → close
    for _, row in df_start.iterrows():
        if row['ts_code'] in l1_codes:
            start_map[row['ts_code']] = float(row['close'])

    result = {}
    for _, row in df_end.iterrows():
        ts_code = row['ts_code']
        if ts_code in l1_codes and ts_code in start_map:
            sector_name = code_to_name[ts_code]
            end_close = float(row['close'])
            start_close = start_map[ts_code]
            ret = (end_close - start_close) / start_close
            result[sector_name] = {"return": ret, "weight": 0}

    return result


def _get_sw_sector_returns_eastmoney(start_date, end_date):
    """东方财富 HTTP 获取申万一级行业收益率（fallback）"""
    result = {}
    start_fmt = start_date.replace("-", "")
    end_fmt = end_date.replace("-", "")

    for sector_name, em_code in _SW_SECTOR_CODES_EM.items():
        try:
            url = (
                "https://push2his.eastmoney.com/api/qt/stock/kline/get"
                f"?secid=90.{em_code}"
                "&fields1=f1,f2,f3,f4,f5,f6"
                "&fields2=f51,f52,f53,f54,f55,f56"
                "&klt=101"
                f"&beg={start_fmt}"
                f"&end={end_fmt}"
                "&fqt=1"
            )
            resp = requests.get(url, timeout=10)
            data = resp.json()
            klines = data.get("data", {}).get("klines", [])
            if klines and len(klines) >= 2:
                first = klines[0].split(",")
                last = klines[-1].split(",")
                first_close = float(first[2])
                last_close = float(last[2])
                ret = (last_close - first_close) / first_close
                result[sector_name] = {"return": ret, "weight": 0}
        except Exception:
            continue

    return result


def get_sw_sector_returns(start_date, end_date):
    """获取申万一级行业指数收益率

    数据源优先级: Tushare sw_daily → 东方财富 HTTP
    返回 {sector_name: {"return": float, "weight": 0}}
    """
    # 尝试 Tushare
    try:
        result = _get_sw_sector_returns_tushare(start_date, end_date)
        if len(result) >= 20:  # 至少拿到 20 个行业才算成功
            print(f"  申万行业数据: Tushare ({len(result)} 个行业)")
            return result
        print(f"  Tushare 仅返回 {len(result)} 个行业，尝试东方财富...")
    except Exception as e:
        print(f"  Tushare 获取申万行业失败: {e}，尝试东方财富...")

    # fallback 东方财富
    result = _get_sw_sector_returns_eastmoney(start_date, end_date)
    if result:
        print(f"  申万行业数据: 东方财富 ({len(result)} 个行业)")
    return result


# ============================================================
# 公开 API：东方财富快讯
# ============================================================

def get_eastmoney_news(limit=20):
    """获取东方财富财经快讯

    返回 [str, ...]（纯文本标题列表）
    """
    import re
    try:
        url = (
            f"https://newsapi.eastmoney.com/kuaixun/v1/"
            f"getlist_102_ajaxResult_{limit}_1_.html"
        )
        headers = {"Referer": "https://kuaixun.eastmoney.com/"}
        resp = requests.get(url, headers=headers, timeout=10)
        text = resp.text
        # 响应格式: var ajaxResult={...}
        json_match = re.search(r'var ajaxResult=(\{.*\})', text)
        if not json_match:
            return []
        import json
        data = json.loads(json_match.group(1))
        items = data.get("LivesList", [])
        return [item["title"] for item in items[:limit] if item.get("title")]
    except Exception as e:
        print(f"  获取东方财富快讯失败: {e}")
        return []


# ============================================================
# 公开 API：A 股市场宽度
# ============================================================

def _get_eastmoney_limit_pool_count(pool_type: str, trade_date: str) -> int | None:
    """获取东方财富涨停池/跌停池数量。pool_type: zt / dt"""
    endpoint = {
        "zt": "getTopicZTPool",
        "dt": "getTopicDTPool",
    }[pool_type]
    try:
        resp = requests.get(
            f"https://push2ex.eastmoney.com/{endpoint}",
            params={
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageindex": 0,
                "date": str(trade_date),
                "pagesize": 10000,
                "sort": "fund:asc" if pool_type == "dt" else "fbt:asc",
                "_": "1621590489736",
            },
            headers={
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": "Mozilla/5.0",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data") or {}
        pool = data.get("pool")
        if isinstance(pool, list):
            return len(pool)
        if data.get("tc") is not None:
            return int(data["tc"])
    except Exception as e:
        print(f"  获取东方财富{trade_date}{pool_type}池失败: {e}")
    return None


def _market_turnover_rows_from_indices(review_date: str) -> list[dict]:
    """用沪深主要指数 amount 字段估算两市最近两个交易日成交额。"""
    end_ts = pd.to_datetime(review_date)
    start = (end_ts - pd.Timedelta(days=10)).strftime("%Y%m%d")
    end = end_ts.strftime("%Y%m%d")
    frames = []
    for code in ["000001", "399001"]:
        df = get_benchmark_prices(code, start, end)
        if df is not None and not df.empty and {"date", "amount"}.issubset(df.columns):
            frame = df[["date", "amount"]].copy()
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
            frame["amount"] = pd.to_numeric(frame["amount"], errors="coerce")
            frame = frame.dropna(subset=["date", "amount"])
        else:
            frame = pd.DataFrame(columns=["date", "amount"])
        if frame.empty:
            frame = _fetch_index_amount_only(code, start, end)
        if frame.empty:
            frame = _fetch_eastmoney_index_amount(code, start, end)
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return []

    combined = pd.concat(frames, ignore_index=True)
    turnover = (
        combined.groupby("date", as_index=False)["amount"]
        .sum()
        .sort_values("date")
        .tail(2)
        .reset_index(drop=True)
    )
    return [
        {
            "date": row["date"].strftime("%Y%m%d"),
            "turnover": float(row["amount"]),
        }
        for _, row in turnover.iterrows()
    ]


def _fetch_index_amount_only(benchmark_index: str, start_date: str, end_date: str) -> pd.DataFrame:
    """旧缓存没有 amount 时，单独补指数成交额字段。"""
    try:
        _ensure_bs_login()

        def _query():
            for prefix in ["sh", "sz"]:
                rs = bs.query_history_k_data_plus(
                    f"{prefix}.{benchmark_index}",
                    "date,amount",
                    start_date=_to_bs_date(start_date),
                    end_date=_to_bs_date(end_date),
                    frequency="d",
                )
                rows = []
                while (rs.error_code == "0") and rs.next():
                    rows.append(rs.get_row_data())
                if rows:
                    return pd.DataFrame(rows, columns=["date", "amount"])
            return pd.DataFrame(columns=["date", "amount"])

        df = _run_with_timeout(_query, timeout=15)
        if df is None or df.empty:
            return pd.DataFrame(columns=["date", "amount"])
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
        return df.dropna(subset=["date", "amount"]).reset_index(drop=True)
    except Exception as e:
        print(f"  获取指数 {benchmark_index} 成交额失败: {e}")
        return pd.DataFrame(columns=["date", "amount"])


def _fetch_eastmoney_index_amount(benchmark_index: str, start_date: str, end_date: str) -> pd.DataFrame:
    """东方财富补指数成交额，避免 baostock 不可用时缺两市成交额。"""
    market = "0" if str(benchmark_index).startswith("399") else "1"
    try:
        resp = requests.get(
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            params={
                "secid": f"{market}.{benchmark_index}",
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57",
                "klt": "101",
                "beg": str(start_date).replace("-", ""),
                "end": str(end_date).replace("-", ""),
                "fqt": "1",
            },
            timeout=10,
        )
        resp.raise_for_status()
        klines = (resp.json().get("data") or {}).get("klines") or []
        rows = []
        for line in klines:
            parts = line.split(",")
            if len(parts) < 7:
                continue
            rows.append({"date": parts[0], "amount": parts[6]})
        if not rows:
            return pd.DataFrame(columns=["date", "amount"])
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
        return df.dropna(subset=["date", "amount"]).reset_index(drop=True)
    except Exception as e:
        print(f"  东方财富获取指数 {benchmark_index} 成交额失败: {e}")
        return pd.DataFrame(columns=["date", "amount"])


def get_a_share_market_breadth(review_date: str) -> dict:
    """获取 A 股两日成交额和涨跌停对比。"""
    turnover_rows = _market_turnover_rows_from_indices(review_date)
    limit_rows = []
    for row in turnover_rows:
        trade_date = row["date"]
        limit_up = _get_eastmoney_limit_pool_count("zt", trade_date)
        limit_down = _get_eastmoney_limit_pool_count("dt", trade_date)
        limit_rows.append({
            "date": trade_date,
            "limit_up": limit_up,
            "limit_down": limit_down,
        })

    return {
        "turnover_rows": turnover_rows,
        "limit_rows": limit_rows,
    }


# ============================================================
# 公开 API：指数成分股
# ============================================================

def get_index_constituents(benchmark_index):
    """获取指数成分股代码列表 → baostock

    返回 [code_str, ...]（6位代码，不含前缀）
    """
    _ensure_bs_login()

    # baostock 提供特定指数的成分股查询
    query_map = {
        "000300": bs.query_hs300_stocks,
        "000905": bs.query_zz500_stocks,
    }

    codes = []
    query_fn = query_map.get(benchmark_index)

    if query_fn:
        def _query_constituents(fn=query_fn):
            result = []
            rs = fn()
            while (rs.error_code == '0') and rs.next():
                row = rs.get_row_data()
                # row[1] = "sh.600000" 格式
                if len(row) >= 2:
                    result.append(row[1].split(".")[-1])
            return result

        try:
            codes = _run_with_timeout(_query_constituents)
        except (TimeoutError, Exception) as e:
            print(f"  警告: 获取指数 {benchmark_index} 成分股超时: {e}")
        return codes

    # 通用方案：query_stock_basic 获取全量后无法按指数筛选
    # 对于不支持的指数，返回空列表，调用方会 fallback 等权
    print(f"  警告: baostock 不直接支持指数 {benchmark_index} 的成分股查询，将使用等权近似")
    return codes
