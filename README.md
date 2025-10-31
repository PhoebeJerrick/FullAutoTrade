## FullAutoTrade
## It is a fully automatically trading strategy in cripto area with deepseek and Okx/Binance platform.

## Isolated position

## configuration

### For config file,please put it on the other file locations.

### config.env

####  DEEPSEEK_API_KEY= #your deepseek  api

####  BINANCE_API_KEY=

####  BINANCE_SECRET=

####  OKX_API_KEY=

####  OKX_SECRET=

#### OKX_PASSWORD=


### 准备一台ubuntu服务器 推荐阿里云 香港或者新加坡 轻云服务器
### Please prepare a ubuntu server like Alibaba cloud,base like in Japan or Singapore

#### wget https://repo.anaconda.com/archive/Anaconda3-2024.10-1-Linux-x86_64.sh

#### bash Anaconda3-2024.10-1-Linux-x86_64.sh

#### source /root/anaconda3/etc/profile.d/conda.sh 
#### echo ". /root/anaconda3/etc/profile.d/conda.sh" >> ~/.bashrc

#### conda create -n ds python=3.10

#### conda activate ds

#### pip install -r requirements.txt

#### apt-get update //Update package mirror sources


#### apt-get upgrade //update the necessary libs


#### apt install npm // install npm


#### npm install pm2 -g //npm to install pm2

#### conda create -n trail3 python=4.10

## Start your application in shell.
# 设置执行权限-每次只要更改过其中一个文件就需要设置一次(其中t没有文件后缀)；
# chmod +x start.sh start_trading.sh t

# 启动账号
# ./t 1        # 启动账号1
# ./t 2        # 启动账号2
# ./t d        # 启动默认账号

# 查看状态
# ./t s        # 查看所有账号状态

# 停止账号
# ./t k 1      # 停止账号1
# ./t k 2      # 停止账号2  
# ./t k a      # 停止所有账号

# 帮助信息
# ./t help     # 显示帮助

# 完整的文件结构
# /AutoQuant/Projects/deepseek/FullAutoTrade/
# ├── start.sh                 # 底层启动脚本
# ├── start_trading.sh         # 完整管理脚本
# ├── t                        # 极简命令脚本（推荐日常使用）
# ├── trade                    # 简化命令脚本（备用）
# └── ds_perfect.py            # 主程序文件


###### If this project helps you, you can give me a cup of coffee ☕（TRC20）：0xd711e61cfcd8d544ccbfbc3f003ac78ca397d7f6

#### 打赏地址（TRC20）：0xd711e61cfcd8d544ccbfbc3f003ac78ca397d7f6

