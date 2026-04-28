#!/bin/bash
# 量化工具集 - 统一入口
#
# 用法:
#   ./quickstart.sh all <PDF路径> [开始日期] [结束日期]   # 全流程
#   ./quickstart.sh parse <PDF路径>                       # 仅解析 PDF
#   ./quickstart.sh attr [开始日期] [结束日期]             # 仅归因分析
#   ./quickstart.sh risk [总权益]                          # 仅风控检查
#
# 总权益自动从 asset_summary.json 读取，也可手动指定

set -e

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }
step()  { echo -e "\n${CYAN}══ $1 ══${NC}\n"; }

AA_DIR="$ROOT_DIR/attribution_analysis"
RC_DIR="$ROOT_DIR/risk_control"

usage() {
    echo "用法:"
    echo "  ./quickstart.sh all <PDF> [开始日期] [结束日期]"
    echo "  ./quickstart.sh parse <PDF>"
    echo "  ./quickstart.sh attr [开始日期] [结束日期]"
    echo "  ./quickstart.sh risk [总权益]"
    echo "  ./quickstart.sh review [股票代码]          # 交易复盘"
    echo "  ./quickstart.sh earnings <PDF> <股票代码>  # 财报摘要"
    echo "  ./quickstart.sh sync-portfolio            # 同步 portfolio.toml → CSV"
    echo "  ./quickstart.sh pattern <command> [args]  # 形态检索"
    exit 1
}

# ── 环境准备 ──
setup_env() {
    VENV_DIR="$ROOT_DIR/.venv"
    DEPS_MARKER="$VENV_DIR/.deps_installed"
    if [ ! -d "$VENV_DIR" ]; then
        warn "创建虚拟环境..."
        python3 -m venv "$VENV_DIR"
    fi
    source "$VENV_DIR/bin/activate"
    info "Python: $(python3 --version)"

    current_deps_hash="$(shasum -a 256 requirements.txt | awk '{print $1}')"
    installed_deps_hash=""
    [ -f "$DEPS_MARKER" ] && installed_deps_hash="$(cat "$DEPS_MARKER")"

    if [ "$current_deps_hash" != "$installed_deps_hash" ]; then
        warn "安装依赖..."
        pip install -r requirements.txt -q -i https://pypi.tuna.tsinghua.edu.cn/simple
        printf '%s\n' "$current_deps_hash" > "$DEPS_MARKER"
        info "依赖安装完成"
    fi
}

# ── PDF 解析 ──
do_parse() {
    local pdf="$1"
    [ -z "$pdf" ] && error "请指定 PDF 路径"
    [ ! -f "$pdf" ] && error "PDF 文件不存在: $pdf"

    step "PDF 解析"
    python3 "$ROOT_DIR/shared/convert_broker_data.py" \
        --input "$pdf" \
        --output-dir "$AA_DIR/data"

    # 同步持仓到风控模块
    cp "$AA_DIR/data/holdings.csv" "$RC_DIR/data/portfolio.csv"
    info "持仓已同步到 risk_control/data/portfolio.csv"
}

# ── 策略归因 ──
do_attr() {
    local start="${1:-2026-01-01}"
    local end="${2:-2026-03-31}"

    [ ! -f "$AA_DIR/data/trades.csv" ] && error "未找到交易数据，请先运行: ./quickstart.sh parse <PDF>"

    step "策略归因分析 ($start ~ $end)"

    local args="--trades $AA_DIR/data/trades.csv"
    [ -f "$AA_DIR/data/holdings.csv" ] && args="$args --holdings $AA_DIR/data/holdings.csv"
    [ -f "$AA_DIR/data/cash_flows.csv" ] && args="$args --cash-flows $AA_DIR/data/cash_flows.csv"

    cd "$AA_DIR"
    python3 scripts/attribution.py \
        $args \
        --start-date "$start" \
        --end-date "$end" \
        --output $ROOT_DIR/output/report.md
    cd "$ROOT_DIR"

    info "归因报告: output/report.md"
}

# ── 风控检查 ──
do_risk() {
    local equity="$1"

    # 总权益：参数 > asset_summary.json
    if [ -z "$equity" ]; then
        local asset_json="$AA_DIR/data/asset_summary.json"
        if [ -f "$asset_json" ]; then
            equity=$(python3 -c "import json; print(json.load(open('$asset_json'))['total_assets'])")
            info "总权益（来自 PDF）: ¥$(printf "%'.0f" "${equity%.*}")"
        else
            error "无法获取总权益，请指定金额或先运行: ./quickstart.sh parse <PDF>"
        fi
    fi

    [ ! -f "$RC_DIR/data/portfolio.csv" ] && error "未找到持仓文件，请先运行: ./quickstart.sh parse <PDF>"

    step "风控检查"
    python3 "$RC_DIR/scripts/risk_report.py" \
        --portfolio "$RC_DIR/data/portfolio.csv" \
        --equity "$equity"

    info "风控报告: output/"
}

# ── 交易复盘 ──
do_review() {
    local code="$1"
    step "交易复盘"
    local args=""
    [ -n "$code" ] && args="--code $code"
    python3 "$ROOT_DIR/llm_digest/scripts/trade_review.py" $args
}

# ── 财报摘要 ──
do_earnings() {
    local pdf="$1"
    local code="$2"
    [ -z "$pdf" ] && error "请指定财报 PDF 路径"
    [ -z "$code" ] && error "请指定股票代码"
    [ ! -f "$pdf" ] && error "PDF 文件不存在: $pdf"

    step "财报摘要"
    python3 "$ROOT_DIR/llm_digest/scripts/earnings_summary.py" \
        --input "$pdf" --code "$code"
}

# ── 同步持仓配置 ──
do_sync_portfolio() {
    step "同步持仓配置"
    if [ ! -f "$ROOT_DIR/portfolio.toml" ]; then
        warn "portfolio.toml 不存在，使用示例文件创建..."
        if [ -f "$ROOT_DIR/portfolio.toml.example" ]; then
            cp "$ROOT_DIR/portfolio.toml.example" "$ROOT_DIR/portfolio.toml"
            warn "已创建 portfolio.toml，请修改为你的实际持仓"
            exit 0
        else
            error "示例文件 portfolio.toml.example 不存在"
        fi
    fi

    python3 "$ROOT_DIR/shared/portfolio_config.py"
    info "持仓配置已同步到 risk_control/data/portfolio.csv"
}

# ── 形态检索 ──
do_pattern() {
    step "形态检索"
    cd "$ROOT_DIR/pattern_finder"
    ./quickstart.sh "$@"
}

# ── 主流程 ──
CMD="${1:-all}"
shift 2>/dev/null || true

echo "=========================================="
echo "  量化工具集"
echo "=========================================="

setup_env

case "$CMD" in
    parse)
        do_parse "$1"
        ;;
    attr)
        do_attr "$1" "$2"
        ;;
    risk)
        do_risk "$1"
        ;;
    review)
        do_review "$1"
        ;;
    earnings)
        do_earnings "$1" "$2"
        ;;
    sync-portfolio)
        do_sync_portfolio
        ;;
    pattern)
        do_pattern "$@"
        ;;
    all)
        do_parse "$1"
        do_attr "${2:-2026-01-01}" "${3:-2026-03-31}"
        do_risk
        ;;
    *)
        usage
        ;;
esac

echo ""
echo "=========================================="
echo "  完成！"
echo "=========================================="
