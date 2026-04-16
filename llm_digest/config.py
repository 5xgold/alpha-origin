"""LLM 信息压缩模块 — 配置"""

import os
from pathlib import Path
from dotenv import load_dotenv

_root = Path(__file__).parent.parent
for _env in [_root / ".env", _root / "attribution_analysis" / ".env"]:
    if _env.exists():
        load_dotenv(_env)
        break

# LLM
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-chat")
LLM_MAX_TOKENS = 2000
LLM_TEMPERATURE = 0.3

# 路径
PROMPTS_DIR = Path(__file__).parent / "prompts"
OUTPUT_DIR = _root / "output"

# 数据路径（复用现有模块）
AA_DATA_DIR = _root / "attribution_analysis" / "data"
RC_DATA_DIR = _root / "risk_control" / "data"
