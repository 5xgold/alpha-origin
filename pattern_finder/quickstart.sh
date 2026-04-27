#!/bin/bash
# Pattern-Finder Module Entry Script
# 形态相似检索模块入口脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Activate virtual environment if exists
if [ -d "../.venv" ]; then
    source ../.venv/bin/activate
fi

# Usage function
usage() {
    cat << EOF
用法: ./quickstart.sh <command> [options]

命令:
  build <stocks>     构建样本库
                     stocks: 股票代码列表，逗号分隔
                     示例: ./quickstart.sh build 600519,000001,000858

  query <stock>      查询单只股票的相似案例
                     stock: 股票代码
                     示例: ./quickstart.sh query 600519

  scan               扫描当前持仓的形态信号
                     读取 ../risk_control/data/portfolio.csv
                     示例: ./quickstart.sh scan

  demo               演示模式（使用模拟数据）
                     示例: ./quickstart.sh demo

选项:
  --start YYYYMMDD   开始日期（默认：20200101）
  --end YYYYMMDD     结束日期（默认：今天）
  --rebuild          强制重建样本库
  --output PATH      指定输出报告路径

示例:
  # 构建样本库（茅台、平安、五粮液）
  ./quickstart.sh build 600519,000001,000858 --start 20200101

  # 查询茅台的相似案例
  ./quickstart.sh query 600519

  # 扫描当前持仓
  ./quickstart.sh scan

  # 演示模式
  ./quickstart.sh demo
EOF
    exit 1
}

# Check arguments
if [ $# -lt 1 ]; then
    usage
fi

COMMAND=$1
shift

# Default parameters
START_DATE="20200101"
END_DATE=$(date +%Y%m%d)
REBUILD=""
OUTPUT=""

# Parse options
while [[ $# -gt 0 ]]; do
    case $1 in
        --start)
            START_DATE="$2"
            shift 2
            ;;
        --end)
            END_DATE="$2"
            shift 2
            ;;
        --rebuild)
            REBUILD="--rebuild"
            shift
            ;;
        --output)
            OUTPUT="--output $2"
            shift 2
            ;;
        *)
            POSITIONAL_ARGS+=("$1")
            shift
            ;;
    esac
done

# Restore positional arguments
set -- "${POSITIONAL_ARGS[@]}"

# Execute command
case $COMMAND in
    build)
        if [ -z "$1" ]; then
            echo "错误：请指定股票代码列表"
            echo "示例: ./quickstart.sh build 600519,000001,000858"
            exit 1
        fi
        STOCKS="$1"
        echo "=== 构建样本库 ==="
        echo "股票列表: $STOCKS"
        echo "时间范围: $START_DATE ~ $END_DATE"
        python3 scripts/library_builder.py \
            --stocks "$STOCKS" \
            --start "$START_DATE" \
            --end "$END_DATE" \
            $REBUILD
        ;;

    query)
        if [ -z "$1" ]; then
            echo "错误：请指定股票代码"
            echo "示例: ./quickstart.sh query 600519"
            exit 1
        fi
        STOCK="$1"
        echo "=== 查询股票形态 ==="
        echo "股票代码: $STOCK"
        python3 scripts/pattern_search.py \
            --stock "$STOCK" \
            --start "$START_DATE" \
            --end "$END_DATE" \
            $OUTPUT
        ;;

    scan)
        echo "=== 扫描持仓形态 ==="
        PORTFOLIO="../risk_control/data/portfolio.csv"
        if [ ! -f "$PORTFOLIO" ]; then
            echo "错误：持仓文件不存在: $PORTFOLIO"
            echo "请先运行风控模块生成持仓文件"
            exit 1
        fi
        python3 scripts/pattern_search.py \
            --scan "$PORTFOLIO" \
            --start "$START_DATE" \
            --end "$END_DATE" \
            $OUTPUT
        ;;

    demo)
        echo "=== 演示模式（模拟数据）==="
        python3 scripts/pattern_search.py --demo $OUTPUT
        ;;

    *)
        echo "错误：未知命令 '$COMMAND'"
        usage
        ;;
esac

echo ""
echo "✅ 完成"
