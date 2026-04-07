#!/bin/bash
# 策略归因分析 - 快速启动脚本
# 用法: ./quickstart.sh [PDF文件路径] [开始日期] [结束日期]

set -e

cd "$(dirname "$0")"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# ── 参数 ──
PDF_INPUT="${1:-data/raw/$(ls data/raw/*.pdf 2>/dev/null | head -1 | xargs basename 2>/dev/null)}"
START_DATE="${2:-2025-01-01}"
END_DATE="${3:-2026-03-31}"
TRADES_CSV="data/trades.csv"
REPORT="output/report.md"

# ── 检查环境 ──
echo "=========================================="
echo "  策略归因分析 - 快速启动"
echo "=========================================="
echo ""

command -v python3 >/dev/null 2>&1 || error "未找到 python3，请先安装 Python 3.14+"
info "Python: $(python3 --version)"

# ── 创建目录 ──
mkdir -p data/raw data/cache output

# ── 虚拟环境 ──
VENV_DIR=".venv"
if [ ! -d "$VENV_DIR" ]; then
    warn "创建虚拟环境..."
    python3 -m venv "$VENV_DIR"
    info "虚拟环境已创建: $VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
info "已激活虚拟环境"

# ── 安装依赖 ──
if [ ! -f "$VENV_DIR/.deps_installed" ]; then
    warn "安装依赖..."
    pip install -r requirements.txt -q -i https://pypi.tuna.tsinghua.edu.cn/simple
    touch "$VENV_DIR/.deps_installed"
    info "依赖安装完成"
else
    info "依赖已安装"
fi

# ── Step 1: 转换 PDF ──
if [ ! -f "$TRADES_CSV" ]; then
    if [ -z "$PDF_INPUT" ] || [ ! -f "$PDF_INPUT" ]; then
        error "未找到 PDF 文件。用法: ./quickstart.sh <PDF路径> [开始日期] [结束日期]"
    fi
    info "转换 PDF: $PDF_INPUT"
    python3 scripts/convert_broker_data.py \
        --input "$PDF_INPUT" \
        --output "$TRADES_CSV"
    info "交割单已转换 → $TRADES_CSV"
else
    info "交割单已存在: $TRADES_CSV (跳过转换)"
fi

# ── Step 2: 运行分析 ──
info "运行归因分析 ($START_DATE ~ $END_DATE)..."
python3 scripts/attribution.py \
    --trades "$TRADES_CSV" \
    --start-date "$START_DATE" \
    --end-date "$END_DATE" \
    --output "$REPORT"

# ── Step 3: 打开报告 ──
if [ -f "$REPORT" ]; then
    info "报告已生成 → $REPORT"
    echo ""
    cat "$REPORT"
else
    error "报告生成失败"
fi

echo ""
echo "=========================================="
echo "  完成！"
echo "=========================================="
