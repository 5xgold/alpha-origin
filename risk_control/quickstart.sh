#!/bin/bash
# 风控检查 - 快速启动脚本
# 用法: ./quickstart.sh [总权益]
# 示例: ./quickstart.sh 500000

set -e

cd "$(dirname "$0")"
ROOT_DIR="$(cd .. && pwd)"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }

EQUITY="${1:-}"

echo "=========================================="
echo "  风控检查 - 快速启动"
echo "=========================================="
echo ""

# ── 检查虚拟环境 ──
VENV_DIR="$ROOT_DIR/.venv"
if [ ! -d "$VENV_DIR" ]; then
    warn "创建虚拟环境..."
    python3 -m venv "$VENV_DIR"
    info "虚拟环境已创建: $VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
info "Python: $(python3 --version)"
info "已激活虚拟环境: $VENV_DIR"

# ── 安装依赖 ──
if [ ! -f "$VENV_DIR/.risk_deps_installed" ]; then
    warn "安装依赖..."
    pip install -r requirements.txt -q -i https://pypi.tuna.tsinghua.edu.cn/simple
    touch "$VENV_DIR/.risk_deps_installed"
    info "依赖安装完成"
else
    info "依赖已安装"
fi

# ── 检查 .env ──
if [ ! -f "$ROOT_DIR/.env" ]; then
    if [ -f "$ROOT_DIR/attribution_analysis/.env" ]; then
        cp "$ROOT_DIR/attribution_analysis/.env" "$ROOT_DIR/.env"
        info "已从 attribution_analysis 复制 .env"
    else
        warn "未找到 .env，港股数据可能无法获取"
        warn "请创建 $ROOT_DIR/.env 并配置 FUTU_HOST/FUTU_PORT/TS_TOKEN"
    fi
fi

# ── 检查持仓文件 ──
mkdir -p data ../output

if [ ! -f "$ROOT_DIR/portfolio.toml" ]; then
    error "未找到持仓文件: $ROOT_DIR/portfolio.toml"
else
    info "持仓文件: $ROOT_DIR/portfolio.toml"
fi

if [ -n "$EQUITY" ]; then
    info "总权益: ¥$(printf "%'.0f" "$EQUITY") (命令行指定)"
else
    info "总权益: 从 portfolio.toml 读取"
fi
echo ""

# ── 运行风控检查 ──
cd "$ROOT_DIR"
if [ -n "$EQUITY" ]; then
    python3 risk_control/scripts/risk_report.py --equity "$EQUITY"
else
    python3 risk_control/scripts/risk_report.py
fi

echo ""
echo "=========================================="
echo "  完成！报告已保存到 output/"
echo "=========================================="
