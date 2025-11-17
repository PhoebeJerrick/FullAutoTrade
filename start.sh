#!/bin/bash

# 默认账号配置
DEFAULT_ACCOUNT="default"
ACCOUNT=${1:-$DEFAULT_ACCOUNT}

PROJECT_DIR="/AutoQuant/Projects/deepseek/FullAutoTrade"
OUTPUT_DIR="/AutoQuant/Projects/deepseek/Output/$ACCOUNT"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DATE_ONLY=$(date +"%Y%m%d")
# ❌ 已移除日志文件名称和路径的定义，因为 Python 程序内部会生成日志

echo "=== Quantitative Trading Program Startup ==="
echo "Account: $ACCOUNT"
echo "Project Directory: $PROJECT_DIR"
echo "Output Directory: $OUTPUT_DIR"
# ❌ 已移除 Log File: $LOG_FILE 的打印
echo "Start Time: $(date)"
echo "User: $(whoami)"

# Create output directory (if not exists)
echo "Creating output directory..."
mkdir -p "$OUTPUT_DIR"
if [ $? -ne 0 ]; then
    echo "Error: Cannot create output directory: $OUTPUT_DIR"
    exit 1
fi

echo "Output directory created/exists: $OUTPUT_DIR"

# Switch to project directory
cd "$PROJECT_DIR" || { echo "Error: Cannot enter project directory"; exit 1; }
echo "Current directory: $(pwd)"

# ❌ 已移除清理旧 log 文件的逻辑
# echo "Cleaning up old log files for account: $ACCOUNT..."
# ls -t "$OUTPUT_DIR"/${ACCOUNT}_*.log 2>/dev/null | tail -n +11 | xargs -r rm -f

# Fix the location of conda. Check which location is valid.
if [ -f "/root/anaconda3/etc/profile.d/conda.sh" ]; then
    echo "Using anaconda3 path..."
    source /root/anaconda3/etc/profile.d/conda.sh
elif [ -f "/root/miniconda3/etc/profile.d/conda.sh" ]; then
    echo "Using miniconda3 path..."
    source /root/miniconda3/etc/profile.d/conda.sh
else
    echo "Error: Cannot find conda.sh"
    exit 1
fi

# Activate conda environment
echo "Activating Python environment..."
conda activate ds

echo "Python Path: $(which python)"
echo "Conda Environment: $CONDA_DEFAULT_ENV"

# Stop any existing old processes for this account
echo "Checking and stopping old processes for account: $ACCOUNT..."
OLD_PID=$(ps aux | grep "python.*ds_perfect.py $ACCOUNT" | grep -v grep | awk '{print $2}')
if [ -n "$OLD_PID" ]; then
    echo "Found old process PID: $OLD_PID, stopping..."
    kill $OLD_PID
    sleep 2
fi

# ❌ 已移除创建日志文件头的逻辑
# echo "Creating log file: $FULL_LOG_PATH"
# cat > "$FULL_LOG_PATH" << EOF
# ... (Header content)
# EOF

echo "Starting main program for account: $ACCOUNT..."
# Start program with unbuffered mode and account parameter
# ✅ 修改点：将标准输出和错误输出重定向到 /dev/null (丢弃)，防止生成第二个日志文件
nohup python -u ds_perfect.py $ACCOUNT > /dev/null 2>&1 &
PID=$!

echo "Program started, PID: $PID"

# Wait and verify startup status
sleep 5

echo "=== Startup Verification ==="
if ps -p $PID > /dev/null; then
    echo "Process running normally (PID: $PID)"

else
    echo "Process startup failed"
    exit 1
fi

echo ""
echo "Quantitative trading program startup completed!"
echo "Account: $ACCOUNT"
echo "Process ID: $PID"
echo "Start time: $(date)"