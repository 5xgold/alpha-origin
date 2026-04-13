"""公共配置 — 数据源 / 缓存 / 外部服务"""

import os
from pathlib import Path
from dotenv import load_dotenv

# .env 优先从项目根目录加载，兼容旧位置
_root = Path(__file__).parent.parent
_env_candidates = [_root / ".env", _root / "attribution_analysis" / ".env"]
for _env in _env_candidates:
    if _env.exists():
        load_dotenv(_env)
        break

# 外部服务
TS_TOKEN = os.getenv("TS_TOKEN", "")
FUTU_HOST = os.getenv("FUTU_HOST", "127.0.0.1")
FUTU_PORT = int(os.getenv("FUTU_PORT", "11111"))

# 数据缓存
CACHE_DIR = str(_root / "data" / "cache")
CACHE_EXPIRY_DAYS = 7
SECTOR_CACHE_DAYS = 30
SECTOR_CLASSIFICATION = "sw"


def parse_benchmark_config(cfg):
    """解析基准配置，返回标准化的组件列表

    Args:
        cfg: str（单一基准）或 list[dict]（复合基准）

    Returns:
        list[dict]: [{"index": str, "weight": float, "source": str}, ...]
    """
    if isinstance(cfg, str):
        return [{"index": cfg, "weight": 1.0, "source": "baostock"}]

    if not isinstance(cfg, list) or len(cfg) == 0:
        raise ValueError(f"无效的基准配置: {cfg}")

    components = []
    for item in cfg:
        idx = item["index"]
        weight = item["weight"]
        source = "futu" if idx.startswith("HK.") else "baostock"
        components.append({"index": idx, "weight": weight, "source": source})

    total_w = sum(c["weight"] for c in components)
    if total_w > 0:
        for c in components:
            c["weight"] /= total_w

    return components
