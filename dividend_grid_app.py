"""Streamlit Cloud 部署入口

Streamlit Community Cloud 部署时, "Main file path" 填本文件:
    dividend_grid_app.py

本地运行同样可用:
    streamlit run dividend_grid_app.py
"""

import sys
from pathlib import Path

# 把 app/ 注入 sys.path, 复用 app/dividend_grid 模块
sys.path.insert(0, str(Path(__file__).parent / "app"))

from dividend_grid.streamlit_app import main

main()
