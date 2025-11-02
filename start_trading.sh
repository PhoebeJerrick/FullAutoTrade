#!/bin/bash

# 统一的交易程序启动脚本
# 用法: 
#   ./start_trading.sh                 # 启动默认账号
#   ./start_trading.sh okxMain        # 启动okxMain
#   ./start_trading.sh okxSub1        # 启动okxSub1
#   ./start_trading.sh status          # 查看所有账号状态
#   ./start_trading.sh stop okxMain   # 停止okxMain
#   ./start_trading.sh stop all        # 停止所有账号

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_START_SCRIPT="$SCRIPT_DIR/start.sh"

# 显示使用说明
show_usage() {
    echo "=== 量化交易程序启动脚本 ==="
    echo "用法: $0 [账号名称|命令]"
    echo ""
    echo "账号选项:"
    echo "  (空)             启动默认账号"
    echo "  okxMain         启动okxMain"
    echo "  okxSub1         启动okxSub1"
    echo "  default          启动默认账号"
    echo ""
    echo "命令选项:"
    echo "  status           查看所有账号状态"
    echo "  stop [账号]      停止指定账号 (stop all 停止所有)"
    echo "  help             显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0               # 启动默认账号"
    echo "  $0 okxMain      # 启动okxMain"
    echo "  $0 status        # 查看状态"
    echo "  $0 stop okxMain # 停止okxMain"
}

# 检查主启动脚本是否存在
check_main_script() {
    if [ ! -f "$MAIN_START_SCRIPT" ]; then
        echo "错误: 找不到主启动脚本: $MAIN_START_SCRIPT"
        exit 1
    fi
}

# 启动指定账号
start_account() {
    local account=$1
    echo "🚀 启动交易账号: $account"
    "$MAIN_START_SCRIPT" "$account"
}

# 查看所有账号状态
show_status() {
    echo "=== 交易账号状态检查 ==="
    echo ""
    
    # 检查进程状态
    declare -A accounts=(["default"]="默认账号" ["okxMain"]="okxMain" ["okxSub1"]="okxSub1")
    
    for account in "${!accounts[@]}"; do
        echo "🔍 检查 ${accounts[$account]} ($account):"
        
        # 修复：改进进程检查，考虑 -u 参数
        PID=$(ps aux | grep "python.*ds_perfect.py $account" | grep -v grep | awk '{print $2}')
        if [ -n "$PID" ]; then
            echo "   ✅ 运行中 (PID: $PID)"
            
            # 检查日志文件
            LOG_DIR="/AutoQuant/Projects/deepseek/Output/$account"
            LATEST_LOG=$(ls -t "$LOG_DIR"/${account}_*.log 2>/dev/null | head -1)
            if [ -n "$LATEST_LOG" ] && [ -f "$LATEST_LOG" ]; then
                LOG_SIZE=$(du -h "$LATEST_LOG" | cut -f1)
                LOG_LINES=$(wc -l < "$LATEST_LOG")
                LOG_BASENAME=$(basename "$LATEST_LOG")
                echo "   📄 日志: $LOG_BASENAME"
                echo "   📊 大小: $LOG_SIZE, 行数: $LOG_LINES"
                
                # 显示最后一条日志
                LAST_LOG=$(tail -1 "$LATEST_LOG" 2>/dev/null)
                if [ -n "$LAST_LOG" ]; then
                    echo "   📝 最后日志: $LAST_LOG"
                fi
            else
                echo "   ⚠️  找不到日志文件"
            fi
        else
            echo "   ❌ 未运行"
            
            # 显示最近的日志文件（即使进程不在运行）
            LOG_DIR="/AutoQuant/Projects/deepseek/Output/$account"
            RECENT_LOG=$(ls -t "$LOG_DIR"/${account}_*.log 2>/dev/null | head -1)
            if [ -n "$RECENT_LOG" ]; then
                LOG_BASENAME=$(basename "$RECENT_LOG")
                MOD_TIME=$(stat -c %y "$RECENT_LOG" 2>/dev/null | cut -d'.' -f1)
                echo "   📄 最近日志: $LOG_BASENAME (修改: $MOD_TIME)"
            fi
        fi
        echo ""
    done
    
    # 显示系统资源使用
    echo "=== 系统资源 ==="
    echo "💰 内存使用前5:"
    ps aux --sort=-%mem | head -6
    echo ""
    echo "💻 CPU使用前5:"
    ps aux --sort=-%cpu | head -6
}

# 停止指定账号
stop_account() {
    local account=$1
    
    if [ "$account" = "all" ]; then
        echo "🛑 停止所有交易账号..."
        pkill -f "ds_perfect.py"
        echo "✅ 已发送停止信号给所有交易进程"
        return
    fi
    
    echo "🛑 停止账号: $account"
    # 修复：改进进程检查，考虑 -u 参数
    PID=$(ps aux | grep "python.*ds_perfect.py $account" | grep -v grep | awk '{print $2}')
    
    if [ -n "$PID" ]; then
        echo "📋 找到进程 PID: $PID, 正在停止..."
        kill $PID
        sleep 2
        
        # 检查是否成功停止
        if ps -p $PID > /dev/null 2>&1; then
            echo "⚠️  进程仍在运行，强制停止..."
            kill -9 $PID
        fi
        
        echo "✅ 账号 $account 已停止"
    else
        echo "ℹ️  账号 $account 未在运行"
    fi
}

# 主逻辑
main() {
    local command=$1
    local sub_command=$2
    
    case $command in
        ""|"default")
            check_main_script
            start_account "default"
            ;;
        "okxMain")
            check_main_script
            start_account "okxMain"
            ;;
        "okxSub1")
            check_main_script
            start_account "okxSub1"
            ;;
        "status")
            show_status
            ;;
        "stop")
            if [ -z "$sub_command" ]; then
                echo "错误: 请指定要停止的账号 (okxMain, okxSub1, default 或 all)"
                echo "用法: $0 stop [账号|all]"
                exit 1
            fi
            stop_account "$sub_command"
            ;;
        "help"|"-h"|"--help")
            show_usage
            ;;
        *)
            echo "错误: 未知命令 '$command'"
            show_usage
            exit 1
            ;;
    esac
}

# 运行主函数
main "$@"


# 日常管理就用这一个脚本
# ./start_trading.sh okxMain      # 启动okxMain
# ./start_trading.sh status        # 查看状态
# ./start_trading.sh stop okxMain # 停止okxMain

