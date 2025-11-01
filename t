#!/bin/bash

# 终极简化交易命令 - 文件名: t
# 用法:
#   ./t 1        # 启动okxMain
#   ./t 2        # 启动okxSub1  
#   ./t s        # 查看状态
#   ./t k 1      # 停止okxMain
#   ./t k a      # 停止所有

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_SCRIPT="$SCRIPT_DIR/start_trading.sh"

show_usage() {
    echo "=== 极简交易命令 (t) ==="
    echo "启动:    ./t [1|2|d]"
    echo "状态:    ./t s" 
    echo "停止:    ./t k [1|2|d|a]"
    echo ""
    echo "示例:"
    echo "  ./t 1     # 启动okxMain"
    echo "  ./t s     # 查看状态"
    echo "  ./t k 1   # 停止okxMain"
    echo "  ./t k a   # 停止所有"
}

case "$1" in
    "1")
        echo "🚀 启动okxMain..."
        "$MAIN_SCRIPT" "okxMain"
        ;;
    "2") 
        echo "🚀 启动okxSub1..."
        "$MAIN_SCRIPT" "okxSub1"
        ;;
    "d"|"default")
        echo "🚀 启动默认账号..."
        "$MAIN_SCRIPT" "default"
        ;;
    "s"|"status")
        "$MAIN_SCRIPT" "status"
        ;;
    "k"|"stop")
        case "$2" in
            "1") 
                echo "🛑 停止okxMain..."
                "$MAIN_SCRIPT" "stop" "okxMain" 
                ;;
            "2")
                echo "🛑 停止okxSub1..." 
                "$MAIN_SCRIPT" "stop" "okxSub1"
                ;;
            "d"|"default")
                echo "🛑 停止默认账号..."
                "$MAIN_SCRIPT" "stop" "default"
                ;;
            "a"|"all")
                echo "🛑 停止所有账号..."
                "$MAIN_SCRIPT" "stop" "all"
                ;;
            *)
                echo "错误: 用法: ./t k [1|2|d|a]"
                echo "  1=okxMain, 2=okxSub1, d=默认, a=全部"
                ;;
        esac
        ;;
    "help"|"-h"|"--help")
        show_usage
        ;;
    *)
        if [ -z "$1" ]; then
            show_usage
        else
            echo "错误: 未知命令 '$1'"
            show_usage
        fi
        ;;
esac
