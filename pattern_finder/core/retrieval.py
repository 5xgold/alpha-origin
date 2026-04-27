"""
相似度检索模块
支持两种方式：
  1. 余弦相似度 + FAISS 向量检索（快速，适合大样本库）
  2. DTW 时间序列相似度（慢但对"形态走势"更精准）
  3. 混合融合评分
"""

import numpy as np
import pandas as pd
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field
import pickle
import os

# ─── 数据结构 ─────────────────────────────────────────────────────

@dataclass
class SampleRecord:
    """样本库中的单条记录"""
    stock_code:   str
    start_date:   str
    end_date:     str
    label:        int                    # 1=成功案例 0=非成功
    vector:       np.ndarray             # 用于余弦/FAISS 检索
    price_norm:   np.ndarray             # 归一化价格序列（用于 DTW）
    future_return: float                 # 未来 FORWARD_DAYS 最大收益率
    future_drawdown: float               # 期间最大回撤
    entry_price:  float


@dataclass
class SearchResult:
    """单条检索结果"""
    sample:       SampleRecord
    cosine_sim:   float = 0.0
    dtw_dist:     float = 0.0
    combined_score: float = 0.0


# ─── 样本库（内存 + 持久化） ──────────────────────────────────────

class SampleLibrary:
    """管理历史成功/失败案例的样本库"""

    def __init__(self):
        self.records: List[SampleRecord] = []
        self._vectors: Optional[np.ndarray] = None   # shape: (N, D)

    def add(self, record: SampleRecord):
        self.records.append(record)
        self._vectors = None   # 清除缓存

    def add_batch(self, records: List[SampleRecord]):
        self.records.extend(records)
        self._vectors = None

    @property
    def vectors(self) -> np.ndarray:
        """懒加载向量矩阵"""
        if self._vectors is None and self.records:
            self._vectors = np.stack(
                [r.vector for r in self.records]
            ).astype(np.float32)
        return self._vectors

    def filter_success_only(self) -> "SampleLibrary":
        """只保留成功案例"""
        lib = SampleLibrary()
        lib.records = [r for r in self.records if r.label == 1]
        return lib

    def save(self, path: str):
        with open(path, "wb") as f:
            pickle.dump(self, f)

    @classmethod
    def load(cls, path: str) -> "SampleLibrary":
        with open(path, "rb") as f:
            return pickle.load(f)

    def __len__(self):
        return len(self.records)


# ─── 余弦相似度检索 ───────────────────────────────────────────────

def cosine_search(
    query_vec: np.ndarray,
    library: SampleLibrary,
    top_k: int = 50,
    success_only: bool = True,
) -> List[Tuple[int, float]]:
    """
    余弦相似度快速召回
    返回 [(record_index, similarity_score), ...]
    """
    if success_only:
        lib = library.filter_success_only()
    else:
        lib = library

    if len(lib) == 0:
        return []

    mat = lib.vectors   # (N, D)
    q   = query_vec.astype(np.float32)

    # L2 归一化
    mat_norm = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    q_norm   = q   / (np.linalg.norm(q) + 1e-9)

    sims = mat_norm @ q_norm   # (N,)
    top_idx = np.argsort(sims)[::-1][:top_k]

    return [(int(i), float(sims[i])) for i in top_idx]


# ─── FAISS 向量检索（大样本库优化） ──────────────────────────────

def faiss_search(
    query_vec: np.ndarray,
    library: SampleLibrary,
    top_k: int = 50,
    success_only: bool = True,
) -> List[Tuple[int, float]]:
    """
    使用 FAISS 做快速近邻检索（需要 pip install faiss-cpu）
    如果 FAISS 不可用，自动降级到余弦检索
    """
    try:
        import faiss
    except ImportError:
        return cosine_search(query_vec, library, top_k, success_only)

    if success_only:
        lib = library.filter_success_only()
    else:
        lib = library

    if len(lib) == 0:
        return []

    mat = lib.vectors.copy()
    q   = query_vec.astype(np.float32).reshape(1, -1)

    # 归一化后用内积 = 余弦相似度
    faiss.normalize_L2(mat)
    faiss.normalize_L2(q)

    dim   = mat.shape[1]
    index = faiss.IndexFlatIP(dim)
    index.add(mat)
    distances, indices = index.search(q, top_k)

    return [(int(indices[0][i]), float(distances[0][i]))
            for i in range(len(indices[0]))
            if indices[0][i] >= 0]


# ─── DTW 相似度计算 ───────────────────────────────────────────────

def dtw_distance(seq1: np.ndarray, seq2: np.ndarray,
                 window: Optional[int] = None) -> float:
    """
    手工实现 DTW（无需外部依赖，支持 Sakoe-Chiba 窗口约束）
    seq1, seq2: 1D 归一化价格序列
    """
    n, m = len(seq1), len(seq2)
    w    = window if window else max(n, m)

    # 用 Inf 初始化 DTW 矩阵
    dtw = np.full((n + 1, m + 1), np.inf)
    dtw[0, 0] = 0

    for i in range(1, n + 1):
        for j in range(max(1, i - w), min(m, i + w) + 1):
            cost      = abs(seq1[i-1] - seq2[j-1])
            dtw[i, j] = cost + min(dtw[i-1, j],
                                   dtw[i, j-1],
                                   dtw[i-1, j-1])
    return float(dtw[n, m])


def dtw_distance_fast(seq1: np.ndarray, seq2: np.ndarray,
                      window: Optional[int] = None) -> float:
    """
    优先使用 dtaidistance 库（更快），如不可用则用手工实现
    """
    try:
        from dtaidistance import dtw as dtaidtw
        if window:
            return float(dtaidtw.distance_fast(
                seq1.astype(np.double),
                seq2.astype(np.double),
                window=window
            ))
        else:
            return float(dtaidtw.distance_fast(
                seq1.astype(np.double),
                seq2.astype(np.double)
            ))
    except ImportError:
        return dtw_distance(seq1, seq2, window)


# ─── 混合相似度检索（主接口） ────────────────────────────────────

def hybrid_search(
    query_window_df: pd.DataFrame,
    library: SampleLibrary,
    top_k: int = 20,
    cosine_top_k: int = 100,   # 先用余弦召回 N 个候选
    dtw_weight: float = 0.5,
    cosine_weight: float = 0.5,
    success_only: bool = True,
    use_faiss: bool = True,
) -> List[SearchResult]:
    """
    两阶段混合检索：
    1. 余弦相似度/FAISS 快速召回 cosine_top_k 个候选
    2. 对候选做 DTW 精排
    3. 加权融合评分，返回 top_k 个结果
    """
    from features.feature_engine import extract_vector, normalize_price_series

    query_vec      = extract_vector(query_window_df)
    query_price    = normalize_price_series(
                         query_window_df["close"]
                     ).values.astype(np.float64)

    # ── 阶段 1：余弦快速召回 ─────────────────────────────────────
    if use_faiss:
        candidates = faiss_search(query_vec, library,
                                  top_k=cosine_top_k,
                                  success_only=success_only)
    else:
        candidates = cosine_search(query_vec, library,
                                   top_k=cosine_top_k,
                                   success_only=success_only)

    if not candidates:
        return []

    # 成功案例过滤后的库
    if success_only:
        lib_records = library.filter_success_only().records
    else:
        lib_records = library.records

    # ── 阶段 2：DTW 精排 ────────────────────────────────────────
    results = []
    for idx, cosine_sim in candidates:
        record = lib_records[idx]
        dtw_d  = dtw_distance_fast(
            query_price,
            record.price_norm,
            window=len(query_price) // 5,   # 20% 窗口约束
        )
        results.append((record, cosine_sim, dtw_d))

    # 归一化 DTW 距离（转为 similarity）
    dtw_vals = np.array([r[2] for r in results])
    dtw_max  = dtw_vals.max() if dtw_vals.max() > 0 else 1.0
    dtw_sims = 1 - dtw_vals / dtw_max   # 越小越像，转为越大越像

    # ── 加权融合 ──────────────────────────────────────────────
    search_results = []
    for i, (record, cos_sim, dtw_d) in enumerate(results):
        combined = cosine_weight * cos_sim + dtw_weight * dtw_sims[i]
        search_results.append(SearchResult(
            sample        = record,
            cosine_sim    = cos_sim,
            dtw_dist      = dtw_d,
            combined_score= combined,
        ))

    # 按融合分数降序排列
    search_results.sort(key=lambda x: x.combined_score, reverse=True)
    return search_results[:top_k]


# ─── 将 windows 批量入库 ─────────────────────────────────────────

def build_library_from_windows(
    windows: list,
    stock_code: str,
    library: Optional[SampleLibrary] = None,
    success_only: bool = False,
) -> SampleLibrary:
    """
    把 create_windows() 的输出批量加入样本库
    如果 success_only=True，只添加 label=1 的成功案例
    """
    from features.feature_engine import extract_vector, normalize_price_series

    if library is None:
        library = SampleLibrary()

    for w in windows:
        if success_only and w["label"] == 0:
            continue

        feat_df     = w["feature_df"]
        future_c    = w["future_close"]
        entry_price = w["entry_price"]

        future_ret  = (future_c / entry_price - 1).max()
        future_dd   = abs(min(0, (future_c / entry_price - 1).min()))

        vec         = extract_vector(feat_df)
        price_norm  = normalize_price_series(
                          feat_df["close"]
                      ).values.astype(np.float64)

        record = SampleRecord(
            stock_code      = stock_code,
            start_date      = str(w["meta"]["start_date"])[:10],
            end_date        = str(w["meta"]["end_date"])[:10],
            label           = w["label"],
            vector          = vec,
            price_norm      = price_norm,
            future_return   = future_ret,
            future_drawdown = future_dd,
            entry_price     = entry_price,
        )
        library.add(record)

    return library


if __name__ == "__main__":
    # 快速测试
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

    from features.feature_engine import build_indicators, create_windows

    np.random.seed(0)
    n = 300
    dates  = pd.date_range("2021-01-01", periods=n, freq="B")
    close  = pd.Series(50 * np.cumprod(1 + np.random.randn(n) * 0.012),
                       index=dates)
    df = pd.DataFrame({
        "open":   close * 0.99, "high": close * 1.02,
        "low":    close * 0.98, "close": close,
        "volume": np.random.randint(1e6, 5e6, n),
    })

    df_feat = build_indicators(df)
    wins    = create_windows(df_feat)
    lib     = build_library_from_windows(wins, "000001.SZ")

    print(f"[样本库大小] {len(lib)} 条")
    print(f"[成功案例数] {sum(r.label for r in lib.records)} 条")

    if wins:
        results = hybrid_search(wins[-1]["feature_df"], lib, top_k=5)
        for r in results:
            print(f"  {r.sample.stock_code} {r.sample.end_date} "
                  f"| 综合={r.combined_score:.3f} "
                  f"| 余弦={r.cosine_sim:.3f} "
                  f"| 未来最大收益={r.sample.future_return:.1%}")
