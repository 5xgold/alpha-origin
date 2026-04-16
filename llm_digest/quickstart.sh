#!/bin/bash
# LLM 信息压缩模块 - 独立入口
#
# 用法:
#   ./llm_digest/quickstart.sh review [股票代码]     # 交易复盘
#   ./llm_digest/quickstart.sh brief [日期]           # 每日简报
#   ./llm_digest/quickstart.sh earnings <PDF> <代码>  # 财报摘要

set -e

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

source "$ROOT_DIR/.venv/bin/activate" 2>/dev/null || true

CMD="${1:-}"
shift 2>/dev/null || true

case "$CMD" in
    review)
        args=""
        [ -n "$1" ] && args="--code $1"
        python3 llm_digest/scripts/trade_review.py $args
        ;;
    brief)
        args=""
        [ -n "$1" ] && args="--date $1"
        python3 llm_digest/scripts/daily_brief.py $args
        ;;
    earnings)
        [ -z "$1" ] && echo "用法: ./llm_digest/quickstart.sh earnings <PDF> <股票代码>" && exit 1
        [ -z "$2" ] && echo "用法: ./llm_digest/quickstart.sh earnings <PDF> <股票代码>" && exit 1
        python3 llm_digest/scripts/earnings_summary.py --input "$1" --code "$2"
        ;;
    *)
        echo "LLM 信息压缩模块"
        echo ""
        echo "用法:"
        echo "  ./llm_digest/quickstart.sh review [股票代码]     # 交易复盘"
        echo "  ./llm_digest/quickstart.sh brief [日期]           # 每日简报"
        echo "  ./llm_digest/quickstart.sh earnings <PDF> <代码>  # 财报摘要"
        exit 1
        ;;
esac
