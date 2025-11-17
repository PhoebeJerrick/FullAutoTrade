# config_center.py

import sys
from typing import Optional

# 1. 声明全局变量，用于存储当前账户名
CURRENT_ACCOUNT: Optional[str] = None

def initialize_runtime_config():
    """
    解析命令行参数，设置 CURRENT_ACCOUNT。
    """
    global CURRENT_ACCOUNT
    
    # 检查命令行参数是否有传入账户名
    if len(sys.argv) > 1:
        # sys.argv[1] 就是启动脚本时传入的第一个参数 (例如 'okxMain')
        CURRENT_ACCOUNT = sys.argv[1]
    else:
        CURRENT_ACCOUNT = "default"
        
    print(f"Configuration Center initialized. Current Account: {CURRENT_ACCOUNT}")

# 2. 关键：在模块被导入时立即执行初始化
# 这样任何导入此模块的脚本，在执行后续代码前，CURRENT_ACCOUNT 都会被设置好。
initialize_runtime_config()

# 你可以在这里添加其他全局配置变量，如全局 API 密钥等