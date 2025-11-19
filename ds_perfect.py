import os
import time
import base64
import hmac
import hashlib
import sys
import math
import uuid
from functools import wraps
from typing import Dict, Any, Optional, List, Tuple, Any, Union
import schedule
from openai import OpenAI
import ccxt
import pandas as pd
import numpy as np
import re
from dotenv import load_dotenv
import json
import requests
from datetime import datetime, timedelta
from ai.ai_ds import analyze_with_deepseek, get_deepseek_analyzer
from strategy.st_sl_tp import get_sl_tp_strategy, initialize_sl_tp_strategy
from strategy.st_config_manager import get_config_manager
from strategy.st_optimizer import StrategyOptimizer
from trade_config import (
    TradingConfig, 
    MULTI_SYMBOL_CONFIGS, 
    print_version_banner,
    ACCOUNT_SYMBOL_MAPPING,
    ACCOUNT_ENV_SUFFIX
)

#导入配置中心 (必须在导入 trade_logger之前，但因为 config_center.py 是自初始化的，顺序不严格)
from cmd_config import CURRENT_ACCOUNT

# Trading parameter configuration - combining advantages of both versions
from trade_config import (TradingConfig, 
                          MULTI_SYMBOL_CONFIGS, 
                          print_version_banner,
                          ACCOUNT_SYMBOL_MAPPING) # ✅ 仅导入类和字典
# Global logger
from trade_logger import logger

# --- NEW: Global Variables for Multi-Symbol ---
# 全局变量，用于保存所有交易品种的配置实例
SYMBOL_CONFIGS: Dict[str, TradingConfig] = {}
# 当前活跃的交易品种（在 trading_bot 中设置，用于日志和调试）
CURRENT_SYMBOL: Optional[str] = None

POSITION_STATE_FILE = f'../Output/{CURRENT_ACCOUNT}/position_state.json'

# AI symbol analyzers save
SYMBOL_ANALYZERS = {}

# 全局止盈止损策略实例
strategy_optimizer = None
sl_tp_strategy = None

# Global variables to store historical data
price_history = {}
signal_history = {}
#1: 在启动时尝试加载仓位状态，如果失败则为 None
position = None

# 全局变量 - 记录每个品种的加仓状态
SCALING_HISTORY: Dict[str, Dict] = {}
# 添加全局变量来存储持仓历史
POSITION_HISTORY: Dict[str, List[Dict]] = {}

# Use relative path
env_path = '../ExApiConfig/ExApiConfig.env'  # .env file in config folder of parent directory
logger.log_info(f"📁Add config file: {env_path}")
load_dotenv(dotenv_path=env_path)

# Initialize DeepSeek client with error handling
deepseek_client = None

# 在文件顶部添加这些函数
def get_timeframe_seconds(timeframe: str) -> int:
    """将时间帧转换为秒数"""
    timeframe_seconds = {
        '1m': 60,
        '5m': 300,
        '15m': 900,
        '1h': 3600,
        '4h': 14400,
        '1d': 86400
    }
    return timeframe_seconds.get(timeframe, 900)  # 默认15分钟

def calculate_next_execution_time(symbol: str) -> float:
    """计算品种的下一个执行时间（对齐到K线周期）"""
    config = SYMBOL_CONFIGS[symbol]
    timeframe_seconds = get_timeframe_seconds(config.timeframe)
    
    # 获取当前时间
    now = datetime.now()
    current_timestamp = now.timestamp()
    
    # 计算当前K线周期的开始时间
    current_candle_start = (current_timestamp // timeframe_seconds) * timeframe_seconds
    
    # 下一个执行时间 = 当前K线周期开始时间 + K线周期 + 延迟（确保K线闭合）
    next_execution = current_candle_start + timeframe_seconds + 10  # 延迟10秒确保K线闭合
    
    # 如果当前时间已经超过计算的下个执行时间（由于处理延迟），调整到下个周期
    if current_timestamp >= next_execution:
        next_execution += timeframe_seconds
    
    return next_execution

def format_time_until_next_execution(next_execution: float) -> str:
    """格式化距离下次执行的时间"""
    now = time.time()
    seconds_until = next_execution - now
    
    if seconds_until <= 0:
        return "立即执行"
    elif seconds_until < 60:
        return f"{int(seconds_until)}秒后"
    elif seconds_until < 3600:
        return f"{int(seconds_until/60)}分钟后"
    else:
        return f"{int(seconds_until/3600)}小时后"

def get_scheduling_status() -> dict:
    """获取当前调度状态"""
    status = {
        'total_symbols': len(symbol_schedules) if 'symbol_schedules' in globals() else 0,
        'active_schedules': [],
        'next_execution': None,
        'status': 'running'
    }
    
    if 'symbol_schedules' in globals():
        current_time = time.time()
        for symbol, schedule in symbol_schedules.items():
            time_until = schedule['next_execution'] - current_time
            status['active_schedules'].append({
                'symbol': get_base_currency(symbol),
                'timeframe': schedule['timeframe'],
                'next_execution': schedule['next_execution'],
                'time_until': time_until,
                'execution_count': schedule.get('execution_count', 0)
            })
        
        # 找到最近的下次执行时间
        if status['active_schedules']:
            next_exec = min([s['next_execution'] for s in status['active_schedules']])
            status['next_execution'] = next_exec
            status['time_until_next'] = next_exec - current_time
    
    return status

def log_scheduling_status():
    """记录调度状态"""
    status = get_scheduling_status()
    logger.log_info(f"📊 调度状态: {status['total_symbols']}个品种监控中")
    
    for schedule in status['active_schedules']:
        if schedule['time_until'] <= 300:  # 只显示5分钟内的
            time_str = format_time_until_next_execution(schedule['next_execution'])
            logger.log_info(f"  {schedule['symbol']}: {time_str} ({schedule['timeframe']})")


def get_base_currency(symbol: str) -> str:
    """
    将完整的交易品种名称（例如 'BTC/USDT:USDT'）转换为基础货币简称（例如 'BTC'）。
    """
    try:
        # 使用 '/' 分割字符串，并取第一个部分
        base_currency = symbol.split('/')[0]
        return base_currency
    except Exception:
        # 如果分割失败（例如输入不包含 '/'），则返回原始字符串
        return symbol

# 根据账号选择对应的环境变量
def get_account_config(account_name):
    """
    动态获取账号配置，无需修改代码逻辑。
    它会根据 trade_config.py 中的 ACCOUNT_ENV_SUFFIX 自动查找环境变量。
    """
    # 1. 获取该账号对应的后缀，如果没有定义，默认使用空字符串
    suffix = ACCOUNT_ENV_SUFFIX.get(account_name, "")
    
    # 2. 动态拼接环境变量名
    # 例如: 如果后缀是 "_1"，则查找 OKX_API_KEY_1
    # 如果后缀是 ""，则查找 OKX_API_KEY
    api_key = os.getenv(f'OKX_API_KEY{suffix}')
    secret = os.getenv(f'OKX_SECRET{suffix}')
    password = os.getenv(f'OKX_PASSWORD{suffix}')

    # 3. 检查是否成功获取
    if not api_key:
        # 尝试回退：如果找不到带后缀的，尝试找不带后缀的作为默认值
        # 或者是为了兼容 default 账号
        if account_name == 'default':
             api_key = os.getenv('OKX_API_KEY')
             secret = os.getenv('OKX_SECRET')
             password = os.getenv('OKX_PASSWORD')
        
        if not api_key:
            # 记录严重的配置错误日志，但这里无法使用 logger (可能还没初始化)，使用 print
            print(f"❌ 严重错误: 无法找到账号 '{account_name}' 的环境变量 (后缀: '{suffix}')")
            print(f"请检查 .env 文件中是否存在 OKX_API_KEY{suffix}")

    return {
        'api_key': api_key,
        'secret': secret,
        'password': password
    }

# 获取当前账号配置
account_config = get_account_config(CURRENT_ACCOUNT)
print(f"🔑 账号配置加载: API_KEY={account_config['api_key'][:10]}...")

def create_order_tag():
    """创建与现有持仓兼容的订单标签"""
    # 使用与现有持仓相同的标签格式
    return '60bb4a8d3416BCDE'  # 简化为原有格式


# 初始化交易所 - 使用动态配置
exchange = ccxt.okx({
    'options': {
        'defaultType': 'swap',
    },
    'apiKey': account_config['api_key'],
    'secret': account_config['secret'],
    'password': account_config['password'],
})

# 1. 根据当前账号选择要交易的品种列表
symbols_to_trade_raw = ACCOUNT_SYMBOL_MAPPING.get(CURRENT_ACCOUNT, [])
# 2. 从 MULTI_SYMBOL_CONFIGS 中过滤并初始化 SYMBOL_CONFIGS
symbols_to_trade: List[str] = [] # 最终用于交易循环的品种列表

def log_order_params(order_type, params, function_name=""):
    """简化版订单参数日志"""
    try:
        safe_params = params.copy()
        sensitive_keys = ['apiKey', 'secret', 'password', 'signature']
        for key in sensitive_keys:
            if key in safe_params:
                safe_params[key] = '***'
        
        # 提取关键信息，避免逐条打印
        key_info = []
        for key, value in safe_params.items():
            if key in ['symbol', 'side', 'amount', 'type', 'reduceOnly', 'tag']:
                key_info.append(f"{key}: {value}")
        
        logger.log_info(f"📋 {function_name} - {order_type}订单: {', '.join(key_info)}")
            
    except Exception as e:
        logger.log_error("log_order_params", f"记录订单参数失败: {str(e)}")

def get_current_price(symbol: str): # 新增 symbol 参数
    """获取当前价格"""
    try:
        # 使用传入的 symbol
        ticker = exchange.fetch_ticker(symbol)
        return ticker['last']
    except Exception as e:
        logger.log_error("current_price", str(e))
        return None

def get_scaling_status(symbol: str) -> Dict:
    """获取品种的加仓状态 - 修复版本"""
    if symbol not in SCALING_HISTORY:
        SCALING_HISTORY[symbol] = {
            'scaling_count': 0,
            'last_scaling_time': None,
            'base_position_size': 0
        }
    
    # 🆕 添加安全检查：确保加仓次数不会超过限制
    config = SYMBOL_CONFIGS[symbol]
    scaling_config = config.position_management.get('scaling_in', {})
    max_scaling_times = scaling_config.get('max_scaling_times', 3)
    
    # 如果加仓次数异常，自动重置
    if SCALING_HISTORY[symbol]['scaling_count'] > max_scaling_times:
        logger.log_warning(f"🔄 {get_base_currency(symbol)}: 加仓次数异常({SCALING_HISTORY[symbol]['scaling_count']})，自动重置")
        SCALING_HISTORY[symbol]['scaling_count'] = max_scaling_times
    
    return SCALING_HISTORY[symbol]

def can_scale_position(symbol: str, signal_data: dict, current_position: dict) -> bool:
    """判断是否允许加仓 - 严格版本"""
    config = SYMBOL_CONFIGS[symbol]
    scaling_config = config.position_management.get('scaling_in', {})
    
    if not scaling_config.get('enable_scaling_in', True):
        return False
    
    # 安全检查：确保有持仓
    if not current_position or current_position['size'] <= 0:
        return False
    
    # 检查持仓方向与信号方向是否一致
    position_side = current_position['side']
    signal_side = 'long' if signal_data['signal'] == 'BUY' else 'short'
    if position_side != signal_side:
        return False
    
    scaling_status = get_scaling_status(symbol)
    
    # 🆕 严格检查加仓次数限制
    max_scaling_times = scaling_config.get('max_scaling_times', 3)
    if scaling_status['scaling_count'] >= max_scaling_times:
        logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 已达最大加仓次数{max_scaling_times}次，禁止加仓")
        return False
    
    # 检查时间间隔
    min_interval = scaling_config.get('min_interval_minutes', 30)
    if scaling_status['last_scaling_time']:
        time_diff = (datetime.now() - scaling_status['last_scaling_time']).total_seconds() / 60
        if time_diff < min_interval:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 加仓间隔不足{min_interval}分钟")
            return False
    
    # 🆕 严格检查基础仓位大小
    if scaling_status['base_position_size'] <= 0:
        logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 基础仓位大小无效，不允许加仓")
        return False
    
    # 🆕 额外检查：确保当前仓位足够大
    min_position_threshold = getattr(config, 'min_amount', 0.01) * 5  # 至少5倍最小交易量
    if current_position['size'] < min_position_threshold:
        logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 当前仓位过小({current_position['size']:.4f}张)，不允许加仓")
        return False
    
    return True


def monitor_scaling_status(symbol: str):
    """监控加仓状态，用于调试和防护"""
    scaling_status = get_scaling_status(symbol)
    config = SYMBOL_CONFIGS[symbol]
    scaling_config = config.position_management.get('scaling_in', {})
    max_scaling_times = scaling_config.get('max_scaling_times', 3)
    
    # 🆕 如果加仓次数异常，自动重置
    if scaling_status['scaling_count'] > max_scaling_times:
        logger.log_error(f"❌ {get_base_currency(symbol)}: 加仓次数异常({scaling_status['scaling_count']})，自动重置")
        reset_scaling_status(symbol)
        scaling_status = get_scaling_status(symbol)  # 重新获取
    
    logger.log_info(f"🔍 {get_base_currency(symbol)}加仓状态监控: "
                   f"当前次数{scaling_status['scaling_count']}/{max_scaling_times}, "
                   f"基础仓位:{scaling_status['base_position_size']:.6f}, "
                   f"最后加仓:{scaling_status['last_scaling_time']}")

def calculate_scaling_position(symbol: str, base_position: float, signal_data: dict) -> float:
    """计算加仓仓位大小 - 严格版本"""
    config = SYMBOL_CONFIGS[symbol]
    scaling_config = config.position_management.get('scaling_in', {})
    
    scaling_status = get_scaling_status(symbol)
    
    # 🆕 在计算前再次严格检查
    max_scaling_times = scaling_config.get('max_scaling_times', 3)
    if scaling_status['scaling_count'] >= max_scaling_times:
        logger.log_error(f"❌ {get_base_currency(symbol)}: 加仓次数已满，但仍在尝试加仓，强制阻止")
        return 0  # 返回0表示不允许加仓
    
    scaling_multiplier = scaling_config.get('scaling_multiplier', 0.5)
    scaling_position = base_position * scaling_multiplier
    
    # 确保不小于最小交易量
    min_contracts = getattr(config, 'min_amount', 0.01)
    
    # 🆕 检查加仓仓位是否过小
    if scaling_position < min_contracts:
        logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 计算出的加仓仓位({scaling_position:.6f})过小，使用最小交易量")
        scaling_position = min_contracts
    
    # 🆕 增加计数（只有在仓位有效时）
    scaling_status['scaling_count'] += 1
    scaling_status['last_scaling_time'] = datetime.now()
    
    logger.log_info(f"📈 {get_base_currency(symbol)}: 第{scaling_status['scaling_count']}次加仓，仓位:{scaling_position:.6f}张")
    
    return scaling_position

def reset_scaling_status(symbol: str):
    """重置加仓状态（平仓时调用）"""
    if symbol in SCALING_HISTORY:
        SCALING_HISTORY[symbol] = {
            'scaling_count': 0,
            'last_scaling_time': None,
            'base_position_size': 0
        }

def check_sufficient_margin(symbol: str, position_size: float, current_price: float) -> bool:
    """检查保证金是否充足"""
    config = SYMBOL_CONFIGS[symbol]
    
    try:
        # 计算所需保证金
        required_margin = (position_size * current_price * config.contract_size) / config.leverage
        
        # 获取账户余额
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']
        
        # 安全缓冲：要求保证金不超过余额的70%
        if required_margin > usdt_balance * 0.7:
            logger.log_error("保证金不足", f"❌ {get_base_currency(symbol)}:需要{required_margin:.2f} USDT, 可用{usdt_balance:.2f} USDT")
            return False
            
        logger.log_info(f"✅ {get_base_currency(symbol)}: 保证金充足 - 需要{required_margin:.2f} USDT, 可用{usdt_balance:.2f} USDT")
        return True
        
    except Exception as e:
        logger.log_error(f"margin_check_{get_base_currency(symbol)}", f"保证金检查失败: {str(e)}")
        return False

def calculate_dynamic_base_amount(symbol: str, usdt_balance: float) -> float:
    """基于账户规模计算动态基础金额 - 修复版本"""
    config = SYMBOL_CONFIGS[symbol]
    posMngmt = config.position_management
    
    # 分级比例
    if usdt_balance > 10000:
        base_ratio = 0.015
    elif usdt_balance > 5000:
        base_ratio = 0.02
    elif usdt_balance > 1000:
        base_ratio = 0.025
    else:
        base_ratio = 0.03  # 小资金使用较高比例但确保不超过余额
    
    dynamic_base = usdt_balance * base_ratio
    
    # 🆕 修复：确保不超过账户余额的80%
    dynamic_base = min(dynamic_base, usdt_balance * 0.8)
    
    # 🆕 修复：调整最小基础金额，基于账户规模
    if usdt_balance < 100:
        min_base = 5  # 小账户最小5U
    elif usdt_balance < 500:
        min_base = 10
    else:
        min_base = 20
    
    max_base = 500
    
    return max(min_base, min(dynamic_base, max_base))

def calculate_volatility_adjustment(symbol: str, df: pd.DataFrame) -> float:
    """基于波动率调整仓位"""
    # 计算ATR波动率
    atr = sl_tp_strategy.calculate_atr(df)
    current_price = df['close'].iloc[-1]
    atr_percentage = (atr / current_price) * 100
    
    # 波动率越大，仓位越小
    if atr_percentage > 3.0:  # 高波动
        return 0.5
    elif atr_percentage > 2.0:  # 中波动
        return 0.8
    else:  # 低波动
        return 1.0


# 添加全局变量来存储持仓历史
POSITION_HISTORY: Dict[str, List[Dict]] = {}

def get_current_position_history(symbol: str) -> list:
    """获取当前有效持仓的历史记录（排除已平仓的）"""
    try:
        if symbol not in POSITION_HISTORY:
            return []
        
        # 获取所有开仓记录
        open_positions = []
        close_positions = []
        
        for record in POSITION_HISTORY[symbol]:
            if record.get('action') in ['open', 'add', 'partial_close']:
                # 开仓或加仓记录
                open_positions.append(record)
            elif record.get('action') == 'close':
                # 平仓记录
                close_positions.append(record)
        
        # 简单的匹配逻辑：假设最后平仓的记录对应最早的开仓记录
        # 更精确的做法需要记录订单ID来匹配
        remaining_positions = open_positions.copy()
        
        for close_record in close_positions:
            close_size = close_record.get('size', 0)
            close_side = close_record.get('side')
            
            # 从开仓记录中减去平仓数量
            temp_remaining = []
            for open_record in remaining_positions:
                if (open_record.get('side') == close_side and 
                    open_record.get('size', 0) > 0):
                    
                    # 匹配到同方向的开仓记录
                    remaining_size = open_record['size'] - close_size
                    if remaining_size > 0:
                        # 部分平仓，更新剩余数量
                        updated_record = open_record.copy()
                        updated_record['size'] = remaining_size
                        temp_remaining.append(updated_record)
                        close_size = 0  # 已完全匹配
                    else:
                        # 完全平仓，跳过这个开仓记录
                        close_size = abs(remaining_size)
                else:
                    temp_remaining.append(open_record)
            
            remaining_positions = temp_remaining
            if close_size <= 0:
                break
        
        # 如果没有找到有效持仓，返回空列表
        if not remaining_positions:
            return []
            
        # 只返回最近50条有效记录
        max_history = 50
        if len(remaining_positions) > max_history:
            remaining_positions = remaining_positions[-max_history:]
            
        return remaining_positions
        
    except Exception as e:
        logger.log_error(f"get_current_position_history_{get_base_currency(symbol)}", f"获取当前持仓历史失败: {str(e)}")
        return []

def get_position_history(symbol: str) -> list:
    """获取品种的持仓历史记录"""
    try:
        if symbol not in POSITION_HISTORY:
            POSITION_HISTORY[symbol] = []
        
        # 从持仓历史中筛选出有效的持仓记录
        current_history = POSITION_HISTORY[symbol]
        
        # 只返回最近50条记录，避免内存占用过大
        max_history = 50
        if len(current_history) > max_history:
            current_history = current_history[-max_history:]
            POSITION_HISTORY[symbol] = current_history
            
        return current_history
        
    except Exception as e:
        logger.log_error(f"get_position_history_{get_base_currency(symbol)}", f"获取持仓历史失败: {str(e)}")
        return []

def add_to_position_history(symbol: str, position_data: dict):
    """添加持仓历史记录"""
    try:
        if symbol not in POSITION_HISTORY:
            POSITION_HISTORY[symbol] = []
        
        # 添加时间戳
        position_record = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'symbol': symbol,
            'side': position_data.get('side'),
            'size': position_data.get('size'),
            'entry_price': position_data.get('entry_price'),
            'unrealized_pnl': position_data.get('unrealized_pnl', 0),
            'leverage': position_data.get('leverage'),
            'margin_mode': position_data.get('margin_mode')
        }
        
        # 如果是平仓操作，添加平仓信息
        if position_data.get('action') == 'close':
            position_record.update({
                'action': 'close',
                'close_price': position_data.get('close_price'),
                'close_reason': position_data.get('close_reason', 'manual'),
                'realized_pnl': position_data.get('realized_pnl', 0)
            })
        else:
            position_record['action'] = position_data.get('action', 'open')
        
        POSITION_HISTORY[symbol].append(position_record)
        
        logger.log_info(f"📝 {get_base_currency(symbol)}: 添加持仓历史 - {position_record['action']} {position_record['side']} {position_record['size']}张")
        
    except Exception as e:
        logger.log_error(f"add_to_position_history_{get_base_currency(symbol)}", f"添加持仓历史失败: {str(e)}")

def cleanup_resources():
    """清理资源"""
    try:
        logger.log_info("🧹 清理资源...")
        
        # 1. 保存持仓历史到文件
        save_position_history()
        
        # 2. 关闭交易所连接
        global exchange
        if exchange:
            try:
                # CCXT 交易所对象通常不需要显式关闭，但我们可以标记为 None
                exchange = None
                logger.log_info("✅ 交易所连接已清理")
            except Exception as e:
                logger.log_warning(f"⚠️ 交易所连接清理异常: {str(e)}")
        
        # 3. 清理 DeepSeek 客户端
        global deepseek_client
        if deepseek_client:
            deepseek_client = None
            logger.log_info("✅ DeepSeek 客户端已清理")
        
        # 4. 清理全局变量
        global price_history, signal_history, SCALING_HISTORY, POSITION_HISTORY
        price_history.clear()
        signal_history.clear()
        SCALING_HISTORY.clear()
        POSITION_HISTORY.clear()
        
        logger.log_info("✅ 所有资源清理完成")
        
    except Exception as e:
        logger.log_error("cleanup_resources", f"资源清理异常: {str(e)}")

def save_position_history():
    """
    将当前的仓位历史状态保存到当前账户的文件夹中。
    """
    global position # 引用全局仓位变量
    
    # 确保保存路径存在 (此逻辑已在 trade_logger 中实现，但这里冗余一次更安全)
    save_dir = os.path.dirname(POSITION_STATE_FILE)
    if not os.path.exists(save_dir):
        os.makedirs(save_dir, exist_ok=True)
        
    # 只有当 position 不是 None 且有内容时才保存
    if position is None:
        return
        
    try:
        # 将 position 对象转换为 JSON 可序列化的格式 (如果 position 是自定义类，需手动转换)
        serializable_position = position # 假设 position 本身是 dict 或 list
        
        with open(POSITION_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(serializable_position, f, indent=4)
        # logger.log_debug(f"💾 成功保存 {CURRENT_ACCOUNT} 账户的仓位状态。")
        
    except Exception as e:
        logger.log_error("save_position_history", f"保存仓位状态失败: {e}")


def load_position_history() -> Optional[Dict[str, Any]]:
    """
    从当前账户的文件夹中加载上次保存的仓位历史状态。
    """
    global position # 引用全局仓位变量
    
    try:
        if os.path.exists(POSITION_STATE_FILE):
            with open(POSITION_STATE_FILE, 'r', encoding='utf-8') as f:
                # 假设 position 存储的是一个字典结构
                position_data = json.load(f)
                logger.log_info(f"✅ 成功加载 {CURRENT_ACCOUNT} 账户的仓位状态。")
                return position_data
        else:
            logger.log_info(f"ℹ️ {CURRENT_ACCOUNT} 账户的仓位状态文件不存在，将从空状态开始。")
            return None
    except Exception as e:
        logger.log_error("load_position_history", f"加载仓位状态失败: {e}")
        return None

def calculate_enhanced_position(symbol: str, signal_data: dict, price_data: dict, current_position: Optional[dict]) -> float:
    """增强版仓位计算 - 修复基础仓位问题"""
    config = SYMBOL_CONFIGS[symbol]
    posMngmt = config.position_management
    
    try:
        # 获取账户余额
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']
        
        # 1. 动态基础金额（基于账户规模）
        dynamic_base_usdt = calculate_dynamic_base_amount(symbol, usdt_balance)
        
        # 检查是否是加仓情况
        is_scaling = current_position and current_position['size'] > 0
        
        if is_scaling:
            # 1. 先获取状态并设置
            scaling_status = get_scaling_status(symbol)
            if scaling_status['base_position_size'] == 0:
                # (这段代码现在会被首先执行)
                logger.log_info(f"🔧 {get_base_currency(symbol)}: 首次加仓，正在设置基础仓位...")
                balance = exchange.fetch_balance()
                usdt_balance = balance['USDT']['free']
                base_position_usdt = calculate_dynamic_base_amount(symbol, usdt_balance)
                nominal_value = base_position_usdt * config.leverage
                base_position_contracts = nominal_value / (price_data['price'] * config.contract_size)
                base_position_contracts = round(base_position_contracts, 6)
                min_contracts = getattr(config, 'min_amount', 0.01)
                if base_position_contracts < min_contracts:
                    base_position_contracts = min_contracts
                
                scaling_status['base_position_size'] = base_position_contracts
                logger.log_info(f"🔧 {get_base_currency(symbol)}: 设置基础仓位为 {base_position_contracts:.6f} 张")
            
            # 2. 后检查 (此时 base_position_size > 0，检查可以通过)
            if not can_scale_position(symbol, signal_data, current_position):
                logger.log_info(f"⏸️ {get_base_currency(symbol)}: 不允许加仓（例如：间隔太短或次数已满），返回0仓位")
                return 0  
            
            scaling_position = calculate_scaling_position(symbol, scaling_status['base_position_size'], signal_data)
            
            # 🆕 如果加仓仓位为0，直接返回
            if scaling_position <= 0:
                return 0
                
            # 转换为合约张数
            # 注意：scaling_position 已经是合约张数，不需要再次转换
            contract_size = scaling_position
            
            # 🆕 --- 动态精度处理 (针对加仓) ---
            step_size = config.amount_precision_step
            min_size = config.min_amount

            if config.requires_integer:
                # 整数合约品种 (向上取整)
                contract_size = max(min_size, math.ceil(contract_size))
                logger.log_warning(f"⚠️ {get_base_currency(symbol)}: (加仓) 调整为整数张合约: {contract_size} 张")
            else:
                # 非整数合约品种 (向下取整到有效步长)
                if step_size > 0:
                    contract_size = math.floor(contract_size / step_size) * step_size
                else:
                    contract_size = round(contract_size, 8) # Fallback
                
                # 确保不小于最小交易量
                if contract_size < min_size:
                    logger.log_warning(f"⚠️ {get_base_currency(symbol)}: (加仓) 计算合约 {contract_size} 小于最小 {min_size}，调整为最小交易量")
                    contract_size = min_size
            
            logger.log_info(f"📈 {get_base_currency(symbol)}: 加仓计算完成 - {contract_size:.6f}张")
            return contract_size
        
        # 非加仓情况，继续标准计算
        # 2. 信心倍数
        confidence_multiplier = {
            'HIGH': posMngmt['high_confidence_multiplier'],
            'MEDIUM': posMngmt['medium_confidence_multiplier'],
            'LOW': posMngmt['low_confidence_multiplier']
        }.get(signal_data['confidence'], 1.0)
        
        # 3. 趋势倍数
        trend = price_data['trend_analysis'].get('overall', 'Consolidation')
        if trend in ['Strong uptrend', 'Strong downtrend']:
            trend_multiplier = posMngmt['trend_strength_multiplier']
        else:
            trend_multiplier = 1.0
        
        # 4. RSI调整
        rsi = price_data['technical_data'].get('rsi', 50)
        if rsi > 75 or rsi < 25:
            rsi_multiplier = 0.7
        else:
            rsi_multiplier = 1.0
        
        # 5. 波动率调整
        volatility_multiplier = calculate_volatility_adjustment(symbol, price_data['full_data'])
        
        # 6. 杠杆调整（如果使用高杠杆，减少仓位）
        leverage_multiplier = 1.0 / min(config.leverage, 10)  # 杠杆越高，实际仓位越小
        
        # 7. 头仓最小比例限制（如果是首次开仓）
        is_first_position = not current_position or current_position['size'] == 0
        if is_first_position:
            # 计算头仓最小金额（总余额 * 最小比例）
            first_position_min = usdt_balance * posMngmt['first_position_min_ratio']
            # 取较大值作为基础金额
            dynamic_base_usdt = max(dynamic_base_usdt, first_position_min)

        if not is_first_position:
            logger.log_info(f"ℹ️ 检测到加仓信号，使用标准逻辑计算仓位。")

        # 计算建议投资金额
        suggested_usdt = (dynamic_base_usdt * confidence_multiplier * trend_multiplier * rsi_multiplier * volatility_multiplier * leverage_multiplier)
        
        # 风险上限
        max_usdt = usdt_balance * posMngmt['max_position_ratio']
        final_usdt = min(suggested_usdt, max_usdt)
        
        # 🆕 新增：确保头仓保证金不小于5 USDT
        MIN_BASE_MARGIN = 5.0  # 最小头仓保证金5 USDT
        if final_usdt < MIN_BASE_MARGIN:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 计算保证金{final_usdt:.2f} USDT小于{MIN_BASE_MARGIN} USDT，调整为最小保证金")
            final_usdt = MIN_BASE_MARGIN
            
            # 再次检查是否超过最大限制
            if final_usdt > max_usdt:
                logger.log_error(f"❌ {get_base_currency(symbol)}: 最小保证金{MIN_BASE_MARGIN} USDT超过最大限制{max_usdt:.2f} USDT，无法开仓")
                return 0
        
        # 转换为合约张数
        nominal_value = final_usdt * config.leverage
        contract_size = nominal_value / (price_data['price'] * config.contract_size)
        
        # 🆕 --- 动态精度处理 (替换原有逻辑) ---
        step_size = config.amount_precision_step
        min_size = config.min_amount

        if config.requires_integer:
            # 整数合约品种 (向上取整)
            # (注意：开仓时我们更倾向于向上取整以满足最小保证金，这与加仓不同)
            contract_size = max(min_size, math.ceil(contract_size))
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: (开仓) 调整为整数张合约: {contract_size} 张")
        else:
            # 非整数合约品种 (向下取整到有效步长)
            if step_size > 0:
                contract_size = math.floor(contract_size / step_size) * step_size
            else:
                contract_size = round(contract_size, 8) # Fallback

            # 确保不小于最小交易量
            if contract_size < min_size:
                logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 计算合约 {contract_size:.6f} 小于最小 {min_size:.6f}，调整为最小交易量")
                contract_size = min_size   
        
        # 🆕 最终保证金验证
        final_margin = (contract_size * price_data['price'] * config.contract_size) / config.leverage
        if final_margin < MIN_BASE_MARGIN:
            # 如果最终保证金仍然小于最小值，重新计算合约数量
            required_nominal_value = MIN_BASE_MARGIN * config.leverage
            contract_size = required_nominal_value / (price_data['price'] * config.contract_size)
            
            step_size = config.amount_precision_step
            min_size = config.min_amount

            if config.requires_integer:
                # (保证金修正时，必须向上取整以满足要求)
                contract_size = max(min_size, math.ceil(contract_size))
            else:
                # (保证金修正时，也应向上取整到下一个步长)
                if step_size > 0:
                    contract_size = math.ceil(contract_size / step_size) * step_size
                else:
                    contract_size = round(contract_size, 8)
                
                # 确保不小于最小交易量
                if contract_size < min_size:
                    contract_size = min_size
            
            final_margin = (contract_size * price_data['price'] * config.contract_size) / config.leverage
            logger.log_info(f"🔄 {get_base_currency(symbol)}: 最终调整保证金为 {final_margin:.2f} USDT")

        # 详细日志
        calculation_details = f"""
        🎯 增强版仓位计算详情:
        账户余额: {usdt_balance:.2f} USDT
        {'头仓最小金额: ' + str(first_position_min) + ' USDT' if is_first_position else ''}
        动态基础: {dynamic_base_usdt:.2f} USDT
        信心倍数: {confidence_multiplier} | 趋势倍数: {trend_multiplier}
        RSI倍数: {rsi_multiplier} | 波动率倍数: {volatility_multiplier}
        杠杆倍数: {leverage_multiplier}
        建议保证金: {suggested_usdt:.2f} USDT → 最终保证金: {final_usdt:.2f} USDT
        名义总价值 (保证金 * 杠杆): {nominal_value:.2f} USDT
        合约数量: {contract_size:.2f}张
        🛡️ 实际保证金: {final_margin:.2f} USDT
        """
        logger.log_info(calculation_details)
        
        # 🆕 最终检查：如果保证金仍然不足，返回0
        if final_margin < MIN_BASE_MARGIN:
            logger.log_error(f"❌ {get_base_currency(symbol)}: 无法满足最小保证金{MIN_BASE_MARGIN} USDT要求，放弃开仓")
            return 0

        return contract_size
        
    except Exception as e:
        logger.log_error("enhanced_position_calculation", str(e))
        # 降级到原版计算
        return calculate_intelligent_position(symbol, signal_data, price_data, current_position)

def log_perpetual_order_details(symbol: str, side: str, amount: float, order_type: str, reduce_only=False, stop_loss=False, take_profit=False, stop_loss_price=None):
    """简化版订单详情日志"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        action_types = []
        if reduce_only:
            action_types.append("只减仓")
        if stop_loss:
            action_types.append("止损")
        if take_profit:
            action_types.append("止盈")
            
        action_str = " | ".join(action_types) if action_types else "普通"
        
        log_msg = f"🎯 {get_base_currency(symbol)} 永续合约订单: {side} {amount}张 | {order_type} | {action_str}"
        if stop_loss_price:
            stop_loss_ratio = abs(stop_loss_price - get_current_price(symbol)) / get_current_price(symbol) * 100  # 添加 symbol 参数
            log_msg += f" | 止损价:{stop_loss_price:.2f}({stop_loss_ratio:.2f}%)"
            
        logger.log_info(log_msg)
            
    except Exception as e:
        logger.log_error("log_perpetual_order_details", f"记录订单{get_base_currency(symbol)} 详情失败: {str(e)}")

def check_existing_positions(symbol: str):
    # Check existing positions and return whether there are isolated positions and the information of isolated positions.
    config = SYMBOL_CONFIGS[symbol]
    logger.log_info("🔍 Checking existing position mode..")
    positions = exchange.fetch_positions([config.symbol])

    has_isolated_position = False
    isolated_position_info = None

    for pos in positions:
        if pos['symbol'] == config.symbol:
            contracts = float(pos.get('contracts', 0))
            mode = pos.get('mgnMode')

            if contracts > 0 and mode == 'isolated':
                has_isolated_position = True
                isolated_position_info = {
                    'side': pos.get('side'),
                    'size': contracts,
                    'entry_price': pos.get('entryPrice'),
                    'mode': mode
                }
                break

    return has_isolated_position, isolated_position_info

def set_margin_mode(mode, symbol):
    """设置保证金模式"""
    try:
        if mode == 'cross':
            # 全仓模式
            exchange.private_post_account_set_position_mode({
                'posMode': 'long_short_mode'
            })
        else:
            # 逐仓模式
            exchange.private_post_account_set_position_mode({
                'posMode': 'isolated'
            })
        logger.log_info(f"✅ Margin mode set to: {mode}")
        return True
    except Exception as e:
        logger.log_error(f"set_margin_mode_{mode}", str(e))
        return False


def setup_exchange(symbol: str):
    """
    智能交易所设置：设置杠杆和保证金模式，并获取合约规格
    """
    # 动态加载当前 symbol 的配置
    config = SYMBOL_CONFIGS[symbol]
    
    try:
        # 1. 先获取合约规格
        markets = exchange.load_markets()
        if symbol not in markets:
            logger.log_error("exchange_setup", f"Symbol {get_base_currency(symbol)} not supported by exchange.")
            return False
            
        market_info = markets[symbol]
        
        # 动态更新配置实例的合约信息
        config.update_exchange_rules(
            contract_size=float(market_info.get('contractSize', 1.0)),
            min_amount=market_info['limits']['amount']['min'],
            amount_step=market_info['precision']['amount'],
            price_step=market_info['precision']['price'],
            requires_integer=(market_info['precision']['amount'] == 1)
        )

        logger.log_info(f"✅ Contract {get_base_currency(symbol)}: 1 contract = {config.contract_size} base asset")
        logger.log_info(f"📏 Min trade {get_base_currency(symbol)}: {config.min_amount} contracts")
        logger.log_info(f"📐 Amount step {get_base_currency(symbol)}: {config.amount_precision_step}")
        logger.log_info(f"💰 Price step {get_base_currency(symbol)}: {config.price_precision_step}")
        logger.log_info(f"🔢 Integer only: {config.requires_integer}")
        # -----------------------------------------------
        # 2. 设置杠杆（使用更安全的方式）
        leverage = getattr(config, 'leverage', 50)
        logger.log_info(f"⚙️ Setting leverage for {get_base_currency(symbol)} to {leverage}x...")
        try:
            # 使用OKX特定的API设置杠杆
            exchange.private_post_account_set_leverage({
                'instId': get_correct_inst_id(symbol),
                'lever': str(leverage),
                'mgnMode': config.margin_mode
            })
            logger.log_warning(f"✅ Leverage {leverage}x set for {get_base_currency(symbol)}")
        except Exception as e:
            logger.log_warning(f"⚠️ Leverage setting failed for {get_base_currency(symbol)}: {e}")
            
        # 3. 设置保证金模式（使用OKX特定的API）
        logger.log_info(f"⚙️ Setting margin mode for {get_base_currency(symbol)} to {config.margin_mode}...")
        try:
            # 使用OKX特定的API设置仓位模式
            exchange.private_post_account_set_position_mode({
                'posMode': 'long_short_mode' if config.margin_mode == 'cross' else 'net_mode'
            })
            logger.log_warning(f"✅ Margin mode {config.margin_mode} set for {get_base_currency(symbol)}")
        except Exception as e:
            # 如果设置失败，可能是已经设置过了，记录警告但不中断流程
            logger.log_warning(f"⚠️ Margin mode setting failed for {get_base_currency(symbol)}: {e}")
            logger.log_warning(f"ℹ️ This might be because the mode is already set, continuing...")
        
        return True

    except Exception as e:
        logger.log_error(f"exchange_setup_{get_base_currency(symbol)}", str(e))
        return False

def fetch_extended_ohlcv(symbol: str, hours: int = 24):
    """获取扩展的K线数据以覆盖指定小时数"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        # 根据时间帧计算所需K线数量
        timeframe_minutes = {
            '1m': 1, '5m': 5, '15m': 15, '1h': 60, '4h': 240
        }.get(config.timeframe, 15)
        
        # 计算需要的K线数量（24小时 + 缓冲）
        required_candles = int((hours * 60) / timeframe_minutes) + 50
        
        # 确保不超过交易所限制
        max_limit = 1000
        actual_limit = min(required_candles, max_limit)
        
        logger.log_info(f"📊 {get_base_currency(symbol)}: 获取{hours}小时数据，需要{actual_limit}根{config.timeframe}K线")
        
        ohlcv = exchange.fetch_ohlcv(symbol, config.timeframe, limit=actual_limit)
        
        if ohlcv is None or len(ohlcv) < 50:  # 至少需要50根K线
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 扩展数据获取不足，使用默认数据")
            return fetch_ohlcv_with_retry(symbol)
            
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        # 计算技术指标
        df = calculate_technical_indicators(df)
        return df
        
    except Exception as e:
        logger.log_error(f"extended_ohlcv_{get_base_currency(symbol)}", str(e))
        # 降级到原函数
        return fetch_ohlcv_with_retry(symbol)

def calculate_multi_timeframe_support_resistance(df, lookback_periods=[20, 50, 100]):
    """基于多个时间范围计算支撑阻力位"""
    try:
        current_price = df['close'].iloc[-1]
        support_levels = []
        resistance_levels = []
        
        # 计算不同时间范围的支撑阻力
        for period in lookback_periods:
            if len(df) >= period:
                # 支撑位：近期低点
                support = df['low'].tail(period).min()
                # 阻力位：近期高点
                resistance = df['high'].tail(period).max()
                
                support_levels.append(support)
                resistance_levels.append(resistance)
        
        # 选择最重要的支撑阻力位
        if support_levels:
            # 选择较近的支撑位（但要有一定距离）
            valid_supports = [s for s in support_levels if s < current_price * 0.98]
            primary_support = max(valid_supports) if valid_supports else min(support_levels)
        else:
            primary_support = current_price * 0.95
            
        if resistance_levels:
            # 选择较近的阻力位（但要有一定距离）
            valid_resistances = [r for r in resistance_levels if r > current_price * 1.02]
            primary_resistance = min(valid_resistances) if valid_resistances else max(resistance_levels)
        else:
            primary_resistance = current_price * 1.05
        
        # 动态支撑阻力（布林带）
        bb_upper = df['bb_upper'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]
        
        return {
            'primary_support': primary_support,
            'primary_resistance': primary_resistance,
            'dynamic_support': bb_lower,
            'dynamic_resistance': bb_upper,
            'support_levels': support_levels,
            'resistance_levels': resistance_levels,
            'price_vs_resistance': ((primary_resistance - current_price) / current_price) * 100,
            'price_vs_support': ((current_price - primary_support) / primary_support) * 100
        }
    except Exception as e:
        logger.log_error("multi_timeframe_levels", str(e))
        return get_support_resistance_levels(df)  # 降级到原函数

def identify_trend_strength(df):
    """识别趋势强度和多时间框架趋势"""
    try:
        current_price = df['close'].iloc[-1]
        
        # 多时间框架移动平均线分析
        timeframes = {
            'short_term': 20,
            'medium_term': 50, 
            'long_term': 100
        }
        
        trend_scores = {}
        for tf_name, period in timeframes.items():
            if len(df) >= period:
                sma = df['close'].rolling(period).mean().iloc[-1]
                # 价格在均线上方为正值，下方为负值
                trend_scores[tf_name] = (current_price - sma) / sma * 100
        
        # 计算综合趋势分数
        total_score = sum(trend_scores.values()) / len(trend_scores) if trend_scores else 0
        
        # 判断趋势强度
        if total_score > 2.0:
            trend_strength = "STRONG_UPTREND"
        elif total_score > 0.5:
            trend_strength = "UPTREND" 
        elif total_score < -2.0:
            trend_strength = "STRONG_DOWNTREND"
        elif total_score < -0.5:
            trend_strength = "DOWNTREND"
        else:
            trend_strength = "CONSOLIDATION"
        
        return {
            'trend_strength': trend_strength,
            'trend_score': total_score,
            'timeframe_scores': trend_scores,
            'description': f"综合趋势分数: {total_score:.2f}% - {trend_strength}"
        }
        
    except Exception as e:
        logger.log_error("trend_strength_analysis", str(e))
        return {'trend_strength': 'UNKNOWN', 'trend_score': 0}

def calculate_intelligent_position(symbol: str, signal_data: dict, price_data: dict, current_position: Optional[dict]) -> float:
    """Calculate intelligent position size - with additional safety checks"""
    config = SYMBOL_CONFIGS[symbol]
    posMngmt = config.position_management

    # 🆕 安全检查：确保 price_data 存在且包含价格
    if not price_data or 'price' not in price_data or not price_data['price']:
        logger.log_error("position_calculation", "价格数据无效，使用最小仓位")
        return getattr(config, 'min_amount', 0.01)

    # 🆕 安全检查：确保配置存在
    if not posMngmt:
        logger.log_error("position_calculation", "仓位管理配置缺失，使用最小仓位")
        return getattr(config, 'min_amount', 0.01)
    
    try:
        # Get account balance
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']

        # Base USDT investment
        base_usdt = posMngmt['base_usdt_amount']
        logger.log_warning(f"💰 Available USDT balance: {usdt_balance:.2f}, base investment {base_usdt}")

        # Adjust based on confidence level - fix here
        confidence_multiplier = {
            'HIGH': posMngmt['high_confidence_multiplier'],
            'MEDIUM': posMngmt['medium_confidence_multiplier'],
            'LOW': posMngmt['low_confidence_multiplier']
        }.get(signal_data['confidence'], 1.0)  # Add default value

        # Adjust based on trend strength
        trend = price_data['trend_analysis'].get('overall', 'Consolidation')
        if trend in ['Strong uptrend', 'Strong downtrend']:
            trend_multiplier = posMngmt['trend_strength_multiplier']
        else:
            trend_multiplier = 1.0

        # Adjust based on RSI status (reduce position in overbought/oversold areas)
        rsi = price_data['technical_data'].get('rsi', 50)
        if rsi > 75 or rsi < 25:
            rsi_multiplier = 0.7
        else:
            rsi_multiplier = 1.0

        # Calculate suggested USDT investment amount
        suggested_usdt = base_usdt * confidence_multiplier * trend_multiplier * rsi_multiplier

        # Risk management: not exceeding specified ratio of total funds - remove duplicate definition
        max_usdt = usdt_balance * posMngmt['max_position_ratio']
        final_usdt = min(suggested_usdt, max_usdt)

        # 🆕 新增：确保头仓保证金不小于5 USDT
        MIN_BASE_MARGIN = 5.0
        if final_usdt < MIN_BASE_MARGIN:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 计算保证金{final_usdt:.2f} USDT小于{MIN_BASE_MARGIN} USDT，调整为最小保证金")
            final_usdt = MIN_BASE_MARGIN
            
            if final_usdt > max_usdt:
                logger.log_error(f"❌ {get_base_currency(symbol)}: 最小保证金{MIN_BASE_MARGIN} USDT超过最大限制{max_usdt:.2f} USDT")
                return 0
            
        # ------------------- 核心修改开始 -------------------
        
        # Correct contract quantity calculation!
        # 此时 final_usdt 代表保证金
        # 保证金 * 杠杆 = 名义总价值
        nominal_value = final_usdt * config.leverage
        contract_size = nominal_value / (price_data['price'] * config.contract_size)

        # ------------------- 核心修改结束 -------------------
        # 🆕 --- 修正的动态精度处理 ---
        step_size = config.amount_precision_step
        min_size = config.min_amount

        if config.requires_integer:
            # 1. 优先处理整数合约：向上取整，并确保不小于最小
            contract_size = max(min_size, math.ceil(contract_size))
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 调整为整数张合约: {contract_size} 张")
        else:
            # 2. 非整数合约：向下取整到步长
            if step_size > 0:
                contract_size = math.floor(contract_size / step_size) * step_size
            else:
                contract_size = round(contract_size, 8) # Fallback

            # 确保不小于最小交易量
            if contract_size < min_size:
                logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 计算合约 {contract_size:.6f} 小于最小 {min_size:.6f}，调整为最小交易量")
                contract_size = min_size
        # --- 修正结束 ---

        calculation_summary = f"""
            📊 仓位计算详情:
            基础保证金: {base_usdt} USDT | 信心倍数: {confidence_multiplier}
            趋势倍数: {trend_multiplier} | RSI倍数: {rsi_multiplier}
            建议保证金: {suggested_usdt:.2f} USDT → 最终保证金: {final_usdt:.2f} USDT
            名义总价值 (保证金 * 杠杆): {nominal_value:.2f} USDT
            合约数量: {contract_size:.4f}张 → 四舍五入: {round(contract_size, 2):.2f}张
            """
        logger.log_info(calculation_summary)

        # 🆕 最终保证金验证
        final_margin = (contract_size * price_data['price'] * config.contract_size) / config.leverage
        if final_margin < MIN_BASE_MARGIN:
            logger.log_error(f"❌ {get_base_currency(symbol)}: 无法满足最小保证金{MIN_BASE_MARGIN} USDT要求")
            return 0
        
        return contract_size

    except Exception as e:
            logger.log_error("Position calculation failed, using base position", str(e))
            # 🆕 --- 修正的备用计算 ---
            # Emergency backup calculation
            base_usdt = posMngmt['base_usdt_amount']
            contract_size = (base_usdt * config.leverage) / (price_data['price'] * getattr(config, 'contract_size', 0.01))
            
            # 同样应用动态精度
            step_size = config.amount_precision_step
            min_size = config.min_amount

            if config.requires_integer:
                contract_size = max(min_size, math.ceil(contract_size))
            else:
                if step_size > 0:
                    contract_size = math.floor(contract_size / step_size) * step_size
                contract_size = max(min_size, contract_size)
            return contract_size


def calculate_technical_indicators(df):
    """Calculate technical indicators - from first strategy"""
    try:
        # Moving averages
        df['sma_5'] = df['close'].rolling(window=5, min_periods=1).mean()
        df['sma_20'] = df['close'].rolling(window=20, min_periods=1).mean()
        df['sma_50'] = df['close'].rolling(window=50, min_periods=1).mean()

        # Exponential moving averages
        df['ema_12'] = df['close'].ewm(span=12).mean()
        df['ema_26'] = df['close'].ewm(span=26).mean()
        df['macd'] = df['ema_12'] - df['ema_26']
        df['macd_signal'] = df['macd'].ewm(span=9).mean()
        df['macd_histogram'] = df['macd'] - df['macd_signal']

        # Relative Strength Index (RSI)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # Bollinger Bands
        df['bb_middle'] = df['close'].rolling(20).mean()
        bb_std = df['close'].rolling(20).std()
        df['bb_upper'] = df['bb_middle'] + (bb_std * 2)
        df['bb_lower'] = df['bb_middle'] - (bb_std * 2)
        df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])

        # Volume moving average
        df['volume_ma'] = df['volume'].rolling(20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_ma']

        # Support resistance levels
        df['resistance'] = df['high'].rolling(20).max()
        df['support'] = df['low'].rolling(20).min()

        # 添加ATR计算
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges, axis=1)
        df['atr'] = true_range.rolling(14).mean()

        # Fill NaN values
        df = df.bfill().ffill()

        return df
    except Exception as e:
        logger.log_error("technical_indicators", str(e))
        return df


def get_support_resistance_levels(df, lookback=20):
    """Calculate support resistance levels"""
    try:
        recent_high = df['high'].tail(lookback).max()
        recent_low = df['low'].tail(lookback).min()
        current_price = df['close'].iloc[-1]

        resistance_level = recent_high
        support_level = recent_low

        # Dynamic support resistance (based on Bollinger Bands)
        bb_upper = df['bb_upper'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]

        return {
            'static_resistance': resistance_level,
            'static_support': support_level,
            'dynamic_resistance': bb_upper,
            'dynamic_support': bb_lower,
            'price_vs_resistance': ((resistance_level - current_price) / current_price) * 100,
            'price_vs_support': ((current_price - support_level) / support_level) * 100
        }
    except Exception as e:
        logger.log_error("support_resistance", str(e))
        return {}


def get_market_trend(df):
    """Determine market trend"""
    try:
        current_price = df['close'].iloc[-1]

        # Multi-timeframe trend analysis
        trend_short = "Uptrend" if current_price > df['sma_20'].iloc[-1] else "Downtrend"
        trend_medium = "Uptrend" if current_price > df['sma_50'].iloc[-1] else "Downtrend"

        # MACD trend
        macd_trend = "bullish" if df['macd'].iloc[-1] > df['macd_signal'].iloc[-1] else "bearish"

        # Comprehensive trend judgment
        if trend_short == "Uptrend" and trend_medium == "Uptrend":
            overall_trend = "Strong uptrend"
        elif trend_short == "Downtrend" and trend_medium == "Downtrend":
            overall_trend = "Strong downtrend"
        else:
            overall_trend = "Consolidation"

        return {
            'short_term': trend_short,
            'medium_term': trend_medium,
            'macd': macd_trend,
            'overall': overall_trend,
            'rsi_level': df['rsi'].iloc[-1]
        }
    except Exception as e:
        logger.log_error("trend_analysis", str(e))
        return {}

def get_correct_inst_id(symbol: str) -> str:
    """
    将 CCXT 格式的永续合约符号转换为 OKX 交易所要求的 InstId (例如: BTC/USDT:USDT -> BTC-USDT-SWAP)。

    Args:
        symbol: CCXT 标准格式的交易品种符号。

    Returns:
        OKX 要求的合约 ID。
    """
    if not symbol or ':' not in symbol:
        # 如果格式不正确，直接返回符号，让交易所 API 报错（安全回退）
        return symbol 

    # 1. 移除合约类型后缀 (:USDT)，得到基础交易对部分
    #    例如: 'ASTR/USDT:USDT' -> 'ASTR/USDT'
    base_quote = symbol.split(':')[0]
    
    # 2. 将分隔符 '/' 替换为 OKX 要求的 '-' (连字符)
    #    例如: 'ASTR/USDT' -> 'ASTR-USDT'
    inst_id_base = base_quote.replace('/', '-')
    
    # 3. 加上 OKX 永续合约的后缀
    #    例如: 'ASTR-USDT' -> 'ASTR-USDT-SWAP'
    return f"{inst_id_base}-SWAP"

def log_api_response(response, function_name=""):
    """记录API响应"""
    try:
        if 'code' in response:
            if response['code'] == '0':
                logger.log_info(f"✅ {function_name} API成功: {response.get('msg', 'Success')}")
            else:
                logger.log_error(f"{function_name}_api", f"API错误: {response.get('msg', 'Unknown error')}")
        else:
            logger.log_warning(f"⚠️ {function_name} 未知API响应格式: {response}")
    except Exception as e:
        logger.log_error("log_api_response", f"记录API响应失败: {str(e)}")

def get_current_position(symbol: str) -> Optional[dict]:
    """Get current position status - 增强版持仓检测"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        positions = exchange.fetch_positions([config.symbol])
        if not positions:
            return None
        
        for pos in positions:
            if pos['symbol'] == config.symbol:
                contracts = float(pos['contracts']) if pos['contracts'] else 0
                side = pos.get('side')
                
                # 🆕 增强验证：确保持仓真实存在
                if (contracts > 0 and 
                    side in ['long', 'short'] and 
                    pos.get('marginMode') in ['isolated', 'cross'] and
                    pos.get('entryPrice') and 
                    float(pos['entryPrice']) > 0):
                    
                    # 🆕 额外验证：通过余额检查
                    try:
                        balance = exchange.fetch_balance()
                        total_balance = balance['total'].get('USDT', 0)
                        if total_balance <= 0:
                            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 账户余额异常，跳过持仓")
                            continue
                    except:
                        pass
                    
                    return {
                        'side': side,
                        'size': contracts,
                        'entry_price': float(pos['entryPrice']),
                        'unrealized_pnl': float(pos['unrealizedPnl']) if pos['unrealizedPnl'] else 0,
                        'leverage': float(pos['leverage']) if pos['leverage'] else config.leverage,
                        'symbol': pos['symbol'],
                        'margin_mode': pos.get('marginMode', ''),
                        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    }

        return None

    except Exception as e:
        logger.log_error(f"position_fetch_{get_base_currency(symbol)}", f"Failed to fetch positions: {str(e)}")
        return None

def generate_cl_ord_id(side: str) -> str:
    """
    生成符合OKX规范的clOrdId：
    - 仅包含字母和数字
    - 长度 1-32位
    - 前缀区分买卖方向，确保唯一性
    """
    prefix = "SELL" if side == "sell" else "BUY"
    unique_str = str(uuid.uuid4()).replace('-', '')
    cl_ord_id = f"{prefix}{unique_str}"[:32]
    return cl_ord_id

def sl_tp_algo_order_set(symbol: str, side: str, amount: float, stop_loss_price: Optional[float] = None, take_profit_price: Optional[float] = None) -> Dict[str, Any]:
    """
    优化版：根据OKX API文档，价格为0表示撤销止盈止损
    返回单个ID而非列表（因每次调用最多生成一个订单）
    """
    # 初始化返回结果
    result = {'success': False, 'algo_id': None, 'algo_cl_ord_id': None}
    config = SYMBOL_CONFIGS[symbol]
    
    # 根据OKX API文档，价格为0表示撤销
    has_stop_loss = stop_loss_price is not None and stop_loss_price != 0
    has_take_profit = take_profit_price is not None and take_profit_price != 0
    
    # 如果都是0或None，则无需创建任何订单
    if not (has_stop_loss or has_take_profit):
        logger.log_warning("⚠️ 未设置有效的止损或止盈价格，无需创建订单")
        return result

    try:
        inst_id = get_correct_inst_id(symbol)
        opposite_side = 'buy' if side in ('sell', 'short') else 'sell'
        
        # 公共参数（三种订单类型的共有字段）
        base_params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': opposite_side,
            'sz': str(amount),
        }

        # 1. 同时存在有效的止损止盈：生成OCO订单
        if has_stop_loss and has_take_profit:
            oco_params = {
                **base_params,
                'ordType': 'oco',
                'slTriggerPx': str(stop_loss_price),
                'slOrdPx': '-1',
                'tpTriggerPx': str(take_profit_price),
                'tpOrdPx': '-1',
                'algoClOrdId': generate_cl_ord_id(f"{side}")  # OCO单专用ID
            }
            logger.log_info(f"📝 OCO订单参数: {json.dumps(oco_params, indent=2)}")
            response = exchange.private_post_trade_order_algo(oco_params)
            log_api_response(response, "OCO订单")
            
            if response and response.get('code') == '0':
                algo_id = response['data'][0]['algoId']
                result['success'] = True
                result['algo_id'] = algo_id
                result['algo_cl_ord_id'] = oco_params['algoClOrdId']
                logger.log_info(f"✅ OCO订单创建成功 (algoId: {algo_id})")

        # 2. 仅止损有效：生成止损单
        elif has_stop_loss:
            sl_params = {
                **base_params,
                'ordType': 'conditional',
                'slTriggerPx': str(stop_loss_price),
                'slOrdPx': '-1',
                'algoClOrdId': generate_cl_ord_id(f"{side}")
            }
            logger.log_info(f"📝 止损订单参数: {json.dumps(sl_params, indent=2)}")
            response = exchange.private_post_trade_order_algo(sl_params)
            log_api_response(response, "止损订单")
            
            if response and response.get('code') == '0':
                algo_id = response['data'][0]['algoId']
                result['success'] = True
                result['algo_id'] = algo_id
                result['algo_cl_ord_id'] = sl_params['algoClOrdId']
                logger.log_info(f"✅ 止损订单创建成功 (algoId: {algo_id})")

        # 3. 仅止盈有效：生成止盈单
        elif has_take_profit:
            tp_params = {
                **base_params,
                'ordType': 'conditional',
                'tpTriggerPx': str(take_profit_price),
                'tpOrdPx': '-1',
                'algoClOrdId': generate_cl_ord_id(f"{side}_tp")
            }
            logger.log_info(f"📝 止盈订单参数: {json.dumps(tp_params, indent=2)}")
            response = exchange.private_post_trade_order_algo(tp_params)
            log_api_response(response, "止盈订单")
            
            if response and response.get('code') == '0':
                algo_id = response['data'][0]['algoId']
                result['success'] = True
                result['algo_id'] = algo_id
                result['algo_cl_ord_id'] = tp_params['algoClOrdId']
                logger.log_info(f"✅ 止盈订单创建成功 (algoId: {algo_id})")

        return result

    except Exception as e:
        result['success'] = False
        logger.log_error("sl_tp_algo_order_set", f"设置止损止盈失败: {str(e)}")
        return result

def cancel_existing_algo_orders(symbol: str):
    """取消指定品种的现有策略委托订单"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        params = {
            'instType': 'SWAP',
            'instId': get_correct_inst_id(symbol),
            'ordType': 'conditional,oco'
        }
        
        response = exchange.private_get_trade_orders_algo_pending(params)
        
        if response['code'] == '0' and response['data']:
            inst_id = get_correct_inst_id(symbol)
            canceled_count = 0
            
            for order in response['data']:
                if order['instId'] == inst_id:
                    # 取消策略委托订单
                    cancel_params = [{
                        'algoId': order['algoId'],
                        'instId': order['instId'],
                    }]
                    cancel_response = exchange.private_post_trade_cancel_algos(cancel_params)
                    if cancel_response['code'] == '0':
                        logger.log_info(f"✅ {get_base_currency(symbol)}: 取消策略委托订单: {order['algoId']}")
                        canceled_count += 1
                    else:
                        logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 取消策略委托订单失败: {cancel_response}")
            
            if canceled_count > 0:
                logger.log_info(f"✅ {get_base_currency(symbol)}: 成功取消 {canceled_count} 个策略委托订单")
            else:
                logger.log_info(f"ℹ️ {get_base_currency(symbol)}: 没有需要取消的策略委托订单")
        else:
            logger.log_info(f"✅ {get_base_currency(symbol)}: 没有找到待取消的策略委托订单")
                    
    except Exception as e:
        logger.log_error(f"cancel_algo_orders_{get_base_currency(symbol)}", str(e))


def set_breakeven_stop(symbol: str,current_position: dict, price_data: dict):
    """使用OKX算法订单设置保本止损"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        # 获取剩余仓位大小（假设已经止盈30%）
        remaining_size = current_position['size'] * 0.70  # 剩余70%
        remaining_size = round(remaining_size, 2)
        
        if remaining_size < getattr(config, 'min_amount', 0.01):
            logger.log_warning("⚠️ 剩余仓位太小，无法设置保本止损")
            return False
        
        sl_price = current_position['entry_price']      # 保本止损，所以止损价设置为开仓价
        existing_order_side = current_position['side']  # 持有仓位的方向
        logger.log_info(f"🛡️ 设置空头保本止损: 触发价{sl_price:.2f}, 数量{remaining_size}张")
        
        # 取消该交易对现有的所有条件单（避免重复）
        cancel_existing_algo_orders(symbol)
        
        # 创建算法订单
        result = sl_tp_algo_order_set(
        symbol=symbol,  # ✅ 修正参数名
        side= existing_order_side,
        amount = remaining_size,
        stop_loss_price = sl_price
        )
        if result['success']:
            logger.log_info("✅ 保本止损设置成功")
            return True
        else:
            logger.log_error("保本止损设置失败")
            return False
            
    except Exception as e:
        logger.log_error("breakeven_stop_setting", str(e))
        return False
    
def log_limit_order_params(order_type, params, limit_price, stop_loss_price, function_name=""):
    """记录限价单参数"""
    try:
        safe_params = params.copy()
        # ... 实现日志记录逻辑
        logger.log_info(f"📋 {function_name} - {order_type}限价单: 限价{limit_price:.2f}, 止损{stop_loss_price:.2f}")
    except Exception as e:
        logger.log_error("log_limit_order_params", f"记录限价单参数失败: {str(e)}")

class PositionManager:
    """持仓管理器，负责多级止盈逻辑"""
    
    def __init__(self):
        self.position_levels = {}  # 记录每个持仓的止盈级别
        
    def check_profit_taking(self, symbol: str, current_position, price_data):
        """检查是否需要执行多级止盈"""
        if not current_position:
            return None
            
        position_key = f"{current_position['side']}_{current_position['entry_price']}"
        
        # ✅ 正确的配置获取方式
        config = SYMBOL_CONFIGS[symbol]
        risk_config = config.get_risk_config()
        profit_taking_config = risk_config['profit_taking']
        
        if not profit_taking_config['enable_multilevel_take_profit']:
            return None
            
        current_price = price_data['price']
        entry_price = current_position['entry_price']
        
        if current_position['side'] == 'long':
            profit_ratio = (current_price - entry_price) / entry_price
        else:  # short
            profit_ratio = (entry_price - current_price) / entry_price
            
        # 检查每个止盈级别
        for i, level in enumerate(profit_taking_config['levels']):
            level_key = f"{position_key}_level_{i}"
            
            # 如果已经执行过这个级别的止盈，跳过
            if self.position_levels.get(level_key, False):
                continue
                
            # 检查是否达到止盈条件
            if profit_ratio >= level['profit_multiplier']:
                logger.log_info(f"🎯 达到止盈级别 {i+1}: 盈利{profit_ratio:.2%}倍, 触发条件{level['profit_multiplier']}倍")
                return {
                    'level': i,
                    'take_profit_ratio': level['take_profit_ratio'],
                    'set_breakeven_stop': level.get('set_breakeven_stop', False),
                    'description': level['description']
                }
                
        return None
        
    def mark_level_executed(self, symbol: str, current_position, level):
        """标记止盈级别已执行"""
        position_key = f"{current_position['side']}_{current_position['entry_price']}"
        level_key = f"{position_key}_level_{level}"
        self.position_levels[level_key] = True

# 创建全局持仓管理器实例
position_manager = PositionManager()


# Optimization: Add a unified error handling and retry decorator
def retry_on_failure(max_retries=None, delay=None, exceptions=(Exception,)):
    # """Unified error handling and retry decorator"""
    if max_retries is None:
        max_retries = 3
    if delay is None:
        delay = 2
        
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    logger.log_error(f"⚠️ {func.__name__} attempt {attempt + 1}", str(e))
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

@retry_on_failure(max_retries=3, delay=2)
def fetch_ohlcv_with_retry(symbol: str,max_retries=None):
    if max_retries is None:
        max_retries = 3

    # 从全局字典中获取该品种的配置
    config = SYMBOL_CONFIGS[symbol]

    for i in range(max_retries):
        try:
            return exchange.fetch_ohlcv(symbol, config.timeframe, limit=config.data_points)
        except Exception as e:
            logger.log_error(f"Get_kline_{get_base_currency(symbol)} failed, retry {i+1}/{max_retries}", str(e))
            time.sleep(1)
    return None

def fetch_ohlcv(symbol: str):
    """获取指定交易品种的K线数据 - 改进版"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        # 使用扩展的K线数据
        df = fetch_extended_ohlcv(symbol, hours=24)
        
        if df is None or len(df) < 50:
            logger.log_warning(f"❌ {get_base_currency(symbol)}: 扩展数据获取失败，使用原方法")
            ohlcv = fetch_ohlcv_with_retry(symbol)
            if ohlcv is None:
                return None, None
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df = calculate_technical_indicators(df)
        
        current_data = df.iloc[-1]
        previous_data = df.iloc[-2]

        # 使用多时间框架支撑阻力计算
        levels_analysis = calculate_multi_timeframe_support_resistance(df)
        trend_analysis = get_market_trend(df)
        
        # 添加趋势强度分析
        trend_strength_analysis = identify_trend_strength(df)
        trend_analysis['strength'] = trend_strength_analysis

        price_data = {
            'price': current_data['close'],
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'high': current_data['high'],
            'low': current_data['low'],
            'volume': current_data['volume'],
            'timeframe': config.timeframe,
            'price_change': ((current_data['close'] - previous_data['close']) / previous_data['close']) * 100,
            'kline_data': df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(10).to_dict('records'),
            'technical_data': {
                'sma_5': current_data.get('sma_5', 0),
                'sma_20': current_data.get('sma_20', 0),
                'sma_50': current_data.get('sma_50', 0),
                'rsi': current_data.get('rsi', 0),
                'macd': current_data.get('macd', 0),
                'macd_signal': current_data.get('macd_signal', 0),
                'macd_histogram': current_data.get('macd_histogram', 0),
                'bb_upper': current_data.get('bb_upper', 0),
                'bb_lower': current_data.get('bb_lower', 0),
                'bb_position': current_data.get('bb_position', 0),
                'volume_ratio': current_data.get('volume_ratio', 0)
            },
            'trend_analysis': trend_analysis,
            'levels_analysis': levels_analysis,
            'full_data': df,
            'trend_strength': trend_strength_analysis['trend_strength']
        }

        return df, price_data
        
    except Exception as e:
        logger.log_error(f"fetch_ohlcv_{get_base_currency(symbol)}", str(e))
        return None, None

def add_to_signal_history(symbol: str, signal_data):
    global signal_history
    
    # 初始化该品种的历史记录
    if symbol not in signal_history:
        signal_history[symbol] = []
    
    signal_history[symbol].append(signal_data)
    
    # Limit the history to 100 records
    max_history = 100
    if len(signal_history[symbol]) > max_history:
        keep_count = int(max_history * 0.8)
        signal_history[symbol] = signal_history[symbol][-keep_count:]

def add_to_price_history(symbol: str, price_data):
    global price_history
    
    if symbol not in price_history:
        price_history[symbol] = []
    
    price_history[symbol].append(price_data)
    
    # Limit the history to 200 records
    max_history = 200
    if len(price_history[symbol]) > max_history:
        keep_count = int(max_history * 0.8)
        price_history[symbol] = price_history[symbol][-keep_count:]

def verify_position_exists(symbol: str, position_info: dict) -> bool:
    """验证持仓是否真实存在 - 增强版本"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        # 方法1：通过账户余额验证
        balance = exchange.fetch_balance()
        total_balance = balance['total'].get('USDT', 0)
        
        if total_balance <= 0:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 账户余额异常")
            return False
        
        # 方法2：尝试获取更详细的持仓信息
        positions = exchange.fetch_positions([config.symbol])
        for pos in positions:
            if (pos['symbol'] == config.symbol and 
                float(pos.get('contracts', 0)) > 0 and
                pos.get('side') == position_info['side']):
                
                # 🆕 额外验证：检查持仓的详细信息
                if (pos.get('entryPrice') and 
                    float(pos['entryPrice']) > 0 and
                    pos.get('marginMode') in ['isolated', 'cross']):
                    return True
        
        # 方法3：使用私有API获取持仓
        try:
            params = {
                'instType': 'SWAP',
                'instId': get_correct_inst_id(symbol)
            }
            response = exchange.private_get_account_positions(params)
            
            if response['code'] == '0' and response['data']:
                for pos in response['data']:
                    if (pos['instId'] == get_correct_inst_id(symbol) and
                        float(pos.get('pos', 0)) > 0 and
                        pos.get('posSide') == 'net' and
                        ((position_info['side'] == 'long' and pos.get('posSide') == 'long') or 
                         (position_info['side'] == 'short' and pos.get('posSide') == 'short'))):
                        return True
        except Exception as api_error:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 私有API持仓查询失败: {str(api_error)}")
        
        # 方法4：如果上述方法都失败，记录详细日志
        logger.log_warning(f"🔍 {get_base_currency(symbol)}: 持仓验证失败 - 详细持仓信息:")
        for pos in positions:
            if pos['symbol'] == config.symbol:
                logger.log_warning(f"  - 合约: {pos.get('contracts')}, 方向: {pos.get('side')}, 模式: {pos.get('marginMode')}, 入场价: {pos.get('entryPrice')}")
        
        return False
        
    except Exception as e:
        logger.log_error(f"position_verification_{get_base_currency(symbol)}", f"持仓验证失败: {str(e)}")
        return False

def setup_trailing_stop(symbol: str, current_position: dict, price_data: dict) -> bool:
    """设置移动止损"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        risk_config = config.get_risk_config()
        trailing_config = risk_config['dynamic_stop_loss']
        
        if not trailing_config['enable_trailing_stop']:
            return False
            
        entry_price = current_position['entry_price']
        current_price = price_data['price']
        position_size = current_position['size']
        side = current_position['side']
        
        if side == 'long':
            profit_ratio = (current_price - entry_price) / entry_price
            if profit_ratio >= trailing_config['trailing_activation_ratio']:
                # 计算移动止损价格
                trailing_stop_price = current_price * (1 - trailing_config['trailing_distance_ratio'])
                
                # 确保移动止损不会低于入场价（保本）
                trailing_stop_price = max(trailing_stop_price, entry_price)
                
                logger.log_info(f"📈 {get_base_currency(symbol)}: 设置多头移动止损 - {trailing_stop_price:.2f} (当前盈利: {profit_ratio:.2%})")
                
                return set_trailing_stop_order(symbol, current_position, trailing_stop_price)
                
        else:  # short
            profit_ratio = (entry_price - current_price) / entry_price
            if profit_ratio >= trailing_config['trailing_activation_ratio']:
                # 计算移动止损价格
                trailing_stop_price = current_price * (1 + trailing_config['trailing_distance_ratio'])
                
                # 确保移动止损不会高于入场价（保本）
                trailing_stop_price = min(trailing_stop_price, entry_price)
                
                logger.log_info(f"📉 {get_base_currency(symbol)}: 设置空头移动止损 - {trailing_stop_price:.2f} (当前盈利: {profit_ratio:.2%})")
                
                return set_trailing_stop_order(symbol, current_position, trailing_stop_price)
                
        return False
        
    except Exception as e:
        logger.log_error(f"trailing_stop_setup_{get_base_currency(symbol)}", f"移动止损设置失败: {str(e)}")
        return False

def check_existing_algo_orders(symbol: str, position: dict) -> dict:
    """检查现有的策略委托订单，返回详细的订单分析 - 修复版本"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        algo_orders_analysis = {
            'has_stop_loss': False,
            'has_take_profit': False,
            'stop_loss_orders': [],
            'take_profit_orders': [],
            'oco_orders': [],
            'total_covered_size': 0,
            'remaining_size': position['size']
        }
        
        # 🆕 首先验证持仓是否存在
        if not verify_position_exists(symbol, position):
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 持仓验证失败，跳过订单检查")
            return algo_orders_analysis
        
        logger.log_info(f"✅ {get_base_currency(symbol)}: 持仓验证成功")
        
        # 🆕 修复：使用正确的算法订单类型参数
        try:
            # 检查条件单
            conditional_params = {
                'instType': 'SWAP',
                'instId': get_correct_inst_id(symbol),
                'ordType': 'conditional'  # 🆕 修复：使用正确的参数名
            }
            
            conditional_response = exchange.private_get_trade_orders_algo_pending(conditional_params)
            
            if conditional_response['code'] == '0' and conditional_response['data']:
                inst_id = get_correct_inst_id(symbol)
                
                for order in conditional_response['data']:
                    if order['instId'] == inst_id:
                        order_size = float(order.get('sz', 0))
                        
                        # 判断是止损单还是止盈单
                        if 'slTriggerPx' in order and order['slTriggerPx'] and float(order['slTriggerPx']) > 0:
                            algo_orders_analysis['has_stop_loss'] = True
                            algo_orders_analysis['stop_loss_orders'].append({
                                'algoId': order['algoId'],
                                'size': order_size,
                                'triggerPrice': float(order['slTriggerPx']),
                                'orderType': 'conditional'
                            })
                            algo_orders_analysis['total_covered_size'] += order_size
                        
                        if 'tpTriggerPx' in order and order['tpTriggerPx'] and float(order['tpTriggerPx']) > 0:
                            algo_orders_analysis['has_take_profit'] = True
                            algo_orders_analysis['take_profit_orders'].append({
                                'algoId': order['algoId'],
                                'size': order_size,
                                'triggerPrice': float(order['tpTriggerPx']),
                                'orderType': 'conditional'
                            })
                            algo_orders_analysis['total_covered_size'] += order_size
                            
        except Exception as e:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 条件单检查失败: {str(e)}")
        
        # 🆕 修复：检查OCO订单
        try:
            oco_params = {
                'instType': 'SWAP',
                'instId': get_correct_inst_id(symbol),
                'ordType': 'oco'  # 🆕 检查OCO订单
            }
            
            oco_response = exchange.private_get_trade_orders_algo_pending(oco_params)
            
            if oco_response['code'] == '0' and oco_response['data']:
                inst_id = get_correct_inst_id(symbol)
                
                for order in oco_response['data']:
                    if order['instId'] == inst_id:
                        order_size = float(order.get('sz', 0))
                        
                        algo_orders_analysis['oco_orders'].append({
                            'algoId': order['algoId'],
                            'size': order_size,
                            'stopLossPrice': float(order.get('slTriggerPx', 0)),
                            'takeProfitPrice': float(order.get('tpTriggerPx', 0)),
                            'orderType': 'oco'
                        })
                        algo_orders_analysis['total_covered_size'] += order_size
                        algo_orders_analysis['has_stop_loss'] = True
                        algo_orders_analysis['has_take_profit'] = True
                        
        except Exception as e:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: OCO订单检查失败: {str(e)}")
        
        # 🆕 修复：计算剩余仓位时考虑浮点数精度
        remaining_size = position['size'] - algo_orders_analysis['total_covered_size']
        
        # 🆕 添加精度容差（使用品种的最小交易单位）
        min_amount = getattr(config, 'min_amount', 0.01)
        precision_tolerance = min_amount * 0.1  # 使用最小交易单位的10%作为容差
        
        if abs(remaining_size) < precision_tolerance:
            remaining_size = 0
        
        algo_orders_analysis['remaining_size'] = max(0, remaining_size)
        
        logger.log_info(f"📊 {get_base_currency(symbol)}: 策略委托分析 - 止损: {algo_orders_analysis['has_stop_loss']}, "
                      f"止盈: {algo_orders_analysis['has_take_profit']}, "
                      f"已覆盖: {algo_orders_analysis['total_covered_size']:.6f}/{position['size']:.6f}张, "
                      f"剩余: {algo_orders_analysis['remaining_size']:.6f}张")
        
        return algo_orders_analysis
            
    except Exception as e:
        logger.log_error(f"check_existing_algo_orders_{get_base_currency(symbol)}", f"检查策略委托订单失败: {str(e)}")
        return {
            'has_stop_loss': False,
            'has_take_profit': False,
            'stop_loss_orders': [],
            'take_profit_orders': [],
            'oco_orders': [],
            'total_covered_size': 0,
            'remaining_size': position['size']
        }

# 🆕 --- 核心修改：智能化移动止损，不再取消止盈单 ---
def set_trailing_stop_order(symbol: str, current_position: dict, stop_price: float) -> bool:
    """
    设置移动止损订单 - 智能版
    
    此函数现在将:
    1. 检查现有的 *止损单*。
    2. 取消 *只* 取消旧的止损单 (保留止盈单)。
    3. 创建新的止损单。
    """
    config = SYMBOL_CONFIGS[symbol]
    try:
        side = current_position['side']
        position_size = current_position['size']
        
        # 1. 检查现有的策略订单
        orders_analysis = check_existing_algo_orders(symbol, current_position)
        
        # 2. 如果有旧的止损单，只取消它们
        if orders_analysis['has_stop_loss'] and orders_analysis['stop_loss_orders']:
            logger.log_info(f"🔄 {get_base_currency(symbol)}: 发现旧的止损单，正在取消...")
            cancel_specific_algo_orders(symbol, orders_analysis['stop_loss_orders'], 'conditional')
            time.sleep(1) # 等待交易所处理取消
        else:
            logger.log_info(f"ℹ️ {get_base_currency(symbol)}: 未发现旧止损单，直接创建新单。")

        # 3. 创建新的移动止损条件单
        logger.log_info(f"🎯 {get_base_currency(symbol)}: 创建新移动止损单于 {stop_price:.2f}")
        result = sl_tp_algo_order_set(
            symbol=symbol,
            side=side,
            amount=position_size,
            stop_loss_price=stop_price,
        )
        if result['success']:
            logger.log_info(f"✅ {get_base_currency(symbol)}: 新移动止损设置成功: {stop_price:.2f}")
            return True
        else:
            logger.log_error(f"set_trailing_stop_order_{get_base_currency(symbol)}", "移动止损设置失败")
            return False
            
    except Exception as e:
        logger.log_error(f"set_trailing_stop_order_{get_base_currency(symbol)}", str(e))
        return False
    # ✅ --- 修改结束 ---

def execute_profit_taking(symbol: str, current_position: dict, profit_taking_signal: dict, price_data: dict):
    """执行多级止盈逻辑 - 永续合约市价平仓"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        order_tag = create_order_tag()
        position_size = current_position['size']
        take_profit_ratio = profit_taking_signal['take_profit_ratio']
        
        # 计算需要平仓的数量
        close_size = position_size * take_profit_ratio
        close_size = round(close_size, 2)
        
        if close_size < getattr(config, 'min_amount', 0.01):
            close_size = getattr(config, 'min_amount', 0.01)
            
        logger.log_info(f"💰 执行部分止盈: 平仓{close_size:.2f}张合约 ({take_profit_ratio:.1%}仓位)")

        # 🆕 记录止盈操作到持仓历史
        add_to_position_history(symbol, {
            'side': current_position['side'],
            'size': close_size,
            'entry_price': current_position['entry_price'],
            'action': 'partial_close',
            'close_reason': f'profit_taking_level_{profit_taking_signal["level"]}',
            'take_profit_ratio': take_profit_ratio
        })

        if not config.test_mode:
            # 记录止盈订单参数 - 永续合约市价平仓
            if current_position['side'] == 'long':
                profit_params = {
                    'reduceOnly': True,
                    'tag': order_tag,
                    'symbol': config.symbol,
                    'side': 'sell',
                    'amount': close_size,
                    'type': 'market',
                    'profit_taking_ratio': take_profit_ratio,
                    'original_position_size': position_size
                }
                log_order_params("永续合约止盈平仓", profit_params, "execute_profit_taking")
                log_perpetual_order_details(symbol, 'sell', close_size, 'market', reduce_only=True, take_profit=True)
                
                exchange.create_market_order(
                    config.symbol,
                    'sell',
                    close_size,
                    params={'reduceOnly': True, 'tag': order_tag}
                )
            else:  # short
                profit_params = {
                    'reduceOnly': True,
                    'tag': order_tag,
                    'symbol': config.symbol,
                    'side': 'buy',
                    'amount': close_size,
                    'type': 'market',
                    'profit_taking_ratio': take_profit_ratio,
                    'original_position_size': position_size
                }
                log_order_params("永续合约止盈平仓", profit_params, "execute_profit_taking")
                log_perpetual_order_details(symbol,'buy', close_size, 'market', reduce_only=True, take_profit=True)
                
                exchange.create_market_order(
                    config.symbol,
                    'buy',
                    close_size,
                    params={'reduceOnly': True, 'tag': order_tag}
                )
            
            # 记录止盈订单执行结果
            logger.log_info(f"✅ 永续合约止盈订单执行完成: 平仓{close_size}张")
            
            # 如果设置保本止损，更新剩余仓位的止损
            if profit_taking_signal.get('set_breakeven_stop', False):
                logger.log_info("🛡️ 设置保本止损...")
                set_breakeven_stop(symbol, current_position, price_data)
                
        logger.log_info("✅ 多级止盈执行完成")
        
    except Exception as e:
        logger.log_error("profit_taking_execution", str(e))


def cancel_specific_algo_orders(symbol: str, algo_orders: list, order_type: str = 'conditional'):
    """取消特定的策略委托订单"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        canceled_count = 0
        
        for order in algo_orders:
            cancel_params = {
                'algoId': order['algoId'],
                'instId': get_correct_inst_id(symbol),
                'algoOrdType': order_type
            }
            
            cancel_response = exchange.privatePostTradeCancelAlgoOrder(cancel_params)
            if cancel_response['code'] == '0':
                logger.log_info(f"✅ {get_base_currency(symbol)}: 取消策略委托订单: {order['algoId']}")
                canceled_count += 1
            else:
                logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 取消策略委托订单失败: {cancel_response}")
        
        if canceled_count > 0:
            logger.log_info(f"✅ {get_base_currency(symbol)}: 成功取消 {canceled_count} 个策略委托订单")
        
        return canceled_count
                    
    except Exception as e:
        logger.log_error(f"cancel_specific_algo_orders_{get_base_currency(symbol)}", str(e))
        return 0

def setup_missing_stop_loss_take_profit(symbol: str, position: dict, price_data: dict, orders_analysis: dict):
    """设置缺失的止损止盈订单 - 修复方向逻辑"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        current_price = price_data['price']
        position_side = position['side']
        remaining_size = orders_analysis['remaining_size']
        
        # 🆕 修复：添加精度容差检查
        min_amount = getattr(config, 'min_amount', 0.01)
        precision_tolerance = min_amount * 0.1
        
        # 如果剩余仓位小于精度容差，认为已完全覆盖
        if abs(remaining_size) < precision_tolerance:
            logger.log_info(f"✅ {get_base_currency(symbol)}: 止盈止损已完全覆盖持仓（精度容差内）")
            return True
            
        if remaining_size <= 0:
            logger.log_info(f"✅ {get_base_currency(symbol)}: 止盈止损已完全覆盖持仓")
            return True
        
        # 计算止损价格
        risk_config = config.get_risk_config()
        stop_loss_config = risk_config['stop_loss']
        take_profit_price = None
        stop_loss_price = None

        if position_side == 'long':
            if stop_loss_config['kline_based_stop_loss']:
                stop_loss_price = sl_tp_strategy.calculate_kline_based_stop_loss(
                    'long', current_price, price_data, stop_loss_config['max_stop_loss_ratio']
                )
            else:
                stop_loss_price = current_price * (1 - stop_loss_config['min_stop_loss_ratio'])
                
            # 多头止盈计算
            take_profit_price = sl_tp_strategy.calculate_intelligent_take_profit(
                symbol, 'long', position['entry_price'], price_data, risk_reward_ratio=2.0
            )
        else:  # short
            if stop_loss_config['kline_based_stop_loss']:
                stop_loss_price = sl_tp_strategy.calculate_kline_based_stop_loss(
                    'short', current_price, price_data, stop_loss_config['max_stop_loss_ratio']
                )
            else:
                stop_loss_price = current_price * (1 + stop_loss_config['min_stop_loss_ratio'])
                
            # 空头止盈计算
            take_profit_price = sl_tp_strategy.calculate_intelligent_take_profit(
                symbol, 'short', position['entry_price'], price_data, risk_reward_ratio=2.0
            )
        
        # 根据缺失情况设置相应的订单
        success = True
        
        # 没有止损/止盈，设置止盈止损
        if not orders_analysis['has_stop_loss'] or not orders_analysis['has_take_profit']:
            logger.log_info(f"🆕 {get_base_currency(symbol)}: 设置止盈止损 - 数量{remaining_size}张")
            
            result = sl_tp_algo_order_set(
                symbol=symbol,
                side=position_side,  # 🆕 使用正确的平仓方向
                amount=remaining_size,
                stop_loss_price=stop_loss_price,
                take_profit_price=take_profit_price
            )

            if not result['success']:
                success = False
                logger.log_error(f"{get_base_currency(symbol)}:止盈止损设置失败")

        if success:
            logger.log_info(f"✅ {get_base_currency(symbol)}: 缺失止盈止损设置完成")
            logger.log_info(f"📊 {get_base_currency(symbol)}: 止损价 {stop_loss_price:.2f}, 止盈价 {take_profit_price:.2f}")
        else:
            logger.log_error(f"missing_orders_setup_{get_base_currency(symbol)}", "缺失止盈止损设置失败")
            
        return success
            
    except Exception as e:
        logger.log_error(f"setup_missing_stop_loss_take_profit_{get_base_currency(symbol)}", f"设置缺失止盈止损失败: {str(e)}")
        return False


def check_and_set_stop_loss(symbol: str, position: dict, price_data: dict):
    """检查并设置止损和止盈订单 - 增强版本"""
    try:
        config = SYMBOL_CONFIGS[symbol]
        
        # 🆕 详细检查现有的策略委托订单
        orders_analysis = check_existing_algo_orders(symbol, position)
        
        # 情况分析并记录日志
        if not orders_analysis['has_stop_loss'] and not orders_analysis['has_take_profit']:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 未设置任何止盈止损订单")
        elif orders_analysis['has_stop_loss'] and not orders_analysis['has_take_profit']:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 已设置止损但未设置止盈")
        elif not orders_analysis['has_stop_loss'] and orders_analysis['has_take_profit']:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 已设置止盈但未设置止损")
        elif orders_analysis['remaining_size'] > 0:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 止盈止损未完全覆盖持仓 (剩余{orders_analysis['remaining_size']}张)")
        else:
            logger.log_info(f"✅ {get_base_currency(symbol)}: 止盈止损已完全设置")
            return True
        
        # 设置缺失的止盈止损
        success = setup_missing_stop_loss_take_profit(symbol, position, price_data, orders_analysis)
        
        return success
            
    except Exception as e:
        logger.log_error(f"stop_loss_check_{get_base_currency(symbol)}", f"止损止盈检查设置失败: {str(e)}")
        return False

def optimize_existing_orders(symbol: str, position: dict, price_data: dict):
    """优化现有订单：取消不合理的订单，重新设置"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        orders_analysis = check_existing_algo_orders(symbol, position)
        current_price = price_data['price']
        position_side = position['side']
        
        canceled_count = 0
        
        # 检查并取消不合理的止损单
        for stop_loss_order in orders_analysis['stop_loss_orders']:
            trigger_price = stop_loss_order['triggerPrice']
            
            # 多头：止损价格不合理（高于当前价格或过于接近）
            if position_side == 'long' and trigger_price >= current_price * 0.99:
                logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 取消不合理的多头止损单 - 触发价{trigger_price:.2f}过于接近当前价{current_price:.2f}")
                canceled_count += cancel_specific_algo_orders(symbol, [stop_loss_order], 'conditional')
            
            # 空头：止损价格不合理（低于当前价格或过于接近）
            elif position_side == 'short' and trigger_price <= current_price * 1.01:
                logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 取消不合理的空头止损单 - 触发价{trigger_price:.2f}过于接近当前价{current_price:.2f}")
                canceled_count += cancel_specific_algo_orders(symbol, [stop_loss_order], 'conditional')
        
        # 检查并取消不合理的止盈单
        for take_profit_order in orders_analysis['take_profit_orders']:
            trigger_price = take_profit_order['triggerPrice']
            
            # 多头：止盈价格不合理（低于当前价格）
            if position_side == 'long' and trigger_price <= current_price:
                logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 取消不合理的多头止盈单 - 触发价{trigger_price:.2f}低于当前价{current_price:.2f}")
                canceled_count += cancel_specific_algo_orders(symbol, [take_profit_order], 'conditional')
            
            # 空头：止盈价格不合理（高于当前价格）
            elif position_side == 'short' and trigger_price >= current_price:
                logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 取消不合理的空头止盈单 - 触发价{trigger_price:.2f}高于当前价{current_price:.2f}")
                canceled_count += cancel_specific_algo_orders(symbol, [take_profit_order], 'conditional')
        
        # 如果有取消的订单，重新设置止盈止损
        if canceled_count > 0:
            logger.log_info(f"🔄 {get_base_currency(symbol)}: 重新设置被取消的止盈止损订单")
            time.sleep(1)  # 等待取消操作完成
            return check_and_set_stop_loss(symbol, position, price_data)
        
        return True
            
    except Exception as e:
        logger.log_error(f"optimize_existing_orders_{get_base_currency(symbol)}", f"优化现有订单失败: {str(e)}")
        return False

def close_position_safely(symbol: str, position: dict, reason: str = "反向开仓平仓") -> bool:
    """
    安全平仓函数 - 统一版本，支持市价平仓和限价平仓
    返回是否成功
    """
    config = SYMBOL_CONFIGS[symbol]
    try:
        # 🆕 双重验证：重新获取持仓信息
        current_position = get_current_position(symbol)
        if not current_position:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 持仓验证失败，实际无持仓")
            return True  # 返回True表示"成功"，因为无需平仓
            
        # 🆕 验证持仓方向是否匹配
        if current_position['side'] != position['side']:
            logger.log_error(f"close_position_{get_base_currency(symbol)}", 
                           f"持仓方向不匹配: 预期{position['side']}, 实际{current_position['side']}")
            return False
            
        # 🆕 验证持仓数量
        if current_position['size'] <= 0:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 持仓数量为0，无需平仓")
            return True
        
        position_side = current_position['side']  # 'long' or 'short'
        position_size = current_position['size']
        
        logger.log_info(f"🔄 {get_base_currency(symbol)}: {reason} - 平{position_size}张")

        # 🆕 记录平仓前的持仓信息到历史
        add_to_position_history(symbol, {
            'side': position_side,
            'size': position_size,
            'entry_price': current_position['entry_price'],
            'action': 'close',
            'close_reason': reason
        })

        # 🆕 取消该品种的所有策略委托订单
        logger.log_info(f"🔄 {get_base_currency(symbol)}: 平仓前取消所有策略委托订单")
        cancel_existing_algo_orders(symbol)
        time.sleep(1)  # 等待取消操作完成

        # 🆕 使用全能平仓逻辑
        try:
            # 1. 确定平仓方向（与原持仓方向相反）
            close_side = 'sell' if position_side in ('buy', 'long') else 'buy'
            action_name = f"{'多头' if position_side in ('buy', 'long') else '空头'}市价平仓"
            
            # 2. 获取必要参数
            inst_id = get_correct_inst_id(symbol)
            current_price = get_current_price(symbol)
            
            if current_price == 0:
                error_msg = "无法获取当前价格，无法执行平仓操作"
                logger.log_error(f"❌ {get_base_currency(symbol)}: {error_msg}")
                return False
            
            # 3. 处理平仓数量
            if position_size <= 0:
                error_msg = "持仓数量无效，无法平仓"
                logger.log_error(f"❌ {get_base_currency(symbol)}: {error_msg}")
                return False

            # 4. 生成自定义订单ID
            cl_ord_id = generate_cl_ord_id(close_side)
            
            # 5. 构建ccxt标准化订单参数
            order_params = {
                'symbol': config.symbol,
                'type': 'market',
                'side': close_side,
                'amount': position_size,
                'params': {
                    'tdMode': config.margin_mode,
                    'reduceOnly': True,
                    'tag': create_order_tag()
                }
            }
            
            # 6. 打印订单信息
            logger.log_info(f"📤 {get_base_currency(symbol)}: {action_name}参数:")
            logger.log_info(f"  方向: {close_side}, 数量: {position_size}, 类型: market")
            logger.log_info(f"🎯 {get_base_currency(symbol)}: 执行{action_name}: {position_size} 张")
            
            # 7. 执行平仓订单（使用ccxt标准化接口）
            if not config.test_mode:
                response = exchange.create_order(
                    symbol=order_params['symbol'],
                    type=order_params['type'],
                    side=order_params['side'],
                    amount=order_params['amount'],
                    price=None,
                    params=order_params['params']
                )
                
                # 8. 处理API响应
                logger.log_info(f"📥 {get_base_currency(symbol)}: {action_name}响应:")
                logger.log_info(f"  订单ID: {response.get('id', 'Unknown')}, 状态: {response.get('status', 'Unknown')}")
                
                # 修复：改进订单状态检查逻辑
                order_id = response.get('id')
                if not order_id:
                    error_msg = f"订单创建失败: {response}"
                    logger.log_error(f"❌ {get_base_currency(symbol)}: {action_name}失败: {error_msg}")
                    # 🆕 尝试备用方法
                    return close_position_fallback(symbol, position, reason)
                
                # 对于市价单，只要订单创建成功就认为成功
                logger.log_info(f"✅ {get_base_currency(symbol)}: {action_name}订单创建成功: {order_id}")
            else:
                logger.log_info(f"✅ {get_base_currency(symbol)}: 测试模式 - {action_name}模拟成功")
                order_id = "test_order_id"

            # 9. 重置加仓状态
            reset_scaling_status(symbol)
            
            # 10. 等待并验证平仓结果
            return verify_position_closed(symbol, position_size, position_side)
                    
        except Exception as inner_e:
            error_msg = f"{get_base_currency(symbol)}: 平仓异常: {str(inner_e)}"
            logger.log_error(f"close_position_inner_{get_base_currency(symbol)}", error_msg)
            logger.log_error(f"close_position_traceback_{get_base_currency(symbol)}", traceback.format_exc())
            # 🆕 尝试备用方法
            return close_position_fallback(symbol, position, reason)
                
    except Exception as e:
        logger.log_error(f"close_position_{get_base_currency(symbol)}", f"平仓失败: {str(e)}")
        # 🆕 尝试备用方法
        return close_position_fallback(symbol, position, reason)

def verify_position_closed(symbol: str, expected_size: float, side: str) -> bool:
    """验证持仓是否已平"""
    max_retries = 3
    retry_delay = 2
    
    for i in range(max_retries):
        try:
            time.sleep(retry_delay)
            current_position = get_current_position(symbol)
            
            if current_position is None:
                logger.log_info(f"✅ {get_base_currency(symbol)}: 持仓验证通过 - 已完全平仓")
                return True
                
            # 检查持仓量是否减少
            remaining_size = current_position['size']
            if remaining_size < expected_size * 0.1:  # 允许10%的误差
                logger.log_info(f"✅ {get_base_currency(symbol)}: 持仓验证通过 - 剩余{remaining_size}张")
                return True
            else:
                logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 第{i+1}次验证 - 仍有{remaining_size}张未平")
                
        except Exception as e:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 第{i+1}次验证失败: {str(e)}")
    
    logger.log_error(f"❌ {get_base_currency(symbol)}: 持仓验证失败 - 可能未完全平仓")
    return False


def create_order_with_sl_tp(symbol: str, side: str, amount: float, order_type: str = 'market', 
                           limit_price: float = None, stop_loss_price: float = None, 
                           take_profit_price: float = None):
    """
    创建订单并同时设置止损止盈 - 使用OKX新的attachAlgoOrds API
    支持市价单和限价单
    """
    config = SYMBOL_CONFIGS[symbol]
    try:
        # 🆕 新增：检查仓位是否有效
        min_amount = getattr(config, 'min_amount', 0.01)
        if amount < min_amount:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 仓位大小 {amount:.4f} 小于最小交易量 {min_amount}，跳过开仓")
            return None
        
        inst_id = get_correct_inst_id(symbol)

        # 🆕 --- 动态合约数量精度调整 ---
        step_size = config.amount_precision_step
        min_size = config.min_amount
        
        if config.requires_integer:
            # 整数合约品种 (向上取整, 确保不小于最小量)
            adjusted_amount = max(min_size, math.ceil(amount)) 
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 整数张合约调整 - 从 {amount:.4f} 调整为 {adjusted_amount} 张")
        else:
            # 非整数合约品种 (向下取整到有效步长)
            if step_size > 0:
                adjusted_amount = math.floor(amount / step_size) * step_size
            else:
                adjusted_amount = round(amount, 8) # Fallback
            
            # 确保不小于最小交易量
            if adjusted_amount < min_size:
                 adjusted_amount = min_size

        # 如果调整后的数量与原数量不同，记录警告
        # (使用步长的 1% 作为浮点数比较的容差)
        if abs(adjusted_amount - amount) > (step_size * 0.01):
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 订单数量从 {amount:.4f} 调整为 {adjusted_amount:.4f} 以满足交易所精度要求")
        
        # 🆕 额外检查：确保调整后的数量仍然有效
        if adjusted_amount <= 0:
            logger.log_error(f"❌ {get_base_currency(symbol)}: 调整后的合约数量无效: {adjusted_amount}")
            return None

        # 基础参数
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': side,
            'ordType': order_type,
            'sz': str(adjusted_amount),  # 🆕 使用调整后的数量
        }
        
        # 🆕 --- 动态价格精度调整 ---
        price_step = config.price_precision_step

        if order_type == 'limit':
            # ...
            # 动态调整限价单价格
            if price_step > 0:
                # OKX 通常要求价格是 price_step 的倍数
                limit_price = round(limit_price / price_step) * price_step
            
            params['px'] = str(limit_price)
        

        # 添加止损止盈参数
        if stop_loss_price is not None and take_profit_price is not None:
            
            # 动态调整止损止盈价格
            if price_step > 0:
                stop_loss_price = round(stop_loss_price / price_step) * price_step
                take_profit_price = round(take_profit_price / price_step) * price_step

            sl_price_str = str(stop_loss_price)
            tp_price_str = str(take_profit_price)

            params['attachAlgoOrds'] = [
                {
                    'tpTriggerPx': tp_price_str,
                    'tpOrdPx': '-1',  # 市价止盈
                    'slTriggerPx': sl_price_str,
                    'slOrdPx': '-1',  # 市价止损
                    'algoOrdType': 'conditional',  # 条件单类型
                    'sz': str(adjusted_amount),  # 🆕 使用调整后的数量
                    'side': 'buy' if side == 'sell' else 'sell'  # 止损止盈方向与开仓方向相反
                }
            ]
        
        # 记录订单参数
        order_type_name = "市价单" if order_type == 'market' else "限价单"
        log_order_params(f"{order_type_name}带止损止盈", params, "create_order_with_sl_tp")
        
        logger.log_info(f"🎯 {get_base_currency(symbol)}: 执行{order_type_name}{side}开仓: {adjusted_amount:.4f} 张")
        
        if stop_loss_price is not None:
            logger.log_info(f"🛡️ {get_base_currency(symbol)}: 止损价格: {stop_loss_price:.2f}")
                
        if take_profit_price is not None:
            logger.log_info(f"🎯 {get_base_currency(symbol)}: 止盈价格: {take_profit_price:.2f}")
        
        # 使用CCXT的私有API方法调用/trade/order接口
        response = exchange.private_post_trade_order(params)
        
        log_api_response(response, "create_order_with_sl_tp")
        
        if response and response.get('code') == '0':
            order_id = response['data'][0]['ordId'] if response.get('data') else 'Unknown'
            logger.log_info(f"✅ {get_base_currency(symbol)}: {order_type_name}创建成功: {order_id}")
            return response
        else:
            logger.log_error(f"order_creation_failed_{get_base_currency(symbol)}", f"❌ {order_type_name}创建失败: {response}")
            return response
            
    except Exception as e:
        logger.log_error(f"order_creation_exception_{get_base_currency(symbol)}", f"{order_type_name}开仓失败: {str(e)}")
        import traceback
        logger.log_error(f"order_traceback_{get_base_currency(symbol)}", f"详细错误信息: {traceback.format_exc()}")
        return None    

def execute_intelligent_trade(symbol: str, signal_data: dict, price_data: dict):
    """执行智能交易 - 添加整体仓位管理"""
    global position
    config = SYMBOL_CONFIGS[symbol]
    
    # 对于HOLD信号，直接返回
    if signal_data['signal'] == 'HOLD':
        logger.log_info(f"⏸️ {get_base_currency(symbol)}: 保持观望，不执行交易")
        return
    
    # 验证价格数据完整性
    if not price_data or 'price' not in price_data:
        logger.log_error(f"invalid_price_data_{get_base_currency(symbol)}", "价格数据无效")
        return

    current_price = price_data['price']
    signal_side = 'long' if signal_data['signal'] == 'BUY' else 'short'
    current_position = get_current_position(symbol)
    
    # 🆕 修复：始终使用信号方向来计算止损止盈
    position_side = signal_side  # 始终使用信号方向
    
    # 🆕 修复：正确判断加仓条件
    is_scaling = current_position and current_position['size'] > 0 and current_position['side'] == signal_side
    
    # 🆕 修复：如果持仓方向与信号方向相反，应该先平仓
    if current_position and current_position['side'] != signal_side:
        logger.log_info(f"🔄 {get_base_currency(symbol)}: 持仓方向{current_position['side']}与信号方向{signal_side}相反，先平仓")
        close_success = close_position_safely(symbol, current_position, f"反向信号平仓: {signal_side}")
        if close_success:
            # 平仓成功后，重置持仓状态
            current_position = None
            reset_scaling_status(symbol)
        else:
            logger.log_error(f"❌ {get_base_currency(symbol)}: 平仓失败，放弃开仓")
            return
    
    # 🆕 修复：预先定义变量
    tp_result = None
    actual_rr = 0
    dynamic_min_rr = 1.2
    stop_loss_price = None  # 初始化为None
    take_profit_price = None  # 初始化为None

    if is_scaling:
        try:
            # 🆕 修复：使用过滤后的当前持仓历史
            position_history = get_current_position_history(symbol)
            
            # 🆕 修复：传入当前持仓以确保方向正确
            overall_levels = sl_tp_strategy.calculate_overall_stop_loss_take_profit(
                symbol, position_history, current_position, current_price, price_data
            )
            
            stop_loss_price = overall_levels['stop_loss']
            take_profit_price = overall_levels['take_profit']
            
            logger.log_info(f"📊 {get_base_currency(symbol)}: 加仓整体止损止盈 - 平均成本:{overall_levels['weighted_entry']:.2f}, 总仓位:{overall_levels['total_size']}张, 方向:{current_position['side']}")
            
            # 🆕 修复：使用当前持仓方向计算盈亏比
            if current_position['side'] == 'long':
                risk = current_price - stop_loss_price
                reward = take_profit_price - current_price
            else:
                risk = stop_loss_price - current_price
                reward = current_price - take_profit_price
                
            actual_rr = reward / risk if risk > 0 else 0
            
            tp_result = {
                'is_acceptable': True,
                'actual_risk_reward': actual_rr
            }
        
        except Exception as e:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 加仓止损计算失败: {str(e)}")
            is_scaling = False
    
    if not is_scaling:
        # 🆕 非加仓情况：在这里计算止损止盈
        stop_loss_price = sl_tp_strategy.calculate_adaptive_stop_loss(symbol, position_side, current_price, price_data)        
        # 动态盈亏比
        trend_strength = price_data['trend_strength']
        
        tp_result = sl_tp_strategy.calculate_aggressive_take_profit(
            symbol, position_side, current_price, stop_loss_price, 
            price_data, dynamic_min_rr, trend_strength
        )
        take_profit_price = tp_result['take_profit']
        actual_rr = tp_result['actual_risk_reward']

    # 🆕 修复：添加详细的价格关系验证日志
    logger.log_info(f"🔍 {get_base_currency(symbol)}: 价格关系验证 - 方向:{position_side}, 入场:{current_price:.2f}, 止损:{stop_loss_price:.2f}, 止盈:{take_profit_price:.2f}")
    
    if not sl_tp_strategy.validate_price_relationship(current_price, stop_loss_price, take_profit_price, position_side):
        logger.log_error(f"price_validation_failed_{get_base_currency(symbol)}", f"❌ {get_base_currency(symbol)}: 价格关系验证失败，放弃开仓")
        
        # 🆕 尝试自动修正价格
        logger.log_info(f"🔄 {get_base_currency(symbol)}: 尝试自动修正价格...")
        if position_side == 'long':
            # 多头修正
            corrected_stop_loss = current_price * 0.98
            corrected_take_profit = current_price * 1.03
        else:
            # 空头修正
            corrected_stop_loss = current_price * 1.02
            corrected_take_profit = current_price * 0.97
        
        if sl_tp_strategy.validate_price_relationship(current_price, corrected_stop_loss, corrected_take_profit, position_side):
            stop_loss_price = corrected_stop_loss
            take_profit_price = corrected_take_profit
            logger.log_info(f"✅ {get_base_currency(symbol)}: 价格自动修正成功")
            
            # 🆕 修复：价格修正后重新计算 actual_rr 和 tp_result
            if position_side == 'long':
                risk = current_price - stop_loss_price
                reward = take_profit_price - current_price
            else:
                risk = stop_loss_price - current_price
                reward = current_price - take_profit_price
            actual_rr = reward / risk if risk > 0 else 0
            
            # 🆕 修复：重新创建 tp_result
            tp_result = {
                'is_acceptable': actual_rr >= dynamic_min_rr * 0.8,  # 使用宽松条件
                'actual_risk_reward': actual_rr,
                'take_profit': take_profit_price
            }
            
        else:
            logger.log_error(f"price_correction_failed_{get_base_currency(symbol)}", "价格自动修正失败")
            return

    # 🆕 修复：添加安全性检查
    if tp_result is None:
        logger.log_error(f"tp_result_missing_{get_base_currency(symbol)}", "❌ tp_result 未定义，放弃开仓")
        return
        
    if 'actual_risk_reward' not in tp_result or tp_result['actual_risk_reward'] <= 0:
        logger.log_error(f"invalid_rr_{get_base_currency(symbol)}", f"❌ {get_base_currency(symbol)}: 无效盈亏比 {tp_result.get('actual_risk_reward', '未定义')}，放弃开仓")
        return
    
    # 🆕 步骤4: 放宽接受条件
    if not tp_result.get('is_acceptable', True):
        # 即使不满足完整阈值，如果盈亏比合理也可以考虑
        actual_rr = tp_result.get('actual_risk_reward', 0)
        if actual_rr >= 0.8:  # 最低可接受盈亏比
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 盈亏比{actual_rr:.2f}略低于阈值{dynamic_min_rr:.2f}，但仍可接受")
        else:
            logger.log_warning(f"🚫 {get_base_currency(symbol)}: 盈亏比{actual_rr:.2f}过低，放弃开仓")
            return

    # 计算仓位
    position_size = calculate_enhanced_position(symbol, signal_data, price_data, get_current_position(symbol))

    # 🆕 新增：严格检查仓位有效性
    min_amount = getattr(config, 'min_amount', 0.01)
    if position_size < min_amount:
        logger.log_warning(f"⏸️ {get_base_currency(symbol)}: 计算仓位 {position_size:.4f} 小于最小交易量 {min_amount}，放弃开仓")
        return
    
    # 🆕 资金充足性检查
    if not check_sufficient_margin(symbol, position_size, current_price):
        logger.log_error("资金不足",f"❌ {get_base_currency(symbol)}: 放弃开仓")
        return
    
    # 记录交易分析
    trade_analysis = f"""
    🎯 {get_base_currency(symbol)} 改进版交易分析:
    ├── 信号: {signal_data['signal']}
    ├── 入场价格: {current_price:.2f}
    ├── 止损位置: {stop_loss_price:.2f}
    ├── 止盈位置: {take_profit_price:.2f}
    ├── 实际盈亏比: {actual_rr:.2f}:1
    ├── 目标阈值: {dynamic_min_rr:.2f}:1
    ├── 仓位大小: {position_size:.2f}张
    └── 状态: {'✅ 满足开仓条件' if tp_result.get('is_acceptable', False) else '⚠️ 条件放宽'}
    """
    logger.log_info(trade_analysis)

    # 更新信号数据
    signal_data['stop_loss'] = stop_loss_price
    signal_data['take_profit'] = take_profit_price

    # 🆕 安全地记录日志
    try:
        logger.log_info(f"🎯 {get_base_currency(symbol)}: 交易执行 - {signal_data['signal']} | 仓位: {position_size:.2f}张 | 止损: {stop_loss_price:.2f} | 止盈: {take_profit_price:.2f}")
    except Exception as log_error:
        logger.log_info(f"🎯 {get_base_currency(symbol)}: 交易执行 - {signal_data['signal']} | 仓位: {position_size:.2f}张")
        logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 日志格式化失败: {str(log_error)}")

    if config.test_mode:
        logger.log_info(f"测试模式 - {get_base_currency(symbol)}: 仅模拟交易")
        return

    # 🆕 只有通过所有验证才执行实际交易
    try:
        # 获取订单簿数据
        order_book = exchange.fetch_order_book(config.symbol)

        # 提取买二价和卖二价
        bid_price = order_book['bids'][1][0] if len(order_book['bids']) >= 2 else order_book['bids'][0][0]
        ask_price = order_book['asks'][1][0] if len(order_book['asks']) >= 2 else order_book['asks'][0][0]
        logger.log_info(f"📊 {get_base_currency(symbol)}: 执行开仓 - 执行价格{current_price:.2f}, 买二{bid_price:.2f}, 卖二{ask_price:.2f}")

        current_position = get_current_position(symbol)
        
        # 执行交易逻辑
        if signal_data['signal'] == 'BUY':
            # 检查是否有现有空头持仓，先平仓
            if current_position and current_position['side'] == 'short':
                logger.log_info(f"🔄 {get_base_currency(symbol)}: 平空仓开多仓 - 平{current_position['size']}张，开{position_size}张")
                
                close_success = close_position_safely(symbol, current_position, "反向开仓平空仓")
                if not close_success:
                    logger.log_error(f"close_position_failed_{get_base_currency(symbol)}", f"❌ {get_base_currency(symbol)}: 平仓失败，放弃开多仓")
                    return
                time.sleep(2)

            # 🆕 修复：传入浮点数而不是字符串
            order_result = create_order_with_sl_tp(
                symbol=symbol,
                side='buy',
                amount=position_size,  # 直接传入浮点数
                order_type='limit',
                limit_price=ask_price,  # 直接传入浮点数
                stop_loss_price=stop_loss_price,  # 直接传入浮点数
                take_profit_price=take_profit_price  # 直接传入浮点数
            )

            if order_result and order_result.get('code') == '0':
                order_id = order_result['data'][0]['ordId']
                logger.log_info(f"✅ {get_base_currency(symbol)}: 限价开多仓提交-{position_size:.2f}张, 订单ID: {order_id}")
                # 🆕 记录开仓操作到持仓历史
                add_to_position_history(symbol, {
                    'side': 'long' if signal_data['signal'] == 'BUY' else 'short',
                    'size': position_size,
                    'entry_price': current_price,
                    'action': 'open',
                    'order_id': order_id,
                    'signal_confidence': signal_data['confidence']
                })
            else:
                logger.log_error(f"buy_order_failed_{get_base_currency(symbol)}", f"❌ {get_base_currency(symbol)}: 限价开多仓提交失败")
                return

        elif signal_data['signal'] == 'SELL':
            # 检查是否有现有多头持仓，先平仓
            if current_position and current_position['side'] == 'long':
                logger.log_info(f"🔄 {get_base_currency(symbol)}: 平多仓开空仓 - 平{current_position['size']}张，开{position_size}张")
                
                close_success = close_position_safely(symbol, current_position, "反向开仓平多仓")
                if not close_success:
                    logger.log_error(f"close_position_failed_{get_base_currency(symbol)}", f"❌ {get_base_currency(symbol)}: 平仓失败，放弃开空仓")
                    return
                time.sleep(1)

            # 🆕 修复：传入浮点数而不是字符串
            order_result = create_order_with_sl_tp(
                symbol=symbol,
                side='sell',
                amount=position_size,  # 直接传入浮点数
                order_type='limit',
                limit_price=bid_price,  # 直接传入浮点数
                stop_loss_price=stop_loss_price,  # 直接传入浮点数
                take_profit_price=take_profit_price  # 直接传入浮点数
            )

            if order_result and order_result.get('code') == '0':
                order_id = order_result['data'][0]['ordId']
                logger.log_info(f"✅ {get_base_currency(symbol)}: 限价开空仓提交-{position_size:.2f}张, 订单ID: {order_id}")  
                # 🆕 记录开仓操作到持仓历史
                add_to_position_history(symbol, {
                    'side': 'long' if signal_data['signal'] == 'BUY' else 'short',
                    'size': position_size,
                    'entry_price': current_price,
                    'action': 'open',
                    'order_id': order_id,
                    'signal_confidence': signal_data['confidence']
                })
            else:
                logger.log_error(f"sell_order_failed_{get_base_currency(symbol)}", f"❌ {get_base_currency(symbol)}: 限价开空仓提交失败")
                return
    except Exception as e:
        logger.log_error(f"trade_execution_{get_base_currency(symbol)}", f"交易执行异常: {str(e)}")
        logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 交易执行失败，但盈亏比分析仍然有效")

        import traceback
        traceback.print_exc()


def filter_signal(signal_data, price_data):
    """过滤信号 - 增强版，考虑盈亏比因素"""
    rsi = price_data['technical_data'].get('rsi', 50)
    
    # RSI过滤条件
    if signal_data['signal'] == 'BUY' and rsi > 70:
        return {
            **signal_data,
            'signal': 'HOLD',
            'reason': f'RSI超买 ({rsi:.2f})，保持观望',
            'confidence': 'LOW'
        }
    
    if signal_data['signal'] == 'SELL' and rsi < 30:
        return {
            **signal_data,
            'signal': 'HOLD', 
            'reason': f'RSI超卖 ({rsi:.2f})，保持观望',
            'confidence': 'LOW'
        }
    
    return signal_data


# 🆕 --- 核心修改：升级主循环以包含持仓管理 ---
def trading_bot(symbol: str):
    """
    主要交易逻辑循环 - 现在接受 symbol 参数
    """
    global CURRENT_SYMBOL
    CURRENT_SYMBOL = symbol  # 设置当前品种，以便日志记录器使用
    
    # 从全局字典中获取该品种的配置
    config = SYMBOL_CONFIGS[symbol]

    logger.log_info(f"\n=====================================")
    logger.log_info(f"🎯 运行交易品种: {get_base_currency(symbol)}")
    logger.log_info(f"配置摘要: {config.get_config_summary()}")  # 打印品种配置摘要
    logger.log_info(f"=====================================")

    try:
        # 添加执行时间记录
        start_time = time.time()

        # 1. 获取市场和价格数据 (使用 symbol)
        df, price_data = fetch_ohlcv(symbol)

        if df is None or price_data is None:
            logger.log_warning(f"❌ Could not fetch data for {get_base_currency(symbol)}.")
            return
            
        # 2. 获取当前持仓 (使用 symbol)
        current_position = get_current_position(symbol)

        # 记录数据状态
        data_status = f"数据: {len(df)}条K线 | 价格: {price_data['price']:.2f}"
        if current_position:
            data_status += f" | 持仓: {current_position['side']} {current_position['size']}张"
        logger.log_info(f"📊 {get_base_currency(symbol)}: {data_status}")

        # 3. [新] 持仓管理模块
        # 如果有持仓，优先处理持仓（止盈、移动止损、安全检查）
        if current_position:
            logger.log_info(f"ℹ️ {get_base_currency(symbol)}: 检测到持仓 {current_position['side']} {current_position['size']}张，进入持仓管理模式...")

            # 3a. 检查多级止盈
            # position_manager 是在文件全局范围创建的
            profit_signal = position_manager.check_profit_taking(symbol, current_position, price_data)
            
            if profit_signal:
                logger.log_info(f"💰 {get_base_currency(symbol)}: 触发多级止盈: {profit_signal['description']}")
                # 执行部分平仓
                execute_profit_taking(symbol, current_position, profit_signal, price_data)
                # 标记此级别已执行
                position_manager.mark_level_executed(symbol, current_position, profit_signal['level'])
                
                # 执行完止盈后，仓位发生变化，结束本轮循环
                # 等待下一个tick（60秒后）再用新仓位和新价格重新评估
                logger.log_info(f"✅ {get_base_currency(symbol)}: 部分止盈完成，结束本轮。")
                return

            # 3b. 检查移动止损 (如果没有触发多级止盈)
            trailing_stop_activated = setup_trailing_stop(symbol, current_position, price_data)
            if trailing_stop_activated:
                logger.log_info(f"🛡️ {get_base_currency(symbol)}: 移动止损已激活或更新。")
                # 移动止损已设置，本轮管理结束
                # 我们不 'return'，因为我们还想在下面检查止损单是否丢失
            
            # 3c. [鲁棒性检查] 检查并设置缺失的止损/止盈
            # 这可以防止因重启、API错误、或移动止损操作不当导致持仓"裸奔"
            # 它会智能地补上缺失的止损单或止盈单
            logger.log_info(f"🛡️ {get_base_currency(symbol)}: 运行安全检查，确保止损止盈单在交易所存在...")
            check_and_set_stop_loss(symbol, current_position, price_data)

            # 3d. [可选] 动态调整止盈 (如果需要更激进的策略)
            # adjust_take_profit_dynamically(symbol, current_position, price_data)

        # --- 持仓管理结束 ---

        # 4 使用DeepSeek高级用法进行市场分析
        analyzer = SYMBOL_ANALYZERS[symbol]
        symbol_signal_history = signal_history.get(symbol, [])
        
        signal_data = analyzer.analyze_market(
            symbol=symbol,
            price_data=price_data,
            signal_history=symbol_signal_history,
            current_position=current_position
        )

        if not signal_data:
            logger.log_warning(f"❌ Could not get signal for {get_base_currency(symbol)}.")
            return
        
        sentiment_data = analyzer.get_sentiment_indicators(symbol)
        if sentiment_data:
            logger.log_info(f"📊 {get_base_currency(symbol)}情绪数据: 正面{sentiment_data['positive_ratio']:.1%}, 负面{sentiment_data['negative_ratio']:.1%}")
        
        # 5. 过滤信号
        filtered_signal = filter_signal(signal_data, price_data)
        
        # 6. 添加到历史记录 (轻量级数据)
        light_price_data = price_data.copy()
        if 'full_data' in light_price_data:
            del light_price_data['full_data'] # 优化内存
            
        add_to_signal_history(symbol, filtered_signal)
        add_to_price_history(symbol, light_price_data)

        # 7. 记录信号
        logger.log_info(f"📊 {get_base_currency(symbol)} 交易信号: {filtered_signal['signal']} | 信心: {filtered_signal['confidence']}")
        logger.log_info(f"📝 原因: {filtered_signal['reason']}")

        # 8. 执行智能交易
        # (此函数负责开仓、反向平仓、或在持仓时加仓)
        execute_intelligent_trade(symbol, filtered_signal, price_data)

        # 记录执行时间
        execution_time = time.time() - start_time
        logger.log_info(f"⏱️ {get_base_currency(symbol)}: 本轮执行完成，耗时 {execution_time:.2f}秒")
        
        # 在交易循环的适当位置添加监控
        monitor_scaling_status(symbol)
        
    except Exception as e:
        logger.log_error(f"trading_bot_{get_base_currency(symbol)}", str(e))
# ✅ --- 修改结束 ---
        import traceback
        logger.log_error(f"trading_bot_traceback_{get_base_currency(symbol)}", traceback.format_exc())

def signal_handler(signum, frame):
    """信号处理函数"""
    logger.log_warning(f"🛑 接收到信号 {signum}，程序退出")
    cleanup_resources()
    sys.exit(0)


def health_check(symbol: str):
    """Check the health of the system for specific symbol."""
    global price_history  # 添加全局变量引用
    
    config = SYMBOL_CONFIGS[symbol]
    checks = []
    
    # Check API connection
    try:
        exchange.fetch_balance()
        checks.append(("API连接", "✅"))
    except Exception as e:
        checks.append(("API连接", "❌"))
        logger.log_error("health_check_api", str(e))
    
    # Check network
    try:
        import requests
        requests.get(config.deepseek_base_url, timeout=5)
        checks.append(("网络", "✅"))
    except Exception as e:
        checks.append(("网络", "❌"))
        logger.log_error("health_check_network", str(e))
    
    # Check data freshness - 使用该品种的价格历史
    symbol_price_history = price_history.get(symbol, [])
    if symbol_price_history:
        latest_data = symbol_price_history[-1]
        try:
            data_age = (datetime.now() - datetime.strptime(latest_data['timestamp'], '%Y-%m-%d %H:%M:%S')).total_seconds()
            status = "✅" if data_age < 300 else "⚠️"
            checks.append(("数据新鲜度", f"{status}({data_age:.0f}s)"))
        except Exception as e:
            checks.append(("数据新鲜度", f"⚠️(解析错误)"))
    else:
        checks.append(("数据新鲜度", "⚠️(无数据)"))
    
    # 🆕 合并为一条状态日志（替换原来的详细日志）
    details = " | ".join([f"{check}: {status}" for check, status in checks])
    
    # 判断整体状态
    overall_status = all("❌" not in status for _, status in checks)
    status_emoji = "✅" if overall_status else "❌"
    
    # 使用合并的健康检查日志
    logger.log_info(f"🔍 {get_base_currency(symbol)}系统健康检查: {status_emoji} | {details}")
    
    return overall_status

def close_position_fallback(symbol: str, position: dict, reason: str) -> bool:
    """备用平仓方法 - 使用不同的API方式"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        logger.log_warning(f"🔄 {get_base_currency(symbol)}: 使用备用平仓方法 - {reason}")
        
        position_size = position['size']
        position_side = position['side']
        margin_mode = position.get('margin_mode', config.margin_mode)
        
        # 🆕 修复：根据保证金模式设置posSide
        close_params = {
            'tdMode': margin_mode,
            'reduceOnly': True,
            'tag': create_order_tag()
        }
        
        if margin_mode == 'isolated':
            close_params['posSide'] = position_side
        else:
            close_params['posSide'] = 'net'
        
        # 🆕 方法1: 使用标准CCXT平仓
        try:
            if position_side == 'long':
                order = exchange.create_order(
                    config.symbol,
                    'market',
                    'sell',
                    position_size,
                    None,
                    close_params
                )
            else:
                order = exchange.create_order(
                    config.symbol,
                    'market', 
                    'buy',
                    position_size,
                    None,
                    close_params
                )
            
            if order and order.get('id'):
                logger.log_info(f"✅ {get_base_currency(symbol)}: 备用平仓方法成功，订单ID: {order['id']}")
                reset_scaling_status(symbol)
                return True
                
        except Exception as e1:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 备用平仓方法失败: {str(e1)}")
        
        return False
        
    except Exception as e:
        logger.log_error(f"close_position_fallback_{get_base_currency(symbol)}", f"备用平仓方法异常: {str(e)}")
        return False

def close_position_with_reason(symbol: str, position: dict, reason: str) -> bool:
    """根据原因平仓 - 增强版本"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        # 🆕 重新获取最新持仓信息，避免数据过时
        current_position = get_current_position(symbol)
        if not current_position:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 实际无持仓，无需平仓")
            return True
            
        # 🆕 验证持仓方向是否匹配
        if current_position['side'] != position['side']:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 持仓方向不匹配，重新获取持仓信息")
            position = current_position
        
        # 🆕 验证持仓数量
        position_size = current_position['size']
        if position_size <= 0:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 持仓数量为0，无需平仓")
            return True
            
        logger.log_warning(f"🔄 {get_base_currency(symbol)}: 执行平仓 - {reason} - {position_size}张")

        # 🆕 记录平仓前的持仓信息到历史
        add_to_position_history(symbol, {
            'side': position['side'],
            'size': position_size,
            'entry_price': position['entry_price'],
            'action': 'close',
            'close_reason': reason
        })

        # 🆕 取消该品种的所有策略委托订单
        logger.log_info(f"🔄 {get_base_currency(symbol)}: 平仓前取消所有策略委托订单")
        cancel_existing_algo_orders(symbol)
        time.sleep(1)  # 等待取消操作完成

        if position['side'] == 'long':
            # 平多仓
            close_params = {
                'reduceOnly': True,
                'tag': create_order_tag()
            }
            
            # 记录订单参数
            log_order_params("平多仓", close_params, "close_position_with_reason")
            log_perpetual_order_details(symbol, 'sell', position_size, 'market', reduce_only=True)
            
            if not config.test_mode:
                try:
                    # 🆕 使用更安全的订单创建方式
                    order = exchange.create_order(
                        config.symbol,
                        'market',
                        'sell',
                        position_size,
                        None,
                        close_params
                    )
                    
                    # 🆕 验证订单是否创建成功
                    if order and order.get('id'):
                        reset_scaling_status(symbol)
                        logger.log_info(f"✅ {get_base_currency(symbol)}: 平多仓订单提交成功，ID: {order['id']}")
                        
                        # 等待并验证平仓结果
                        return verify_position_closed(symbol, position_size, 'long')
                    else:
                        logger.log_error(f"❌ {get_base_currency(symbol)}: 平多仓订单提交失败，响应: {order}")
                        return False
                        
                except Exception as order_error:
                    logger.log_error(f"close_long_position_{get_base_currency(symbol)}", 
                                   f"平多仓异常: {str(order_error)}")
                    # 🆕 尝试备用方法
                    return close_position_fallback(symbol, position, reason)
            else:
                logger.log_info("测试模式 - 模拟平多仓成功")
                return True
                
        else:  # short
            # 平空仓
            close_params = {
                'reduceOnly': True,
                'tag': create_order_tag()
            }
            
            log_order_params("平空仓", close_params, "close_position_with_reason")
            log_perpetual_order_details(symbol, 'buy', position_size, 'market', reduce_only=True)
            
            if not config.test_mode:
                try:
                    order = exchange.create_order(
                        config.symbol,
                        'market',
                        'buy',
                        position_size,
                        None,
                        close_params
                    )
                    
                    if order and order.get('id'):
                        reset_scaling_status(symbol)
                        logger.log_info(f"✅ {get_base_currency(symbol)}: 平空仓订单提交成功，ID: {order['id']}")
                        return verify_position_closed(symbol, position_size, 'short')
                    else:
                        logger.log_error(f"❌ {get_base_currency(symbol)}: 平空仓订单提交失败，响应: {order}")
                        return False
                        
                except Exception as order_error:
                    logger.log_error(f"close_short_position_{get_base_currency(symbol)}", 
                                   f"平空仓异常: {str(order_error)}")
                    return close_position_fallback(symbol, position, reason)
            else:
                logger.log_info("测试模式 - 模拟平空仓成功")
                return True
                
    except Exception as e:
        logger.log_error(f"close_position_{get_base_currency(symbol)}", f"平仓失败: {str(e)}")
        # 🆕 尝试备用方法
        return close_position_fallback(symbol, position, reason)

def check_existing_positions_on_startup():
    """启动时检查所有交易品种的现有持仓 - 修复版本"""
    logger.log_info("🔍 启动时持仓检查开始...")
    
    for symbol, config in SYMBOL_CONFIGS.items():
        try:
            logger.log_info(f"📊 检查 {get_base_currency(symbol)} 的持仓状态...")

            # 获取当前持仓
            current_position = get_current_position(symbol)
            
            if current_position is None:
                logger.log_info(f"✅ {get_base_currency(symbol)}: 无持仓")
                continue
            
            # 🆕 验证持仓真实性
            if not verify_position_exists(symbol, current_position):
                logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 持仓数据可能不准确，跳过处理")
                continue
                
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 发现现有持仓 - {current_position['side']} {current_position['size']}张")
            
            # 获取市场数据进行分析
            df, price_data = fetch_ohlcv(symbol)
            
            if df is None or price_data is None:
                logger.log_warning(f"❌ {get_base_currency(symbol)}: 无法获取市场数据，跳过分析")
                continue
            
            # 🆕 首先优化现有订单（取消不合理的订单）
            optimize_existing_orders(symbol, current_position, price_data)
            
            # 分析是否应该继续持有
            should_hold = analyze_should_hold_position(symbol, current_position, price_data)
            
            if should_hold:
                # 检查并设置止损订单
                check_and_set_stop_loss(symbol, current_position, price_data)
            else:
                # 平仓
                close_position_with_reason(symbol, current_position, "启动分析建议平仓")
                
        except Exception as e:
            logger.log_error(f"startup_check_{get_base_currency(symbol)}", f"启动检查失败: {str(e)}")
    
    logger.log_info("✅ 启动时持仓检查完成")

def analyze_should_hold_position(symbol: str, position: dict, price_data: dict) -> bool:
    """分析是否应该继续持有现有持仓"""
    try:
        config = SYMBOL_CONFIGS[symbol]
        
        # 🆕 使用高级用法
        analyzer = SYMBOL_ANALYZERS[symbol]
        symbol_signal_history = signal_history.get(symbol, [])
        
        signal_data = analyzer.analyze_market(
            symbol=symbol,
            price_data=price_data,
            signal_history=symbol_signal_history,
            current_position=position
        )
        
        # 🆕 修复：使用明确的 None 检查而不是真值判断
        if signal_data is None:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 无法获取分析信号，保守处理：继续持有")
            return True
        
        position_side = position['side']  # 'long' or 'short'
        signal_side = signal_data['signal']  # 'BUY', 'SELL', 'HOLD'
        
        logger.log_info(f"📊 {get_base_currency(symbol)} 持仓分析: 持仓{position_side}, 信号{signal_side}, 信心{signal_data['confidence']}")
        
        # 判断逻辑
        if signal_side == 'HOLD':
            logger.log_info(f"✅ {get_base_currency(symbol)}: 信号建议持有，继续持仓")
            return True
            
        elif (position_side == 'long' and signal_side == 'BUY') or \
             (position_side == 'short' and signal_side == 'SELL'):
            logger.log_info(f"✅ {get_base_currency(symbol)}: 信号与持仓方向一致，继续持仓")
            return True
            
        elif (position_side == 'long' and signal_side == 'SELL') or \
             (position_side == 'short' and signal_side == 'BUY'):
            # 趋势反转，需要进一步分析强度
            reversal_strength = analyze_trend_reversal_strength(position_side, signal_side, price_data, signal_data)
            
            if reversal_strength in ['STRONG', 'MEDIUM']:
                logger.log_warning(f"🔄 {get_base_currency(symbol)}: 检测到{reversal_strength}强度趋势反转，建议平仓")
                return False
            else:
                logger.log_info(f"✅ {get_base_currency(symbol)}: 弱强度反转信号，继续持有观察")
                return True
        else:
            logger.log_warning(f"⚠️ {get_base_currency(symbol)}: 未知信号组合，保守处理：继续持有")
            return True
            
    except Exception as e:
        logger.log_error(f"hold_analysis_{get_base_currency(symbol)}", f"持仓分析失败: {str(e)}")
        return True  # 出错时保守处理，继续持有

def analyze_trend_reversal_strength(position_side: str, signal_side: str, price_data: dict, signal_data: dict) -> str:
    """分析趋势反转强度"""
    try:
        tech = price_data['technical_data']
        confirmation_count = 0
        
        # 1. RSI 确认
        rsi = tech.get('rsi', 50)
        if (position_side == 'long' and rsi > 70) or (position_side == 'short' and rsi < 30):
            confirmation_count += 1
            
        # 2. 移动平均线确认
        price = price_data['price']
        sma_20 = tech.get('sma_20', price)
        if (position_side == 'long' and price < sma_20) or (position_side == 'short' and price > sma_20):
            confirmation_count += 1
            
        # 3. MACD 确认
        macd = tech.get('macd', 0)
        macd_signal = tech.get('macd_signal', 0)
        if (position_side == 'long' and macd < macd_signal) or (position_side == 'short' and macd > macd_signal):
            confirmation_count += 1
            
        # 4. 置信度确认
        if signal_data.get('confidence') == 'HIGH':
            confirmation_count += 1
            
        # 判断强度
        if confirmation_count >= 3:
            return 'STRONG'
        elif confirmation_count >= 2:
            return 'MEDIUM'
        else:
            return 'WEAK'
            
    except Exception as e:
        logger.log_error("reversal_strength_analysis", str(e))
        return 'WEAK'


def log_performance_metrics(symbol: str):
    """Log performance metrics for specific symbol."""
    global signal_history
    
    if symbol not in signal_history or not signal_history[symbol]:
        return

    signals = [s['signal'] for s in signal_history[symbol]]

    buy_count = signals.count('BUY')
    sell_count = signals.count('SELL')
    hold_count = signals.count('HOLD')
    total = len(signals)
    
    # Use logger.log_performance instead of print
    performance_metrics = {
        'symbol': get_base_currency(symbol),  # <-- 使用提取出的基础货币
        'buy_signals': f"{buy_count}/{total}",
        'sell_signals': f"{sell_count}/{total}", 
        'hold_signals': f"{hold_count}/{total}",
        'total_signals': total
    }
    logger.log_performance(performance_metrics)


def analyze_position_history(symbol: str) -> dict:
    """分析持仓历史，提供统计数据"""
    try:
        history = get_position_history(symbol)
        if not history:
            return {'total_trades': 0, 'message': '无历史数据'}
        
        # 统计信息
        total_trades = len(history)
        open_trades = [h for h in history if h.get('action') in ['open', 'add']]
        close_trades = [h for h in history if h.get('action') in ['close', 'partial_close']]
        
        # 计算盈利情况
        profitable_trades = 0
        total_profit = 0
        
        for trade in close_trades:
            if trade.get('realized_pnl', 0) > 0:
                profitable_trades += 1
            total_profit += trade.get('realized_pnl', 0)
        
        win_rate = profitable_trades / len(close_trades) if close_trades else 0
        
        analysis = {
            'total_trades': total_trades,
            'open_trades': len(open_trades),
            'closed_trades': len(close_trades),
            'win_rate': f"{win_rate:.1%}",
            'total_profit': total_profit,
            'avg_profit_per_trade': total_profit / len(close_trades) if close_trades else 0,
            'recent_activity': history[-5:] if len(history) >= 5 else history
        }
        
        logger.log_info(f"📈 {get_base_currency(symbol)} 持仓历史分析: "
                       f"总交易{total_trades}次, 胜率{analysis['win_rate']}, "
                       f"总盈利{total_profit:.2f} USDT")
        
        return analysis
        
    except Exception as e:
        logger.log_error(f"analyze_position_history_{get_base_currency(symbol)}", f"持仓历史分析失败: {str(e)}")
        return {'error': str(e)}

# 添加配置管理功能
def update_strategy_config(new_config: Dict[str, Any]) -> bool:
    """更新策略配置"""
    global sl_tp_strategy
    if sl_tp_strategy:
        return sl_tp_strategy.update_strategy_config(new_config)
    return False

def get_strategy_performance(symbol: str) -> Dict[str, Any]:
    """获取策略性能分析"""
    global strategy_optimizer
    if strategy_optimizer:
        return strategy_optimizer.analyze_performance(symbol)
    return {}

def optimize_strategy_parameters(symbol: str) -> Dict[str, Any]:
    """优化策略参数"""
    global strategy_optimizer
    if strategy_optimizer:
        # 这里需要实际的交易数据
        trade_data = []  # 需要从数据库或文件中加载
        parameter_ranges = {
            'atr_multiplier': [1.0, 1.2, 1.5, 1.8, 2.0],
            'min_risk_reward': [1.0, 1.2, 1.5, 1.8, 2.0],
            'max_stop_loss_ratio': [0.3, 0.35, 0.4, 0.45, 0.5]
        }
        return strategy_optimizer.backtest_parameters(trade_data, parameter_ranges)
    return {}

def main():
    """
    优化后的主程序 - 基于K线周期的动态调度
    """
    global SYMBOL_CONFIGS, symbols_to_trade

    # 🆕 在程序开始时加载仓位状态
    global position
    position = load_position_history()
    if position is None:
        logger.log_info("ℹ️ 从空仓位状态开始")
    else:
        logger.log_info(f"✅ 成功加载仓位状态")

    # 添加信号处理
    import signal
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if not symbols_to_trade_raw:
        logger.log_error("配置错误", f"❌ 账号 '{CURRENT_ACCOUNT}' 在 ACCOUNT_SYMBOL_MAPPING 中没有对应的交易品种配置。")
        return

    logger.log_info(f"⚙️ 账号 '{CURRENT_ACCOUNT}' 准备加载 {len(symbols_to_trade_raw)} 个品种的配置...")

    # 1. 加载品种配置 - 第一轮：从原始列表加载
    for symbol in symbols_to_trade_raw:
        config_dict = MULTI_SYMBOL_CONFIGS.get(symbol)
        if config_dict:
            try:
                symbol_config = TradingConfig(symbol, **config_dict)
                is_valid, errors, warnings = symbol_config.validate_config()
                if not is_valid:
                    logger.log_error(f"❌ {get_base_currency(symbol)} 配置验证失败: {errors}")
                    continue
                if warnings:
                    for w in warnings:
                        logger.log_warning(f"⚠️ {get_base_currency(symbol)} 配置警告: {w}")
                
                SYMBOL_CONFIGS[symbol] = symbol_config
                symbols_to_trade.append(symbol)
                logger.log_info(f"✅ {get_base_currency(symbol)} 配置加载成功")
                
            except Exception as e:
                logger.log_error(f"❌ {get_base_currency(symbol)} 配置初始化失败: {str(e)}")
        else:
            logger.log_error(f"❌ 品种 {symbol} 在 MULTI_SYMBOL_CONFIGS 中未找到配置，跳过。")

    logger.log_info(f"🚀 账号 '{CURRENT_ACCOUNT}' 初步加载 {len(symbols_to_trade)} 个品种")

    # 🆕 2. 第二轮配置验证和初始化 - 这是你提到的关键代码
    valid_symbols = []
    for symbol in symbols_to_trade:
        try:
            if symbol not in MULTI_SYMBOL_CONFIGS:
                logger.log_warning(f"⚠️ 跳过未配置的品种: {get_base_currency(symbol)}")
                continue
                
            # 这里确保配置对象正确创建
            if symbol not in SYMBOL_CONFIGS:
                config_dict = MULTI_SYMBOL_CONFIGS[symbol]
                config = TradingConfig(symbol=symbol, config_data=config_dict)
            else:
                config = SYMBOL_CONFIGS[symbol]
            
            # 验证配置
            is_valid, errors, warnings = config.validate_config(symbol)
            if not is_valid:
                logger.log_error(f"config_validation_{get_base_currency(symbol)}", f"配置验证失败: {errors}")
                continue
                
            # 确保配置正确存储
            SYMBOL_CONFIGS[symbol] = config
            valid_symbols.append(symbol)
            
            logger.log_info(f"✅ 加载配置: {get_base_currency(symbol)} | 杠杆 {config.leverage}x | 基础金额 {config.position_management['base_usdt_amount']} USDT")
            
        except Exception as e:
            logger.log_error(f"config_loading_{get_base_currency(symbol)}", str(e))
    
    # 更新有效的交易品种列表
    symbols_to_trade = valid_symbols

    # 🆕 类型安全检查
    if not SYMBOL_CONFIGS or not isinstance(SYMBOL_CONFIGS, dict):
        logger.log_error("program_exit", "交易品种配置加载失败或类型错误")
        return
        
    # 🆕 确保 first_config 是 TradingConfig 对象
    first_config = None
    for config in SYMBOL_CONFIGS.values():
        if hasattr(config, 'max_consecutive_errors'):
            first_config = config
            break
    
    if first_config is None:
        logger.log_warning("⚠️ 无法获取有效配置，使用默认值")
        # 创建一个默认配置对象或使用硬编码值
        class DefaultConfig:
            max_consecutive_errors = 5
            config_check_interval = 300
            perf_log_interval = 3600
        
        first_config = DefaultConfig()

    logger.log_info(f"🎯 最终交易品种列表: {[get_base_currency(s) for s in symbols_to_trade]}")

    # 2. 初始化交易所设置
    for symbol in list(SYMBOL_CONFIGS.keys()):
        if not setup_exchange(symbol):
            logger.log_error("exchange_setup", f"交易所设置失败: {get_base_currency(symbol)}")
            del SYMBOL_CONFIGS[symbol]

    symbols_to_trade = list(SYMBOL_CONFIGS.keys())
    if not symbols_to_trade:
        logger.log_error("program_exit", "所有交易品种初始化失败")
        return
        
    # 3. 打印版本信息
    version_config = SYMBOL_CONFIGS[symbols_to_trade[0]]
    print_version_banner(version_config)

    # 初始化 DeepSeek 分析器
    global SYMBOL_ANALYZERS
    for symbol in symbols_to_trade:
        config = SYMBOL_CONFIGS[symbol]
        SYMBOL_ANALYZERS[symbol] = get_deepseek_analyzer(config)
        logger.log_info(f"✅ {get_base_currency(symbol)}: DeepSeek分析器初始化完成")

    # 🆕 初始化止盈止损策略
    global sl_tp_strategy
    initialize_sl_tp_strategy(SYMBOL_CONFIGS)
    sl_tp_strategy = get_sl_tp_strategy()

    # 🆕 初始化策略优化器
    global strategy_optimizer
    strategy_optimizer = StrategyOptimizer()
    
    # 🆕 启动时持仓检查
    check_existing_positions_on_startup()

    # 🆕 4. 初始化动态调度系统
    symbol_schedules = {}
    for symbol in symbols_to_trade:
        config = SYMBOL_CONFIGS[symbol]
        next_execution = calculate_next_execution_time(symbol)
        
        symbol_schedules[symbol] = {
            'next_execution': next_execution,
            'timeframe': config.timeframe,
            'timeframe_seconds': get_timeframe_seconds(config.timeframe),
            'last_execution': 0,
            'execution_count': 0
        }
        
        next_time_str = datetime.fromtimestamp(next_execution).strftime('%H:%M:%S')
        logger.log_info(f"⏰ {get_base_currency(symbol)}: 首次执行 {next_time_str} ({config.timeframe}周期)")

    logger.log_info(f"🚀 动态调度系统启动，监控 {len(symbols_to_trade)} 个品种")

    # 5. 主循环控制变量
    consecutive_errors = 0
    last_health_check = 0
    health_check_interval = 3600  # 1小时
    last_config_check = 0
    config_check_interval = 300   # 5分钟
    last_perf_log = 0
    perf_log_interval = 3600      # 1小时
    last_position_analysis = 0
    position_analysis_interval = 3600  # 1小时

    try:
        while True:
            current_time = time.time()
            executed_this_cycle = False

            # 🆕 动态调度：检查每个品种的执行时间
            for symbol in symbols_to_trade:
                schedule = symbol_schedules[symbol]
                
                if current_time >= schedule['next_execution']:
                    try:
                        # 执行交易逻辑
                        trading_bot(symbol)
                        schedule['execution_count'] += 1
                        schedule['last_execution'] = current_time
                        executed_this_cycle = True
                        
                        # 计算下一个执行时间
                        schedule['next_execution'] = calculate_next_execution_time(symbol)
                        
                        next_time_str = datetime.fromtimestamp(schedule['next_execution']).strftime('%H:%M:%S')
                        time_until_str = format_time_until_next_execution(schedule['next_execution'])
                        
                        logger.log_info(f"⏰ {get_base_currency(symbol)}: 下次执行 {next_time_str} ({time_until_str})")
                        
                    except Exception as e:
                        logger.log_error(f"scheduled_execution_{get_base_currency(symbol)}", f"调度执行失败: {str(e)}")
                        # 出错时仍然设置下一个执行时间，避免阻塞
                        schedule['next_execution'] = current_time + 60  # 1分钟后重试

            # 🆕 定期健康检查
            if current_time - last_health_check >= health_check_interval:
                logger.log_info("🔍 执行定期健康检查...")
                health_ok = True
                for symbol in symbols_to_trade:
                    if not health_check(symbol):
                        health_ok = False
                        break
                
                if not health_ok:
                    consecutive_errors += 1
                    max_errors = getattr(version_config, 'max_consecutive_errors', 5)
                    if consecutive_errors >= max_errors:
                        logger.log_error("🚨 连续错误过多，程序退出")
                        break
                else:
                    consecutive_errors = 0
                last_health_check = current_time

            # 🆕 定期配置检查
            if current_time - last_config_check >= config_check_interval:
                last_config_check = current_time
                # 这里可以添加配置重载逻辑

            # 🆕 定期性能日志
            if current_time - last_perf_log >= perf_log_interval:
                for symbol in symbols_to_trade:
                    log_performance_metrics(symbol)
                last_perf_log = current_time

            # 🆕 定期持仓分析
            if current_time - last_position_analysis >= position_analysis_interval:
                for symbol in symbols_to_trade:
                    analyze_position_history(symbol)
                last_position_analysis = current_time

            # 🆕 保存仓位状态
            save_position_history()

            # 🆕 智能睡眠计算
            if executed_this_cycle:
                # 如果本轮有执行，短暂睡眠后继续检查
                sleep_time = 1
            else:
                # 计算距离最近的下次执行时间
                next_executions = [s['next_execution'] for s in symbol_schedules.values()]
                if next_executions:
                    next_execution = min(next_executions)
                    sleep_time = max(1, min(30, next_execution - current_time))
                else:
                    sleep_time = 30
                
                # 记录调度状态
                if sleep_time > 5:  # 只在较长睡眠时记录
                    active_schedules = []
                    for symbol, schedule in symbol_schedules.items():
                        time_until = schedule['next_execution'] - current_time
                        if time_until <= 300:  # 只显示5分钟内的
                            active_schedules.append(
                                f"{get_base_currency(symbol)}:{format_time_until_next_execution(schedule['next_execution'])}"
                            )
                    
                    if active_schedules:
                        logger.log_debug(f"⏰ 调度状态: {', '.join(active_schedules)}")

            time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.log_warning("\n🛑 用户中断程序")
    except Exception as e:
        logger.log_error("main_loop", f"主循环异常: {str(e)}")
    finally:
        cleanup_resources()
        
        # 🆕 输出调度统计
        logger.log_info("📊 动态调度统计:")
        for symbol, schedule in symbol_schedules.items():
            execution_count = schedule.get('execution_count', 0)
            timeframe = schedule.get('timeframe', 'unknown')
            logger.log_info(f"  {get_base_currency(symbol)}: 执行{execution_count}次 ({timeframe}周期)")
        
        logger.log_info("👋 程序退出")



if __name__ == "__main__":
    main()
