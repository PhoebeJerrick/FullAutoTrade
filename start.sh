#!/bin/bash

PROJECT_DIR="/AutoQuant/Projects/deepseek/FullAutoTrade"
LOG_FILE="TradeOutput.log"

cd $PROJECT_DIR

# 方法1：使用 conda run（最可靠）
nohup conda run -n ds python ds_perfect.py > $LOG_FILE 2>&1 &

# 或者方法2：使用完整的 conda 激活
# source /root/miniconda3/etc/profile.d/conda.sh
# conda activate ds
# nohup python ds_perfect.py > $LOG_FILE 2>&1 &

echo "✅ 量化程序已启动，PID: $!"
echo "📁 项目目录: $(pwd)"
echo "📝 日志文件: $LOG_FILE"
echo "🐍 Python路径: $(which python)"