#!/bin/bash

# 默认账号配置
DEFAULT_ACCOUNT="default"
ACCOUNT=${1:-$DEFAULT_ACCOUNT}

PROJECT_DIR="/AutoQuant/Projects/deepseek/FullAutoTrade"
OUTPUT_DIR="/AutoQuant/Projects/deepseek/Output/$ACCOUNT"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
DATE_ONLY=$(date +"%Y%m%d")
# 日志文件名称格式: 账号_日期_时间.log
LOG_FILE="${ACCOUNT}_${TIMESTAMP}.log"
FULL_LOG_PATH="$OUTPUT_DIR/$LOG_FILE"

echo "=== Quantitative Trading Program Startup ==="
echo "Account: $ACCOUNT"
echo "Project Directory: $PROJECT_DIR"
echo "Output Directory: $OUTPUT_DIR"
echo "Log File: $LOG_FILE"
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

# Clean up old log files (keep latest 10) - 更新为按账号名称匹配
echo "Cleaning up old log files for account: $ACCOUNT..."
ls -t "$OUTPUT_DIR"/${ACCOUNT}_*.log 2>/dev/null | tail -n +11 | xargs -r rm -f

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

# Create new log file with header information
echo "Creating log file: $FULL_LOG_PATH"
cat > "$FULL_LOG_PATH" << EOF
==============================================
DeepSeek Quantitative Trading Program - Startup Log
==============================================
Account:       $ACCOUNT
Start Time:    $(date)
Program File:  ds_perfect.py
Project Dir:   $PROJECT_DIR
Output Dir:    $OUTPUT_DIR
Python Env:    $(which python)
Conda Env:     $CONDA_DEFAULT_ENV
Log File:      $LOG_FILE
==============================================

EOF

echo "Starting main program for account: $ACCOUNT..."
# Start program with unbuffered mode and account parameter
nohup python -u ds_perfect.py $ACCOUNT >> "$FULL_LOG_PATH" 2>&1 &
PID=$!

echo "Program started, PID: $PID"

# Wait and verify startup status
sleep 5

echo "=== Startup Verification ==="
if ps -p $PID > /dev/null; then
    echo "Process running normally (PID: $PID)"
    
    # Check initial log output
    if [ -f "$FULL_LOG_PATH" ]; then
        LOG_LINES=$(wc -l < "$FULL_LOG_PATH")
        echo "Log file created, current lines: $LOG_LINES"
        
        if [ $LOG_LINES -gt 10 ]; then
            echo "Recent log output:"
            tail -5 "$FULL_LOG_PATH"
        else
            echo "Log content is minimal, program may still be initializing"
        fi
    else
        echo "Log file does not exist: $FULL_LOG_PATH"
    fi
else
    echo "Process startup failed"
    if [ -f "$FULL_LOG_PATH" ]; then
        echo "Check error information:"
        tail -20 "$FULL_LOG_PATH"
    fi
    exit 1
fi

echo ""
echo "Quantitative trading program startup completed!"
echo "Account: $ACCOUNT"
echo "View real-time logs: tail -f '$FULL_LOG_PATH'"
echo "Log file location: $FULL_LOG_PATH"
echo "Process ID: $PID"
echo "Start time: $(date)"