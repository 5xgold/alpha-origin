#!/bin/bash
# 策略归因分析 - 快速启动脚本
# 用法: ./quickstart.sh [PDF文件路径] [开始日期] [结束日期]
# 选项: --force-refresh  清空缓存重新执行

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

# ── 解析参数 ──
FORCE_REFRESH=false
POSITIONAL=()
for arg in "$@"; do
    case $arg in
        --force-refresh)
            FORCE_REFRESH=true
            ;;
        *)
            POSITIONAL+=("$arg")
            ;;
    esac
done

PDF_INPUT="${POSITIONAL[0]:-data/raw/$(ls data/raw/*.pdf 2>/dev/null | head -1 | xargs basename 2>/dev/null)}"
START_DATE="${POSITIONAL[1]:-2026-01-01}"
END_DATE="${POSITIONAL[2]:-2026-03-31}"
DATA_DIR="data"
REPORT="../output/report.md"

# ── 检查环境 ──
echo "=========================================="
echo "  策略归因分析 - 快速启动"
echo "=========================================="
echo ""

command -v python3 >/dev/null 2>&1 || error "未找到 python3，请先安装 Python 3.14+"
info "Python: $(python3 --version)"

# ── 创建目录 ──
mkdir -p data/raw data/cache ../output

# ── 虚拟环境 ──
VENV_DIR=".venv"
DEPS_MARKER="$VENV_DIR/.deps_installed"
if [ ! -d "$VENV_DIR" ]; then
    warn "创建虚拟环境..."
    python3 -m venv "$VENV_DIR"
    info "虚拟环境已创建: $VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
info "已激活虚拟环境"

# ── 安装依赖 ──
CURRENT_DEPS_HASH="$(shasum -a 256 requirements.txt | awk '{print $1}')"
INSTALLED_DEPS_HASH=""
[ -f "$DEPS_MARKER" ] && INSTALLED_DEPS_HASH="$(cat "$DEPS_MARKER")"

if [ "$CURRENT_DEPS_HASH" != "$INSTALLED_DEPS_HASH" ]; then
    warn "安装依赖..."
    pip install -r requirements.txt -q -i https://pypi.tuna.tsinghua.edu.cn/simple
    printf '%s\n' "$CURRENT_DEPS_HASH" > "$DEPS_MARKER"
    info "依赖安装完成"
else
    info "依赖已安装"
fi

# ── Step 1: 转换 PDF → data/ 目录 ──
CONVERT_ARGS=""
if [ "$FORCE_REFRESH" = true ]; then
    CONVERT_ARGS="--force-refresh"
    # 清空已有数据文件，强制重新转换
    rm -f "$DATA_DIR/trades.csv" "$DATA_DIR/holdings.csv" "$DATA_DIR/cash_flows.csv"
    info "已清空数据文件，将重新转换"
fi

if [ ! -f "$DATA_DIR/trades.csv" ]; then
    if [ -z "$PDF_INPUT" ] || [ ! -f "$PDF_INPUT" ]; then
        error "未找到 PDF 文件。用法: ./quickstart.sh <PDF路径> [开始日期] [结束日期]"
    fi
    info "转换 PDF: $PDF_INPUT → $DATA_DIR/"
    python3 ../shared/convert_broker_data.py \
        --input "$PDF_INPUT" \
        --output-dir "$DATA_DIR" \
        $CONVERT_ARGS
    info "数据文件已生成 → $DATA_DIR/{trades,holdings,cash_flows}.csv"
else
    info "数据文件已存在: $DATA_DIR/trades.csv (跳过转换)"
fi

# ── Step 2: 运行分析 ──
info "运行归因分析 ($START_DATE ~ $END_DATE)..."

ANALYSIS_ARGS="--trades $DATA_DIR/trades.csv"
[ -f "$DATA_DIR/holdings.csv" ] && ANALYSIS_ARGS="$ANALYSIS_ARGS --holdings $DATA_DIR/holdings.csv"
[ -f "$DATA_DIR/cash_flows.csv" ] && ANALYSIS_ARGS="$ANALYSIS_ARGS --cash-flows $DATA_DIR/cash_flows.csv"

python3 scripts/attribution.py \
    $ANALYSIS_ARGS \
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
