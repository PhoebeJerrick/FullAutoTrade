#!/bin/bash

# ---------------------------------------------------------
# 终极简化命令 (动态映射版)
# 用法: ./t [1|2|3...] 或 ./t [账号名]
# 自动将数字映射到 trade_config.py 中的账号顺序
# ---------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_SCRIPT="$SCRIPT_DIR/start_trading.sh"

# 1. 同样动态获取账号列表
GET_ACCOUNTS_CMD="import sys; sys.path.append('$SCRIPT_DIR'); from trade_config import ACCOUNT_SYMBOL_MAPPING; print(' '.join(ACCOUNT_SYMBOL_MAPPING.keys()))"
ACCOUNTS_STRING=$(python -c "$GET_ACCOUNTS_CMD" 2>/dev/null)
IFS=' ' read -r -a ACC_LIST <<< "$ACCOUNTS_STRING"

# 获取账号名称的函数 (通过索引)
get_acc_by_index() {
    local idx=$(($1 - 1)) # 数组从0开始，用户输入从1开始
    if [ $idx -ge 0 ] && [ $idx -lt ${#ACC_LIST[@]} ]; then
        echo "${ACC_LIST[$idx]}"
    else
        echo ""
    fi
}

show_usage() {
    echo "=== 极简交易命令 (t) ==="
    echo "自动识别到以下账号:"
    local i=1
    for acc in "${ACC_LIST[@]}"; do
        echo "  $i) $acc"
        ((i++))
    done
    echo ""
    echo "用法:"
    echo "  ./t [序号]    -> 启动对应账号 (例: ./t 1)"
    echo "  ./t [名称]    -> 启动指定名称 (例: ./t okxNew)"
    echo "  ./t s         -> 查看状态"
    echo "  ./t k [序号]  -> 停止对应账号"
    echo "  ./t k a       -> 停止所有"
}

CMD=$1
PARAM=$2

case "$CMD" in
    "s"|"status")
        "$MAIN_SCRIPT" status
        ;;
    "k"|"stop")
        if [ "$PARAM" = "a" ] || [ "$PARAM" = "all" ]; then
            "$MAIN_SCRIPT" stop all
        else
            # 尝试解析数字
            TARGET_ACC=$(get_acc_by_index "$PARAM")
            if [ -n "$TARGET_ACC" ]; then
                "$MAIN_SCRIPT" stop "$TARGET_ACC"
            else
                # 如果不是数字或越界，直接当作名称
                "$MAIN_SCRIPT" stop "$PARAM"
            fi
        fi
        ;;
    "help"|"-h"|"")
        show_usage
        ;;
    *)
        # 启动逻辑
        # 1. 尝试按数字查找
        TARGET_ACC=$(get_acc_by_index "$CMD")
        
        if [ -n "$TARGET_ACC" ]; then
            # 如果是有效数字 (./t 1)
            "$MAIN_SCRIPT" "$TARGET_ACC"
        else
            # 如果不是数字，直接传参 (./t okxNew)
            "$MAIN_SCRIPT" "$CMD"
        fi
        ;;
esac