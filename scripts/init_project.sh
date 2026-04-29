#!/bin/bash
# 项目初始化脚本 — 一键生成用户配置文件
#
# 用法: ./scripts/init_project.sh
#
# 生成的文件（均已在 .gitignore 中，不会被提交）：
#   .env                          — API 密钥
#   portfolio.toml                — 持仓 + 账户信息
#   attribution_analysis/data/    — 归因分析数据目录
#   risk_control/data/            — 风控数据目录

set -e

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
skip()  { echo -e "    $1 已存在，跳过"; }

echo "========================================"
echo "  项目初始化"
echo "========================================"
echo ""

# 1. .env
if [ ! -f "$ROOT_DIR/.env" ]; then
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    info ".env 已创建，请填入你的 API 密钥"
    warn "  编辑: $ROOT_DIR/.env"
else
    skip ".env"
fi

# 2. portfolio.toml
if [ ! -f "$ROOT_DIR/portfolio.toml" ]; then
    cp "$ROOT_DIR/portfolio.toml.example" "$ROOT_DIR/portfolio.toml"
    info "portfolio.toml 已创建，请修改为你的实际持仓"
    warn "  编辑: $ROOT_DIR/portfolio.toml"
else
    skip "portfolio.toml"
fi

# 3. 数据目录
for dir in "attribution_analysis/data" "risk_control/data" "output" "data/cache"; do
    mkdir -p "$ROOT_DIR/$dir"
done
info "数据目录已就绪"

echo ""
echo "========================================"
echo "  初始化完成"
echo "========================================"
echo ""
echo "下一步："
echo "  1. 编辑 .env          填入 TS_TOKEN 等密钥"
echo "  2. 编辑 portfolio.toml 填入持仓和账户信息"
echo "  3. 运行 ./quickstart.sh risk  执行风控检查"
echo ""
echo "所有个人数据文件均在 .gitignore 中，不会被提交到 git。"
