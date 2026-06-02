#!/bin/bash
# 量化工具集 - 统一入口
#
# 用法:
#   ./quickstart.sh all <PDF路径> [开始日期] [结束日期]   # 归因全流程
#   ./quickstart.sh parse <PDF路径>                       # 仅解析 PDF
#   ./quickstart.sh attr [开始日期] [结束日期]             # 仅归因分析
#   ./quickstart.sh risk [总权益]                          # 仅风控检查
#   ./quickstart.sh risk-data [日期]                       # 风控补数需求检查
#   ./quickstart.sh risk-merge [日期]                      # 合并外部补数 cache
#
# 总权益默认从 portfolio.toml 读取，也可手动指定

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
    echo "  ./quickstart.sh all <PDF> [开始日期] [结束日期]   # 归因全流程，不运行风控"
    echo "  ./quickstart.sh parse <PDF>"
    echo "  ./quickstart.sh attr [开始日期] [结束日期]"
    echo "  ./quickstart.sh risk-data [日期]             # 风控补数需求检查"
    echo "  ./quickstart.sh risk-merge [日期]            # 合并外部补数 cache"
    echo "  ./quickstart.sh risk [总权益]"
    echo "  ./quickstart.sh backtest [开始日期] [结束日期] [--sweep]  # 风控回测"
    echo "  ./quickstart.sh review [股票代码]          # 交易复盘"
    echo "  ./quickstart.sh earnings <PDF> <股票代码>  # 财报摘要"
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

    info "PDF 解析完成；归因数据已写入 attribution_analysis/data/"
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

# ── 风控数据需求检查 ──
do_risk_data() {
    local date="$1"
    local args=""
    [ -n "$date" ] && args="--date $date"
    step "风控补数需求检查"
    python3 "$RC_DIR/data_dependencies.py" $args
}

# ── 风控补数合并 ──
do_risk_merge() {
    local date="$1"
    local args=""
    [ -n "$date" ] && args="--date $date"
    step "风控补数合并"
    python3 "$RC_DIR/agent_price_cache.py" $args --strict
}

# ── 风控回测 ──
do_backtest() {
    local args=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --sweep) args="$args --sweep"; shift ;;
            --regime) args="$args --regime $2"; shift 2 ;;
            *)
                if [ -z "$(echo "$args" | grep -- '--start')" ]; then
                    args="$args --start $1"
                else
                    args="$args --end $1"
                fi
                shift ;;
        esac
    done
    step "风控回测"
    python3 "$RC_DIR/backtest/run.py" $args
    info "回测报告: output/"
}

# ── 风控检查 ──
do_risk() {
    local equity="$1"
    local equity_args=""

    # 总权益：参数 > portfolio.toml（由 Python 自动读取）
    if [ -n "$equity" ]; then
        equity_args="--equity $equity"
    else
        info "总权益将从 portfolio.toml 读取"
    fi

    step "风控补数需求检查"
    python3 "$RC_DIR/data_dependencies.py" --fetch-missing --strict

    step "风控检查"
    python3 "$RC_DIR/scripts/risk_report.py" \
        $equity_args

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
    risk-data)
        do_risk_data "$1"
        ;;
    risk-merge)
        do_risk_merge "$1"
        ;;
    backtest)
        do_backtest "$@"
        ;;
    review)
        do_review "$1"
        ;;
    earnings)
        do_earnings "$1" "$2"
        ;;
    pattern)
        do_pattern "$@"
        ;;
    all)
        do_parse "$1"
        do_attr "${2:-2026-01-01}" "${3:-2026-03-31}"
        ;;
    *)
        usage
        ;;
esac

echo ""
echo "=========================================="
echo "  完成！"
echo "=========================================="
