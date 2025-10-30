
#!/bin/bash

PROJECT_DIR="/AutoQuant/Projects/deepseek/FullAutoTrade"
LOG_FILE="TradeOutput.log"

# 直接激活 deepseek 环境（根据你的实际情况选择一种）
# source ~/miniconda3/etc/profile.d/conda.sh
conda activate ds

cd $PROJECT_DIR

# 或者如果是虚拟环境
# source venv/bin/activate

nohup python ds_perfect.py > $LOG_FILE 2>&1 &
echo "✅ 量化程序已启动，PID: $!"
echo "Python路径: $(which python)"

