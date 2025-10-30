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
## python ds_perfect.py


## Start the application automatically in the back of the (ubuntu) server.
## chmod +x start.sh  //给执行权限
## ./start.sh  //运行脚本

## Stop the application in the back.
## Example1：use pid,for eg:12345
## kill 12345 

# Example1：use application name,for eg:ds_perfect.py
## pkill -f "python ds_perfect.py"


## To the application is actually on or not.
# ps aux | grep "ds_perfect.py"

## To see the real time logs
# tail -f TradeOutput.log

## Stop the application.
# pkill -f "python your_quant_program.py"


###### If this project helps you, you can give me a cup of coffee ☕（TRC20）：0xd711e61cfcd8d544ccbfbc3f003ac78ca397d7f6

#### 打赏地址（TRC20）：0xd711e61cfcd8d544ccbfbc3f003ac78ca397d7f6

