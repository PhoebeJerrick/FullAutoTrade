#!/bin/bash

# ---------------------------------------------------------
# 智能交易启动脚本 (动态版)
# 无需修改此文件即可支持新账号
# ---------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_START_SCRIPT="$SCRIPT_DIR/start.sh"

# 1. 动态从 Python 配置中获取所有账号名称
# 这行命令会读取 trade_config.py 中的 keys 并以空格分隔返回
GET_ACCOUNTS_CMD="import sys; sys.path.append('$SCRIPT_DIR'); from trade_config import ACCOUNT_SYMBOL_MAPPING; print(' '.join(ACCOUNT_SYMBOL_MAPPING.keys()))"
ACCOUNTS_STRING=$(python -c "$GET_ACCOUNTS_CMD" 2>/dev/null)

# 如果获取失败（比如python环境问题），回退到默认
if [ -z "$ACCOUNTS_STRING" ]; then
    ACCOUNTS_STRING="default okxMain okxSub1"
fi

# 转为数组
IFS=' ' read -r -a ALL_ACCOUNTS <<< "$ACCOUNTS_STRING"

show_usage() {
    echo "=== 量化交易程序启动脚本 (自动识别) ==="
    echo "用法: $0 [账号名称|命令]"
    echo ""
    echo "当前已配置的账号:"
    for acc in "${ALL_ACCOUNTS[@]}"; do
        echo "  $acc"
    done
    echo ""
    echo "命令选项:"
    echo "  status           查看所有账号状态"
    echo "  stop [账号|all]  停止指定账号"
    echo "  help             显示此帮助信息"
}

# 检查主启动脚本
check_main_script() {
    if [ ! -f "$MAIN_START_SCRIPT" ]; then
        echo "错误: 找不到主启动脚本: $MAIN_START_SCRIPT"
        exit 1
    fi
}

# 启动指定账号
start_account() {
    local account=$1
    # 检查账号是否在配置中 (可选，为了灵活性也可以不强校验)
    if [[ ! " ${ALL_ACCOUNTS[*]} " =~ " ${account} " ]] && [ "$account" != "default" ]; then
        echo "⚠️  警告: 账号 '$account' 未在 trade_config.py 中定义，但尝试启动..."
    fi
    
    echo "🚀 启动交易账号: $account"
    "$MAIN_START_SCRIPT" "$account"
}

# 查看状态 (动态循环)
show_status() {
    echo "=== 交易账号状态检查 ==="
    echo "检测范围: ${ALL_ACCOUNTS[*]}"
    echo ""
    
    for account in "${ALL_ACCOUNTS[@]}"; do
        echo "🔍 检查 $account:"
        
        # 检查进程
        PID=$(ps aux | grep "python.*ds_perfect.py $account" | grep -v grep | awk '{print $2}')
        if [ -n "$PID" ]; then
            echo "   ✅ 运行中 (PID: $PID)"
            
            # 检查日志
            LOG_DIR="/AutoQuant/Projects/deepseek/Output/$account"
            LATEST_LOG=$(ls -t "$LOG_DIR"/${account}_*.log 2>/dev/null | head -1)
            if [ -n "$LATEST_LOG" ]; then
                LOG_BASENAME=$(basename "$LATEST_LOG")
                # 获取最后一条非空日志
                LAST_MSG=$(grep -v "^$" "$LATEST_LOG" | tail -1 | cut -c 1-100)
                echo "   📄 日志: $LOG_BASENAME"
                echo "   📝 最新: $LAST_MSG..."
            fi
        else
            echo "   ❌ 未运行"
        fi
        echo ""
    done
    
    echo "=== 系统资源 TOP 3 ==="
    ps aux --sort=-%mem | head -4 | awk '{if(NR>1) print "💻 " $11 " | Mem: " $4 "% | CPU: " $3 "%"}'
}

# 停止账号
stop_account() {
    local account=$1
    if [ "$account" = "all" ]; then
        echo "🛑 停止所有交易进程..."
        pkill -f "ds_perfect.py"
    else
        echo "🛑 停止账号: $account"
        PID=$(ps aux | grep "python.*ds_perfect.py $account" | grep -v grep | awk '{print $2}')
        if [ -n "$PID" ]; then
            kill $PID
            echo "✅ 已发送停止信号 (PID: $PID)"
        else
            echo "ℹ️  未运行"
        fi
    fi
}

# 主逻辑
main() {
    local cmd=$1
    local sub_cmd=$2
    
    case $cmd in
        ""|"help"|"-h")
            show_usage
            ;;
        "status"|"s")
            show_status
            ;;
        "stop"|"k")
            if [ -z "$sub_cmd" ]; then
                echo "请指定账号: $0 stop [AccountName|all]"
                exit 1
            fi
            stop_account "$sub_cmd"
            ;;
        *)
            # 如果不是命令，则视为账号名称，直接启动
            check_main_script
            start_account "$cmd"
            ;;
    esac
}

main "$@"