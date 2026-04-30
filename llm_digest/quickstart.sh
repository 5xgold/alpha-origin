#!/bin/bash
# LLM 信息压缩模块 - 独立入口
#
# 用法:
#   ./llm_digest/quickstart.sh review [股票代码]       # 交易复盘
#   ./llm_digest/quickstart.sh daily-review [总权益] [日期]   # 每日复盘
#   ./llm_digest/quickstart.sh daily-pack [总权益] [日期]     # 每日复盘 + 图表
#   ./llm_digest/quickstart.sh earnings <PDF> <代码>   # 财报摘要

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
    daily-review)
        args=""
        [ -n "$1" ] && args="--equity $1"
        [ -n "$2" ] && args="$args --date $2"
        python3 llm_digest/scripts/daily_review.py $args
        ;;
    daily-pack)
        args=""
        [ -n "$1" ] && args="--equity $1"
        [ -n "$2" ] && args="$args --date $2"
        python3 llm_digest/scripts/daily_review.py $args
        chart_args=""
        [ -n "$2" ] && chart_args="--date $2"
        python3 scripts/gen_review_charts.py $chart_args
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
        echo "  ./llm_digest/quickstart.sh review [股票代码]       # 交易复盘"
        echo "  ./llm_digest/quickstart.sh daily-review [总权益] [日期]   # 每日复盘"
        echo "  ./llm_digest/quickstart.sh daily-pack [总权益] [日期]     # 每日复盘 + 图表"
        echo "  ./llm_digest/quickstart.sh earnings <PDF> <代码>   # 财报摘要"
        exit 1
        ;;
esac
