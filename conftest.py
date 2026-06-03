"""pytest 根配置 — 将 app/ 加入 sys.path。

源码模块统一位于 app/ 目录（attribution_analysis / pattern_finder /
risk_control / shared / watchlist_signals）。在仓库根目录运行 pytest 时，
将 app/ 置于 sys.path 首位，使得各模块可按 `from risk_control...`、
`from shared import ...` 的顶级包名导入，与生产运行时的 sys.path 行为一致。
"""

import sys
from pathlib import Path

_APP_ROOT = Path(__file__).parent / "app"
if _APP_ROOT.is_dir():
    sys.path.insert(0, str(_APP_ROOT))
