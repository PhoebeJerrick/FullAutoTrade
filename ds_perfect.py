import os
import time
import base64
import hmac
import hashlib
import sys
from functools import wraps
from typing import Optional, Dict, List, Any, Union
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

# Trading parameter configuration - combining advantages of both versions
from trade_config import TradingConfig, MULTI_SYMBOL_CONFIGS # 新代码: 导入类和多品种配置
# Global logger
from trade_logger import logger

# --- NEW: Global Variables for Multi-Symbol ---
# 全局变量，用于保存所有交易品种的配置实例
SYMBOL_CONFIGS: Dict[str, TradingConfig] = {}
# 当前活跃的交易品种（在 trading_bot 中设置，用于日志和调试）
CURRENT_SYMBOL: Optional[str] = None


# Global variables to store historical data
price_history = []
signal_history = []
position = None

# Use relative path
env_path = '../ExApiConfig/ExApiConfig.env'  # .env file in config folder of parent directory
logger.log_info(f"📁Add config file: {env_path}")
load_dotenv(dotenv_path=env_path)

# Initialize DeepSeek client with error handling
deepseek_client = None

def get_deepseek_client(symbol: str):
    global deepseek_client
    config = SYMBOL_CONFIGS[symbol]
    if deepseek_client is None:
        try:
            api_key = os.getenv('DEEPSEEK_API_KEY')
            if not api_key:
                raise ValueError("DEEPSEEK_API_KEY environment variable is not set")
            
            deepseek_client = OpenAI(
                api_key=api_key,
                base_url=config.deepseek_base_url
            )
            logger.log_info("DeepSeek client initialized successfully")
        except Exception as e:
            logger.log_error("deepseek_client_init", str(e))
            raise
    return deepseek_client


# 添加账号参数支持
if len(sys.argv) > 1:
    account = sys.argv[1]
    logger.log_info(f"🎯 使用交易账号: {account}")
else:
    account = "default"
    logger.log_info("🎯 使用默认交易账号")

# 在全局变量中记录当前账号
CURRENT_ACCOUNT = account

# 根据账号选择对应的环境变量
def get_account_config(account_name):
    """根据账号名称获取对应的配置"""
    if account_name == "okxMain":
        return {
            'api_key': os.getenv('OKX_API_KEY_1') or os.getenv('OKX_API_KEY'),
            'secret': os.getenv('OKX_SECRET_1') or os.getenv('OKX_SECRET'),
            'password': os.getenv('OKX_PASSWORD_1') or os.getenv('OKX_PASSWORD')
        }
    elif account_name == "okxSub1":
        return {
            'api_key': os.getenv('OKX_API_KEY_2'),
            'secret': os.getenv('OKX_SECRET_2'),
            'password': os.getenv('OKX_PASSWORD_2')
        }
    else:  # default
        return {
            'api_key': os.getenv('OKX_API_KEY'),
            'secret': os.getenv('OKX_SECRET'),
            'password': os.getenv('OKX_PASSWORD')
        }

# 获取当前账号配置
account_config = get_account_config(account)
print(f"🔑 账号配置加载: API_KEY={account_config['api_key'][:10]}...")

# 修改订单标签函数，包含账号信息
# def create_order_tag():
#     """创建符合OKX要求的订单标签"""
#     # 使用固定格式，避免特殊字符
#     base_tag = 'DS60bb4a8d3416BCDE'  # 添加前缀确保格式正确
    
#     # 简单处理账号名称
#     account_suffix = CURRENT_ACCOUNT.replace('account', 'A')
    
#     tag = f"{base_tag}{account_suffix}"
    
#     # 确保不超过32字符
#     tag = tag[:32]
    
#     logger.log_info(f"📝 生成的订单标签: {tag}")
#     return tag

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

def calculate_dynamic_base_amount(symbol: str, usdt_balance: float) -> float:
    """基于账户规模计算动态基础金额"""
    config = SYMBOL_CONFIGS[symbol]
    posMngmt = config.position_management
    
    # 方法1：固定比例
    base_ratio = 0.02  # 2% of total balance
    dynamic_base = usdt_balance * base_ratio
    
    # 方法2：分级比例（资金越大，单次投资比例越小）
    if usdt_balance > 10000:
        base_ratio = 0.015
    elif usdt_balance > 5000:
        base_ratio = 0.02
    else:
        base_ratio = 0.03
        
    dynamic_base = usdt_balance * base_ratio
    
    # 设置上下限（保持不变）
    min_base = 50  # 最小50U
    max_base = 500 # 最大500U
    
    return max(min_base, min(dynamic_base, max_base))


def calculate_volatility_adjustment(symbol: str, df: pd.DataFrame) -> float:
    """基于波动率调整仓位"""
    # 计算ATR波动率
    atr = calculate_atr(df)
    current_price = df['close'].iloc[-1]
    atr_percentage = (atr / current_price) * 100
    
    # 波动率越大，仓位越小
    if atr_percentage > 3.0:  # 高波动
        return 0.5
    elif atr_percentage > 2.0:  # 中波动
        return 0.8
    else:  # 低波动
        return 1.0

def calculate_enhanced_position(symbol: str, signal_data: dict, price_data: dict, current_position: Optional[dict]) -> float:
    """增强版仓位计算"""
    config = SYMBOL_CONFIGS[symbol]
    posMngmt = config.position_management
    
    try:
        # 获取账户余额
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']
        
        # 1. 动态基础金额（基于账户规模）
        dynamic_base_usdt = calculate_dynamic_base_amount(symbol, usdt_balance)
        
        # 2. 头仓最小比例限制（如果是首次开仓）
        is_first_position = not current_position or current_position['size'] == 0
        if is_first_position:
            # 计算头仓最小金额（总余额 * 最小比例）
            first_position_min = usdt_balance * posMngmt['first_position_min_ratio']
            # 取较大值作为基础金额
            dynamic_base_usdt = max(dynamic_base_usdt, first_position_min)
        else:
            # 非首次开仓（加仓），应用加仓比例限制
            first_position_size = current_position['size']  # 假设current_position包含头仓大小
            
            # 计算基于头仓的最大和最小加仓金额
            max_addition = first_position_size * posMngmt['add_position_max_ratio']
            min_addition = first_position_size * posMngmt['add_position_min_ratio']
            
            # 计算建议加仓金额
            suggested_addition = (dynamic_base_usdt * confidence_multiplier * 
                                trend_multiplier * rsi_multiplier * 
                                volatility_multiplier * leverage_multiplier)
            
            # 应用加仓限制
            dynamic_base_usdt = max(min_addition, min(suggested_addition, max_addition))
        
        # 3. 信心倍数
        confidence_multiplier = {
            'HIGH': posMngmt['high_confidence_multiplier'],
            'MEDIUM': posMngmt['medium_confidence_multiplier'],
            'LOW': posMngmt['low_confidence_multiplier']
        }.get(signal_data['confidence'], 1.0)
        
        # 4. 趋势倍数
        trend = price_data['trend_analysis'].get('overall', 'Consolidation')
        if trend in ['Strong uptrend', 'Strong downtrend']:
            trend_multiplier = posMngmt['trend_strength_multiplier']
        else:
            trend_multiplier = 1.0
        
        # 5. RSI调整
        rsi = price_data['technical_data'].get('rsi', 50)
        if rsi > 75 or rsi < 25:
            rsi_multiplier = 0.7
        else:
            rsi_multiplier = 1.0
        
        # 6. 波动率调整
        volatility_multiplier = calculate_volatility_adjustment(symbol, price_data['full_data'])
        
        # 7. 杠杆调整（如果使用高杠杆，减少仓位）
        leverage_multiplier = 1.0 / min(config.leverage, 10)  # 杠杆越高，实际仓位越小
        
        # 计算建议投资金额
        suggested_usdt = (dynamic_base_usdt * confidence_multiplier * 
                         trend_multiplier * rsi_multiplier * 
                         volatility_multiplier * leverage_multiplier)
        
        # 风险上限
        max_usdt = usdt_balance * posMngmt['max_position_ratio']
        final_usdt = min(suggested_usdt, max_usdt)
        
        # 转换为合约张数
        contract_size = final_usdt / (price_data['price'] * config.contract_size)
        contract_size = round(contract_size, 2)  # 精度处理
        
        # 确保最小交易量
        min_contracts = getattr(config, 'min_amount', 0.01)
        if contract_size < min_contracts:
            contract_size = min_contracts
        
        # 详细日志
        calculation_details = f"""
        🎯 增强版仓位计算详情:
        账户余额: {usdt_balance:.2f} USDT
        {'头仓最小金额: ' + str(first_position_min) + ' USDT' if is_first_position else ''}
        动态基础: {dynamic_base_usdt:.2f} USDT
        信心倍数: {confidence_multiplier} | 趋势倍数: {trend_multiplier}
        RSI倍数: {rsi_multiplier} | 波动率倍数: {volatility_multiplier}
        杠杆倍数: {leverage_multiplier}
        建议投资: {suggested_usdt:.2f} USDT → 最终投资: {final_usdt:.2f} USDT
        合约数量: {contract_size:.2f}张
        """
        logger.log_info(calculation_details)
        
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
        
        log_msg = f"🎯 {symbol} 永续合约订单: {side} {amount}张 | {order_type} | {action_str}"
        if stop_loss_price:
            stop_loss_ratio = abs(stop_loss_price - get_current_price(symbol)) / get_current_price(symbol) * 100  # 添加 symbol 参数
            log_msg += f" | 止损价:{stop_loss_price:.2f}({stop_loss_ratio:.2f}%)"
            
        logger.log_info(log_msg)
            
    except Exception as e:
        logger.log_error("log_perpetual_order_details", f"记录订单{symbol} 详情失败: {str(e)}")

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
            logger.log_error("exchange_setup", f"Symbol {symbol} not supported by exchange.")
            return False
            
        market_info = markets[symbol]
        
        # 动态更新配置实例的合约信息
        config.contract_size = float(market_info.get('contractSize', 1.0))
        config.min_amount = market_info['limits']['amount']['min']
        
        logger.log_info(f"✅ Contract {symbol}: 1 contract = {config.contract_size} base asset")
        logger.log_info(f"📏 Min trade {symbol}: {config.min_amount} contracts")
        
        # 2. 设置杠杆（使用更安全的方式）
        leverage = getattr(config, 'leverage', 50)
        logger.log_info(f"⚙️ Setting leverage for {symbol} to {leverage}x...")
        try:
            # 使用OKX特定的API设置杠杆
            exchange.private_post_account_set_leverage({
                'instId': get_correct_inst_id(symbol),
                'lever': str(leverage),
                'mgnMode': config.margin_mode
            })
            logger.log_warning(f"✅ Leverage {leverage}x set for {symbol}")
        except Exception as e:
            logger.log_warning(f"⚠️ Leverage setting failed for {symbol}: {e}")
            
        # 3. 设置保证金模式（使用OKX特定的API）
        logger.log_info(f"⚙️ Setting margin mode for {symbol} to {config.margin_mode}...")
        try:
            # 使用OKX特定的API设置仓位模式
            exchange.private_post_account_set_position_mode({
                'posMode': 'long_short_mode' if config.margin_mode == 'cross' else 'net_mode'
            })
            logger.log_warning(f"✅ Margin mode {config.margin_mode} set for {symbol}")
        except Exception as e:
            # 如果设置失败，可能是已经设置过了，记录警告但不中断流程
            logger.log_warning(f"⚠️ Margin mode setting failed for {symbol}: {e}")
            logger.log_warning(f"ℹ️ This might be because the mode is already set, continuing...")
        
        return True

    except Exception as e:
        logger.log_error(f"exchange_setup_{symbol}", str(e))
        return False


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

        # Correct contract quantity calculation!
        # Formula: Contract quantity = (Investment USDT) / (Current price * Contract multiplier)
        contract_size = (final_usdt) / (price_data['price'] * config.contract_size)

        # Precision handling: OKX BTC contract minimum trading unit is 0.01 contracts
        contract_size = round(contract_size, 2)  # Keep 2 decimal places

        # Ensure minimum trading volume
        min_contracts = getattr(config, 'min_amount', 0.01)
        if contract_size < min_contracts:
            contract_size = min_contracts

        calculation_summary = f"""
            📊 仓位计算详情:
            基础投资: {base_usdt} USDT | 信心倍数: {confidence_multiplier}
            趋势倍数: {trend_multiplier} | RSI倍数: {rsi_multiplier}
            建议投资: {suggested_usdt:.2f} USDT → 最终投资: {final_usdt:.2f} USDT
            合约数量: {contract_size:.4f}张 → 四舍五入: {round(contract_size, 2):.2f}张
            """
        logger.log_info(calculation_summary)

        return contract_size

    except Exception as e:
        logger.log_error("Position calculation failed, using base position", str(e))
        # Emergency backup calculation
        base_usdt = posMngmt['base_usdt_amount']
        contract_size = (base_usdt * config.leverage) / (
                    price_data['price'] * getattr(config, 'contract_size', 0.01))
        return round(max(contract_size, getattr(config, 'min_amount', 0.01)), 2)


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


def get_sentiment_indicators(symbol: str):
    """Get sentiment indicators - simplified version"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        API_URL = config.sentiment_api_url
        API_KEY = config.sentiment_api_key

        # 从 symbol 中提取币种名称
        # 格式可能是 "BTC/USDT:USDT" 或 "ETH/USDT:USDT" 等
        base_currency = symbol.split('/')[0].upper()
        
        # Get recent 4-hour data
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=4)

        request_body = {
            "apiKey": API_KEY,
            "endpoints": ["CO-A-02-01", "CO-A-02-02"],  # Keep only core indicators
            "startTime": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "timeType": "15m",
            "token": [base_currency]  # 修改这里，使用动态的币种
        }

        headers = {"Content-Type": "application/json", "X-API-KEY": API_KEY}
        response = requests.post(API_URL, json=request_body, headers=headers)

        if response.status_code == 200:
            data = response.json()
            if data.get("code") == 200 and data.get("data"):
                time_periods = data["data"][0]["timePeriods"]

                # Find first time period with valid data
                for period in time_periods:
                    period_data = period.get("data", [])

                    sentiment = {}
                    valid_data_found = False

                    for item in period_data:
                        endpoint = item.get("endpoint")
                        value = item.get("value", "").strip()

                        if value:  # Only process non-empty values
                            try:
                                if endpoint in ["CO-A-02-01", "CO-A-02-02"]:
                                    sentiment[endpoint] = float(value)
                                    valid_data_found = True
                            except (ValueError, TypeError):
                                continue

                    # If valid data found
                    if valid_data_found and "CO-A-02-01" in sentiment and "CO-A-02-02" in sentiment:
                        positive = sentiment['CO-A-02-01']
                        negative = sentiment['CO-A-02-02']
                        net_sentiment = positive - negative

                        # Correct time delay calculation
                        data_delay = int((datetime.now() - datetime.strptime(
                            period['startTime'], '%Y-%m-%d %H:%M:%S')).total_seconds() // 60)

                        logger.log_warning(f"✅ {symbol}: 使用情绪数据时间: {period['startTime']} (延迟: {data_delay} 分钟)")

                        return {
                            'positive_ratio': positive,
                            'negative_ratio': negative,
                            'net_sentiment': net_sentiment,
                            'data_time': period['startTime'],
                            'data_delay_minutes': data_delay
                        }

                logger.log_warning(f"❌ {symbol}: 所有时间段数据为空")
                return None

        return None
    except Exception as e:
        logger.log_error(f"sentiment_data_{symbol}", str(e))
        return None


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
    
def get_correct_inst_id(symbol: str):
    """获取正确的合约ID"""
    # 对于 BTC/USDT:USDT，正确的instId是 BTC-USDT-SWAP
    config = SYMBOL_CONFIGS[symbol]
    symbol = config.symbol
    if symbol == 'BTC/USDT:USDT':
        return 'BTC-USDT-SWAP'
    elif symbol == 'ETH/USDT:USDT':
        return 'ETH-USDT-SWAP'
    elif symbol == 'SOLUSDT:USDT':
        return 'SOL-USDT-SWAP'
    elif symbol == 'BCH/USDT:USDT':
        return 'BCH-USDT-SWAP'
    elif symbol == 'LTC/USDT:USDT':
        return 'LTC-USDT-SWAP'
    else:
        # 通用处理
        return symbol.replace('/', '-').replace(':USDT', '-SWAP')

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

def create_algo_order(symbol: str, side: str, sz: Union[float, str], trigger_price: Union[float, str], 
                     order_type: str = 'conditional', stop_loss_price: float = None, take_profit_price: float = None) -> bool:
    """创建策略委托订单 - 根据OKX API重新实现"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        inst_id = get_correct_inst_id(symbol)
        
        # 确保参数类型正确
        if isinstance(trigger_price, (int, float)):
            trigger_price = str(round(trigger_price, 1))
        if isinstance(sz, (int, float)):
            sz = str(round(sz, 2))
        if stop_loss_price and isinstance(stop_loss_price, (int, float)):
            stop_loss_price = str(round(stop_loss_price, 1))
        if take_profit_price and isinstance(take_profit_price, (int, float)):
            take_profit_price = str(round(take_profit_price, 1))
        
        margin_mode = getattr(config, 'margin_mode', 'isolated')
        
        # 🆕 根据OKX API构建策略委托参数
        params = {
            'instId': inst_id,
            'tdMode': margin_mode,
            'algoOrdType': order_type,
        }
        
        # 根据订单类型设置不同参数
        if order_type == 'conditional':
            # 条件单参数
            params.update({
                'side': side.upper(),
                'sz': sz,
                'tpTriggerPx': take_profit_price if take_profit_price else '',
                'slTriggerPx': stop_loss_price if stop_loss_price else '',
                'tpOrdPx': '-1',  # 触发后市价单
                'slOrdPx': '-1',  # 触发后市价单
            })
            
            # 如果没有明确指定触发价格，使用止损或止盈价格
            if not trigger_price and stop_loss_price:
                params['slTriggerPx'] = stop_loss_price
            elif not trigger_price and take_profit_price:
                params['tpTriggerPx'] = take_profit_price
            else:
                # 根据方向设置触发价格
                if side.upper() == 'SELL' and take_profit_price:
                    params['tpTriggerPx'] = trigger_price
                elif side.upper() == 'BUY' and stop_loss_price:
                    params['slTriggerPx'] = trigger_price
                
        elif order_type == 'oco':
            # 双向止盈止损单 - 同时设置止损和止盈
            params.update({
                'side': side.upper(),
                'sz': sz,
                'tpTriggerPx': take_profit_price if take_profit_price else '',
                'slTriggerPx': stop_loss_price if stop_loss_price else '',
                'tpOrdPx': '-1',
                'slOrdPx': '-1',
            })
        
        # 记录订单参数
        log_order_params(f"策略委托{order_type}", params, "create_algo_order")
        
        logger.log_info(f"📊 {symbol}: 创建策略委托 - 类型:{order_type}, 方向:{side}, 数量:{sz}")
        
        # 调用OKX策略委托下单接口
        response = exchange.privatePostTradeOrderAlgo(params)
        
        # 记录API响应
        log_api_response(response, "create_algo_order")
        
        if response['code'] == '0':
            algo_id = response['data'][0]['algoId']
            logger.log_info(f"✅ {symbol}: 策略委托创建成功: {algo_id}")
            return True
        else:
            logger.log_error(f"algo_order_failed_{symbol}", f"策略委托创建失败: {response}")
            return False
            
    except Exception as e:
        logger.log_error(f"create_algo_order_{symbol}", f"创建策略委托异常: {str(e)}")
        return False

def cancel_existing_algo_orders(symbol: str):
    """取消指定品种的现有策略委托订单"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        params = {
            'instType': 'SWAP',
            'algoOrdType': 'conditional'
        }
        
        response = exchange.private_get_trade_orders_algo_pending(params)
        
        if response['code'] == '0' and response['data']:
            inst_id = get_correct_inst_id(symbol)
            canceled_count = 0
            
            for order in response['data']:
                if order['instId'] == inst_id:
                    # 取消策略委托订单
                    cancel_params = {
                        'algoId': order['algoId'],
                        'instId': order['instId'],
                        'algoOrdType': 'conditional'
                    }
                    cancel_response = exchange.privatePostTradeCancelAlgoOrder(cancel_params)
                    if cancel_response['code'] == '0':
                        logger.log_info(f"✅ {symbol}: 取消策略委托订单: {order['algoId']}")
                        canceled_count += 1
                    else:
                        logger.log_warning(f"⚠️ {symbol}: 取消策略委托订单失败: {cancel_response}")
            
            if canceled_count > 0:
                logger.log_info(f"✅ {symbol}: 成功取消 {canceled_count} 个策略委托订单")
            else:
                logger.log_info(f"ℹ️ {symbol}: 没有需要取消的策略委托订单")
        else:
            logger.log_info(f"✅ {symbol}: 没有找到待取消的策略委托订单")
                    
    except Exception as e:
        logger.log_error(f"cancel_algo_orders_{symbol}", str(e))

def calculate_dynamic_risk_reward_threshold(symbol: str, price_data: dict) -> float:
    """基于市场波动性计算动态盈亏比阈值"""
    try:
        # 计算ATR波动率
        df = price_data['full_data']
        atr = calculate_atr(df)
        current_price = price_data['price']
        atr_percentage = (atr / current_price) * 100
        
        # 基于波动率设置不同的盈亏比阈值
        if atr_percentage > 3.0:  # 高波动市场
            min_rr = 1.5  # 高波动时可以要求更高盈亏比
        elif atr_percentage > 2.0:  # 中等波动
            min_rr = 1.2
        elif atr_percentage > 1.0:  # 低波动
            min_rr = 1.0
        else:  # 极低波动
            min_rr = 0.8  # 窄幅震荡时降低要求
        
        # 考虑品种特性
        symbol_factors = {
            'BTC/USDT:USDT': 1.0,
            'ETH/USDT:USDT': 0.9,
            'SOL/USDT:USDT': 0.8,
            'LTC/USDT:USDT': 0.7,
            'BCH/USDT:USDT': 0.7
        }
        
        symbol_factor = symbol_factors.get(symbol, 1.0)
        adjusted_min_rr = min_rr * symbol_factor
        
        logger.log_info(f"📊 {symbol}: 波动率{atr_percentage:.2f}%, 动态盈亏比阈值: {adjusted_min_rr:.2f}")
        
        return adjusted_min_rr
        
    except Exception as e:
        logger.log_error("dynamic_rr_threshold", str(e))
        return 1.0  # 默认阈值

def calculate_adaptive_stop_loss(symbol: str, side: str, current_price: float, price_data: dict) -> float:
    """自适应止损计算"""
    config = SYMBOL_CONFIGS[symbol]
    
    try:
        df = price_data['full_data']
        atr = calculate_atr(df)
        
        # 方法1: 基于ATR的止损
        atr_stop_distance = atr * 1.5  # 1.5倍ATR
        
        # 方法2: 基于支撑阻力位的止损
        levels = price_data['levels_analysis']
        
        if side == 'long':
            support_level = levels.get('static_support', current_price * 0.98)
            dynamic_support = levels.get('dynamic_support', current_price * 0.98)
            
            # 选择较近的支撑位
            structure_stop = min(support_level, dynamic_support)
            
            # 结合ATR和结构止损，选择较近的
            atr_stop_price = current_price - atr_stop_distance
            stop_loss = max(structure_stop, atr_stop_price)
            
            # 确保止损合理（不超过当前价格的5%）
            max_stop_distance = current_price * 0.05
            min_stop_price = current_price - max_stop_distance
            stop_loss = max(stop_loss, min_stop_price)
            
        else:  # short
            resistance_level = levels.get('static_resistance', current_price * 1.02)
            dynamic_resistance = levels.get('dynamic_resistance', current_price * 1.02)
            
            # 选择较近的阻力位
            structure_stop = max(resistance_level, dynamic_resistance)
            
            # 结合ATR和结构止损，选择较近的
            atr_stop_price = current_price + atr_stop_distance
            stop_loss = min(structure_stop, atr_stop_price)
            
            # 确保止损合理（不超过当前价格的5%）
            max_stop_distance = current_price * 0.05
            max_stop_price = current_price + max_stop_distance
            stop_loss = min(stop_loss, max_stop_price)
        
        stop_distance_percent = abs(stop_loss - current_price) / current_price * 100
        logger.log_info(f"🎯 {symbol}: 自适应止损 - {stop_loss:.2f} (距离: {stop_distance_percent:.2f}%)")
        
        return stop_loss
        
    except Exception as e:
        logger.log_error(f"adaptive_stop_loss_{symbol}", str(e))
        # 备用止损
        if side == 'long':
            return current_price * 0.98
        else:
            return current_price * 1.02

def calculate_realistic_take_profit(symbol: str, side: str, entry_price: float, stop_loss: float, 
                                  price_data: dict, min_risk_reward: float) -> dict:
    """计算现实的止盈位置"""
    try:
        levels = price_data['levels_analysis']
        current_price = price_data['price']
        
        if side == 'long':
            # 理论止盈（基于最小盈亏比）
            risk = entry_price - stop_loss
            theoretical_tp = entry_price + (risk * min_risk_reward)
            
            # 现实止盈（基于阻力位）
            resistance_level = levels.get('static_resistance', current_price * 1.03)
            dynamic_resistance = levels.get('dynamic_resistance', current_price * 1.03)
            realistic_tp = min(resistance_level, dynamic_resistance)
            
            # 选择较近的止盈
            take_profit = min(theoretical_tp, realistic_tp)
            
            # 计算实际盈亏比
            actual_reward = take_profit - entry_price
            actual_rr = actual_reward / risk if risk > 0 else 0
            
        else:  # short
            # 理论止盈（基于最小盈亏比）
            risk = stop_loss - entry_price
            theoretical_tp = entry_price - (risk * min_risk_reward)
            
            # 现实止盈（基于支撑位）
            support_level = levels.get('static_support', current_price * 0.97)
            dynamic_support = levels.get('dynamic_support', current_price * 0.97)
            realistic_tp = max(support_level, dynamic_support)
            
            # 选择较近的止盈
            take_profit = max(theoretical_tp, realistic_tp)
            
            # 计算实际盈亏比
            actual_reward = entry_price - take_profit
            actual_rr = actual_reward / risk if risk > 0 else 0
        
        return {
            'take_profit': take_profit,
            'actual_risk_reward': actual_rr,
            'is_acceptable': actual_rr >= min_risk_reward * 0.8  # 允许80%的阈值
        }
        
    except Exception as e:
        logger.log_error(f"realistic_take_profit_{symbol}", str(e))
        # 备用止盈
        if side == 'long':
            return {
                'take_profit': entry_price * 1.02,
                'actual_risk_reward': 1.0,
                'is_acceptable': True
            }
        else:
            return {
                'take_profit': entry_price * 0.98,
                'actual_risk_reward': 1.0,
                'is_acceptable': True
            }


def validate_risk_reward_before_trade(symbol: str, entry_price: float, stop_loss_price: float, 
                                    take_profit_price: float, side: str, min_risk_reward: float = 1.5) -> dict:
    """在交易前验证盈亏比，决定是否开仓"""
    try:
        risk_reward_ratio = calculate_risk_reward_ratio(entry_price, stop_loss_price, take_profit_price, side)
        
        if side == 'long':
            risk_amount = entry_price - stop_loss_price
            reward_amount = take_profit_price - entry_price
            risk_percent = (risk_amount / entry_price) * 100
            reward_percent = (reward_amount / entry_price) * 100
        else:  # short
            risk_amount = stop_loss_price - entry_price
            reward_amount = entry_price - take_profit_price
            risk_percent = (risk_amount / entry_price) * 100
            reward_percent = (reward_amount / entry_price) * 100
        
        validation_result = {
            'is_valid': risk_reward_ratio >= min_risk_reward,
            'risk_reward_ratio': risk_reward_ratio,
            'risk_percent': risk_percent,
            'reward_percent': reward_percent,
            'risk_amount': risk_amount,
            'reward_amount': reward_amount,
            'message': ''
        }
        
        if validation_result['is_valid']:
            validation_result['message'] = f"✅ 盈亏比达标: {risk_reward_ratio:.2f} >= {min_risk_reward}"
        else:
            validation_result['message'] = f"❌ 盈亏比不足: {risk_reward_ratio:.2f} < {min_risk_reward}，放弃开仓"
        
        return validation_result
        
    except Exception as e:
        logger.log_error("risk_reward_validation", str(e))
        return {
            'is_valid': False,
            'risk_reward_ratio': 0,
            'risk_percent': 0,
            'reward_percent': 0,
            'risk_amount': 0,
            'reward_amount': 0,
            'message': f"盈亏比验证失败: {str(e)}"
        }

def find_optimal_risk_reward_levels(symbol: str, side: str, current_price: float, price_data: dict, 
                                  min_risk_reward: float = 1.5) -> dict:
    """寻找满足最小盈亏比的最优止损止盈位置"""
    config = SYMBOL_CONFIGS[symbol]
    
    try:
        # 基于市场结构计算止损位置
        if side == 'long':
            # 多头：止损放在支撑位下方
            support_level = price_data['levels_analysis'].get('static_support', current_price * 0.98)
            dynamic_support = price_data['levels_analysis'].get('dynamic_support', current_price * 0.98)
            
            # 选择较近的支撑作为止损参考
            stop_loss_candidate = min(support_level, dynamic_support)
            
            # 添加安全缓冲（1%）
            stop_loss_price = stop_loss_candidate * 0.99
            
            # 计算满足最小盈亏比的止盈位置
            risk_amount = current_price - stop_loss_price
            min_reward_amount = risk_amount * min_risk_reward
            take_profit_price = current_price + min_reward_amount
            
            # 检查止盈位置是否合理（不超过阻力位）
            resistance_level = price_data['levels_analysis'].get('static_resistance', current_price * 1.05)
            dynamic_resistance = price_data['levels_analysis'].get('dynamic_resistance', current_price * 1.05)
            
            max_reasonable_tp = min(resistance_level, dynamic_resistance)
            
            if take_profit_price > max_reasonable_tp:
                # 止盈位置超出合理范围，需要重新计算
                available_reward = max_reasonable_tp - current_price
                actual_rr = available_reward / risk_amount if risk_amount > 0 else 0
                
                if actual_rr >= min_risk_reward:
                    take_profit_price = max_reasonable_tp
                else:
                    # 无法满足最小盈亏比
                    return {
                        'is_viable': False,
                        'stop_loss': stop_loss_price,
                        'take_profit': take_profit_price,
                        'risk_reward_ratio': actual_rr,
                        'message': f"止盈位置超出阻力位，实际盈亏比 {actual_rr:.2f} 不足 {min_risk_reward}"
                    }
                    
        else:  # short
            # 空头：止损放在阻力位上方
            resistance_level = price_data['levels_analysis'].get('static_resistance', current_price * 1.02)
            dynamic_resistance = price_data['levels_analysis'].get('dynamic_resistance', current_price * 1.02)
            
            # 选择较近的阻力作为止损参考
            stop_loss_candidate = max(resistance_level, dynamic_resistance)
            
            # 添加安全缓冲（1%）
            stop_loss_price = stop_loss_candidate * 1.01
            
            # 计算满足最小盈亏比的止盈位置
            risk_amount = stop_loss_price - current_price
            min_reward_amount = risk_amount * min_risk_reward
            take_profit_price = current_price - min_reward_amount
            
            # 检查止盈位置是否合理（不低于支撑位）
            support_level = price_data['levels_analysis'].get('static_support', current_price * 0.95)
            dynamic_support = price_data['levels_analysis'].get('dynamic_support', current_price * 0.95)
            
            min_reasonable_tp = max(support_level, dynamic_support)
            
            if take_profit_price < min_reasonable_tp:
                # 止盈位置超出合理范围，需要重新计算
                available_reward = current_price - min_reasonable_tp
                actual_rr = available_reward / risk_amount if risk_amount > 0 else 0
                
                if actual_rr >= min_risk_reward:
                    take_profit_price = min_reasonable_tp
                else:
                    # 无法满足最小盈亏比
                    return {
                        'is_viable': False,
                        'stop_loss': stop_loss_price,
                        'take_profit': take_profit_price,
                        'risk_reward_ratio': actual_rr,
                        'message': f"止盈位置超出支撑位，实际盈亏比 {actual_rr:.2f} 不足 {min_risk_reward}"
                    }
        
        # 验证最终的盈亏比
        final_rr = calculate_risk_reward_ratio(current_price, stop_loss_price, take_profit_price, side)
        
        if final_rr >= min_risk_reward:
            return {
                'is_viable': True,
                'stop_loss': stop_loss_price,
                'take_profit': take_profit_price,
                'risk_reward_ratio': final_rr,
                'message': f"找到可行位置，盈亏比: {final_rr:.2f}"
            }
        else:
            return {
                'is_viable': False,
                'stop_loss': stop_loss_price,
                'take_profit': take_profit_price,
                'risk_reward_ratio': final_rr,
                'message': f"无法满足最小盈亏比，实际: {final_rr:.2f}"
            }
            
    except Exception as e:
        logger.log_error(f"optimal_levels_finding_{symbol}", str(e))
        return {
            'is_viable': False,
            'stop_loss': 0,
            'take_profit': 0,
            'risk_reward_ratio': 0,
            'message': f"寻找最优位置失败: {str(e)}"
        }

def calculate_market_structure_levels(symbol: str, side: str, current_price: float, price_data: dict) -> dict:
    """基于市场结构计算止损止盈位置"""
    config = SYMBOL_CONFIGS[symbol]
    
    try:
        levels_analysis = price_data['levels_analysis']
        
        if side == 'long':
            # 多头交易
            stop_loss = levels_analysis.get('static_support', current_price * 0.98)
            take_profit = levels_analysis.get('static_resistance', current_price * 1.03)
            
            # 使用动态支撑阻力作为备选
            dynamic_sl = levels_analysis.get('dynamic_support', current_price * 0.98)
            dynamic_tp = levels_analysis.get('dynamic_resistance', current_price * 1.03)
            
            # 选择更保守的止损（较高的）和更现实的止盈（较低的）
            stop_loss = max(stop_loss, dynamic_sl)
            take_profit = min(take_profit, dynamic_tp)
            
        else:  # short
            # 空头交易
            stop_loss = levels_analysis.get('static_resistance', current_price * 1.02)
            take_profit = levels_analysis.get('static_support', current_price * 0.97)
            
            # 使用动态支撑阻力作为备选
            dynamic_sl = levels_analysis.get('dynamic_resistance', current_price * 1.02)
            dynamic_tp = levels_analysis.get('dynamic_support', current_price * 0.97)
            
            # 选择更保守的止损（较低的）和更现实的止盈（较高的）
            stop_loss = min(stop_loss, dynamic_sl)
            take_profit = max(take_profit, dynamic_tp)
        
        # 添加安全缓冲
        if side == 'long':
            stop_loss = stop_loss * 0.995  # 额外0.5%缓冲
            take_profit = take_profit * 0.995  # 避免正好在阻力位
        else:
            stop_loss = stop_loss * 1.005  # 额外0.5%缓冲
            take_profit = take_profit * 1.005  # 避免正好在支撑位
        
        return {
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'message': '基于市场结构计算'
        }
        
    except Exception as e:
        logger.log_error(f"market_structure_levels_{symbol}", str(e))
        # 备用计算
        if side == 'long':
            return {
                'stop_loss': current_price * 0.98,
                'take_profit': current_price * 1.03,
                'message': '备用计算'
            }
        else:
            return {
                'stop_loss': current_price * 1.02,
                'take_profit': current_price * 0.97,
                'message': '备用计算'
            }





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
        
        entry_price = current_position['entry_price']
        side = current_position['side']
        
        # 根据持仓方向确定条件单参数
        if side == 'long':
            # 多头持仓：设置止损卖出单，触发价格为开仓价
            algo_order_type = 'conditional'  # 条件单
            trigger_action = 'sell'  # 触发后卖出
            trigger_price = entry_price  # 触发价格设为开仓价（保本）
            order_type = 'market'  # 市价单
            
            logger.log_info(f"🛡️ 设置多头保本止损: 触发价{trigger_price:.2f}, 数量{remaining_size}张")
            
        else:  # short
            # 空头持仓：设置止损买入单，触发价格为开仓价
            algo_order_type = 'conditional'  # 条件单
            trigger_action = 'buy'  # 触发后买入
            trigger_price = entry_price  # 触发价格设为开仓价（保本）
            order_type = 'market'  # 市价单
            
            logger.log_info(f"🛡️ 设置空头保本止损: 触发价{trigger_price:.2f}, 数量{remaining_size}张")
        
        # 取消该交易对现有的所有条件单（避免重复）
        cancel_existing_algo_orders(symbol)
        
        # 创建算法订单
        result = create_algo_order(
        symbol=symbol,  # ✅ 修正参数名
        side=trigger_action,
        sz=remaining_size,
        trigger_price=trigger_price
        )

        if result:
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

def calculate_atr(df, period=14):
    """计算平均真实波幅(ATR)"""
    try:
        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())
        
        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = true_range.rolling(period).mean().iloc[-1]
        return atr
    except Exception as e:
        logger.log_error("atr_calculation", str(e))
        return df['close'].iloc[-1] * 0.02  # 默认2%作为ATR

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
            logger.log_error(f"Get_kline_{symbol} failed, retry {i+1}/{max_retries}", str(e))
            time.sleep(1)
    return None

def fetch_ohlcv(symbol: str):
    """获取指定交易品种的K线数据"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        ohlcv = fetch_ohlcv_with_retry(symbol)
        
        if ohlcv is None:
            logger.log_warning(f"❌ Failed to fetch K-line data for {symbol}")
            return None, None

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        # Calculate technical indicators
        df = calculate_technical_indicators(df)

        current_data = df.iloc[-1]
        previous_data = df.iloc[-2]

        # Get technical analysis data
        trend_analysis = get_market_trend(df)
        levels_analysis = get_support_resistance_levels(df)

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
            'full_data': df
        }

        return df, price_data
        
    except Exception as e:
        logger.log_error(f"fetch_ohlcv_{symbol}", str(e))
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

def generate_technical_analysis_text(price_data):
    """Generate technical analysis text"""
    if 'technical_data' not in price_data:
        return "Technical indicator data unavailable"

    tech = price_data['technical_data']
    trend = price_data.get('trend_analysis', {})
    levels = price_data.get('levels_analysis', {})

    # Check data validity
    def safe_float(value, default=0):
        return float(value) if value and pd.notna(value) else default

    analysis_text = f"""
    【技术指标概览】
    📈 趋势: {trend.get('overall', 'N/A')} | RSI: {safe_float(tech['rsi']):.1f}
    📊 均线: 5期{tech.get('sma_5', 0):.2f} | 20期{tech.get('sma_20', 0):.2f} | 50期{tech.get('sma_50', 0):.2f}
    🎯 关键位: 阻力{levels.get('static_resistance', 0):.2f} | 支撑{levels.get('static_support', 0):.2f}
    """
    return analysis_text


def get_current_position(symbol: str) -> Optional[dict]:
    """Get current position status - OKX version"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        positions = exchange.fetch_positions([config.symbol])
        if not positions:
            return None
        
        for pos in positions:
            if pos['symbol'] == config.symbol:
                contracts = float(pos['contracts']) if pos['contracts'] else 0

                if contracts > 0:
                    return {
                        'side': pos['side'],  # 'long' or 'short'
                        'size': contracts,
                        'entry_price': float(pos['entryPrice']) if pos['entryPrice'] else 0,
                        'unrealized_pnl': float(pos['unrealizedPnl']) if pos['unrealizedPnl'] else 0,
                        'leverage': float(pos['leverage']) if pos['leverage'] else config.leverage,
                        'symbol': pos['symbol']
                    }

        return None

    except Exception as e:
        logger.log_error(f"position_fetch_{symbol}", f"Failed to fetch positions: {str(e)}")
        return None
    
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
                
                logger.log_info(f"📈 {symbol}: 设置多头移动止损 - {trailing_stop_price:.2f} (当前盈利: {profit_ratio:.2%})")
                
                return set_trailing_stop_order(symbol, current_position, trailing_stop_price)
                
        else:  # short
            profit_ratio = (entry_price - current_price) / entry_price
            if profit_ratio >= trailing_config['trailing_activation_ratio']:
                # 计算移动止损价格
                trailing_stop_price = current_price * (1 + trailing_config['trailing_distance_ratio'])
                
                # 确保移动止损不会高于入场价（保本）
                trailing_stop_price = min(trailing_stop_price, entry_price)
                
                logger.log_info(f"📉 {symbol}: 设置空头移动止损 - {trailing_stop_price:.2f} (当前盈利: {profit_ratio:.2%})")
                
                return set_trailing_stop_order(symbol, current_position, trailing_stop_price)
                
        return False
        
    except Exception as e:
        logger.log_error(f"trailing_stop_setup_{symbol}", f"移动止损设置失败: {str(e)}")
        return False

def set_trailing_stop_order(symbol: str, current_position: dict, stop_price: float):
    """设置移动止损订单 - 先设置新的，再取消旧的"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        side = current_position['side']
        position_size = current_position['size']
        
        if side == 'long':
            # 多头：止损卖出
            trigger_action = 'sell'
        else:
            # 空头：止损买入
            trigger_action = 'buy'
        
        # 先创建新的移动止损条件单
        result = create_algo_order(
            symbol=symbol,
            side=trigger_action,
            sz=position_size,
            trigger_price=stop_price
        )
        
        if result:
            logger.log_info(f"✅ 新移动止损设置成功: {stop_price:.2f}")
            
            # 等待新订单处理完成
            time.sleep(1)
            
            # 现在取消旧的止损单
            cancel_existing_algo_orders(symbol)
            
            return True
        else:
            logger.log_error("移动止损设置失败")
            return False
            
    except Exception as e:
        logger.log_error("set_trailing_stop_order", str(e))
        return False


def adjust_take_profit_dynamically(symbol: str, current_position: dict, price_data: dict) -> bool:
    """动态调整止盈位置"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        current_price = price_data['price']
        entry_price = current_position['entry_price']
        side = current_position['side']
        
        # 计算当前盈利比例
        if side == 'long':
            profit_ratio = (current_price - entry_price) / entry_price
        else:
            profit_ratio = (entry_price - current_price) / entry_price
        
        # 根据盈利幅度调整止盈
        if profit_ratio >= 0.10:  # 盈利10%以上
            # 重新计算更激进的止盈
            new_take_profit = calculate_intelligent_take_profit(
                symbol, side, entry_price, price_data, risk_reward_ratio=3.0
            )
            
            # 取消旧的止盈单
            cancel_existing_take_profit_orders(symbol)
            
            # 设置新的止盈单
            if side == 'long':
                return create_take_profit_algo_order(symbol, 'sell', current_position['size'], new_take_profit)
            else:
                return create_take_profit_algo_order(symbol, 'buy', current_position['size'], new_take_profit)
                
        return False
        
    except Exception as e:
        logger.log_error(f"dynamic_take_profit_{symbol}", f"动态止盈调整失败: {str(e)}")
        return False

def cancel_existing_take_profit_orders(symbol: str):
    """取消现有的止盈订单"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        params = {
            'instType': 'SWAP',
            'algoOrdType': 'conditional'
        }
        
        response = exchange.private_get_trade_orders_algo_pending(params)
        
        if response['code'] == '0' and response['data']:
            inst_id = get_correct_inst_id(symbol)
            
            for order in response['data']:
                if order['instId'] == inst_id and 'tpTriggerPx' in order:
                    # 取消止盈条件单
                    cancel_params = {
                        'algoId': order['algoId'],
                        'instId': order['instId'],
                        'algoOrdType': 'conditional'
                    }
                    cancel_response = exchange.privatePostTradeCancelAlgoOrder(cancel_params)
                    if cancel_response['code'] == '0':
                        logger.log_info(f"✅ {symbol}: 取消现有止盈单: {order['algoId']}")
                    else:
                        logger.log_warning(f"⚠️ {symbol}: 取消止盈单失败: {cancel_response}")
        else:
            logger.log_info(f"✅ {symbol}: 没有找到待取消的止盈单")
                    
    except Exception as e:
        logger.log_error(f"cancel_take_profit_orders_{symbol}", str(e))


def calculate_intelligent_take_profit(symbol: str, side: str, entry_price: float, price_data: dict, risk_reward_ratio: float = 2.0) -> float:
    """计算智能止盈价格"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        current_price = price_data['price']
        df = price_data['full_data']
        
        if side == 'long':
            # 多头止盈计算
            # 方法1: 基于阻力位
            resistance_level = price_data['levels_analysis'].get('static_resistance', current_price * 1.05)
            
            # 方法2: 基于ATR
            atr = calculate_atr(df)
            atr_take_profit = current_price + (atr * risk_reward_ratio)
            
            # 方法3: 基于固定风险回报比
            risk = abs(entry_price - price_data.get('stop_loss', entry_price * 0.98))
            rr_take_profit = entry_price + (risk * risk_reward_ratio)
            
            # 取最合理的止盈价格
            take_profit_price = min(resistance_level, atr_take_profit, rr_take_profit)
            
            # 确保止盈价格合理
            min_take_profit = current_price * 1.01  # 至少1%盈利
            take_profit_price = max(take_profit_price, min_take_profit)
            
        else:  # short
            # 空头止盈计算
            # 方法1: 基于支撑位
            support_level = price_data['levels_analysis'].get('static_support', current_price * 0.95)
            
            # 方法2: 基于ATR
            atr = calculate_atr(df)
            atr_take_profit = current_price - (atr * risk_reward_ratio)
            
            # 方法3: 基于固定风险回报比
            risk = abs(entry_price - price_data.get('stop_loss', entry_price * 1.02))
            rr_take_profit = entry_price - (risk * risk_reward_ratio)
            
            # 取最合理的止盈价格
            take_profit_price = max(support_level, atr_take_profit, rr_take_profit)
            
            # 确保止盈价格合理
            max_take_profit = current_price * 0.99  # 至少1%盈利
            take_profit_price = min(take_profit_price, max_take_profit)
        
        take_profit_ratio = abs(take_profit_price - entry_price) / entry_price * 100
        logger.log_info(f"🎯 {symbol}: 智能止盈计算 - 入场{entry_price:.2f}, 止盈{take_profit_price:.2f} (盈利{take_profit_ratio:.2f}%)")
        
        return take_profit_price
        
    except Exception as e:
        logger.log_error(f"take_profit_calculation_{symbol}", f"止盈计算失败: {str(e)}")
        # 备用止盈计算
        if side == 'long':
            return entry_price * 1.03  # 默认3%止盈
        else:
            return entry_price * 0.97  # 默认3%止盈


def safe_json_parse(json_str):
    """Safely parse JSON, handle non-standard format situations"""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        try:
            # Fix common JSON format issues
            json_str = json_str.replace("'", '"')
            json_str = re.sub(r'(\w+):', r'"\1":', json_str)
            json_str = re.sub(r',\s*}', '}', json_str)
            json_str = re.sub(r',\s*]', ']', json_str)
            # 🆕 修复：移除数字中的逗号（如 106,600 -> 106600）
            json_str = re.sub(r'(\d),(\d)', r'\1\2', json_str)
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.log_error("json_parsing", f"Failed to parse: {json_str}")
            logger.log_error("json_parsing", f"Error details: {e}")
            return None


def create_fallback_signal(price_data):
    """Create backup trading signal"""
    return {
        "signal": "HOLD",
        "reason": "Conservative strategy adopted due to temporary unavailability of technical analysis",
        "stop_loss": price_data['price'] * 0.98,  # -2%
        "take_profit": price_data['price'] * 1.02,  # +2%
        "confidence": "LOW",
        "is_fallback": True
    }

@retry_on_failure(max_retries=3, delay=2)
def analyze_with_deepseek(symbol: str, price_data: dict):
    """Use DeepSeek to analyze market and generate trading signals (enhanced version)"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        # Get the client (will be initialized on the first call)
        client = get_deepseek_client(symbol)
    
        # Generate technical analysis text
        technical_analysis = generate_technical_analysis_text(price_data)

        # Build K-line data text
        kline_text = f"【Recent 5 {config.timeframe} K-line Data】\n"
        for i, kline in enumerate(price_data['kline_data'][-5:]):
            trend = "Bullish" if kline['close'] > kline['open'] else "Bearish"
            change = ((kline['close'] - kline['open']) / kline['open']) * 100
            kline_text += f"K-line {i + 1}: {trend} Open:{kline['open']:.2f} Close:{kline['close']:.2f} Change:{change:+.2f}%\n"

        # Add previous trading signal
        signal_text = ""
        if symbol in signal_history and signal_history[symbol]:
            last_signal = signal_history[symbol][-1]
            signal_text = f"\n【Previous Trading Signal】\nSignal: {last_signal.get('signal', 'N/A')}\nConfidence: {last_signal.get('confidence', 'N/A')}"
        # Get sentiment data
        sentiment_data = get_sentiment_indicators(symbol)
        # Simplified sentiment text - too much is useless
        if sentiment_data:
            sign = '+' if sentiment_data['net_sentiment'] >= 0 else ''
            sentiment_text = f"【Market Sentiment】Optimistic {sentiment_data['positive_ratio']:.1%} Pessimistic {sentiment_data['negative_ratio']:.1%} Net {sign}{sentiment_data['net_sentiment']:.3f}"
        else:
            sentiment_text = "【Market Sentiment】Data temporarily unavailable"

        # Add current position information
        current_pos = get_current_position(symbol)
        position_text = "No position" if not current_pos else f"{current_pos['side']} position, Quantity: {current_pos['size']}, P&L: {current_pos['unrealized_pnl']:.2f}USDT"
        pnl_text = f", Position P&L: {current_pos['unrealized_pnl']:.2f} USDT" if current_pos else ""

        # 🆕 Enhanced Trend Reversal Analysis Criteria
        trend_reversal_criteria = """
        【Trend Reversal Judgment Criteria - Must meet at least 2 conditions】
        1. Price breaks through key support/resistance levels + volume amplification
        2. Break of major moving averages (e.g., 20-period, 50-period)  
        3. RSI reversal from overbought/oversold areas and forms divergence
        4. MACD shows clear death cross/golden cross signal

        【Position Management Principles】
        - Existing position opposite to current signal → Strongly consider closing position
        - Existing position same as current signal → Continue holding, check stop loss
        - Signal is HOLD but position exists → Decide whether to hold based on technical indicators

        【Key Technical Levels for {symbol}】
        - Strong Resistance: When price approaches recent high + Bollinger Band upper
        - Strong Support: When price approaches recent low + Bollinger Band lower
        - Breakout Confirmation: Requires closing price break + volume > 20-period average
        - False Breakout: Price breaks but fails to sustain, immediately reverses
        """

        prompt = f"""
        You are a professional cryptocurrency trading analyst. Please analyze based on the following {symbol} {config.timeframe} period data:  # 修改这里

        {kline_text}

        {technical_analysis}

        {signal_text}

        {sentiment_text}  # Add sentiment analysis

        【Current Market】
        - Current price: ${price_data['price']:,.2f}
        - Time: {price_data['timestamp']}
        - Current K-line high: ${price_data['high']:,.2f}
        - Current K-line low: ${price_data['low']:,.2f}
        - Current K-line volume: {price_data['volume']:.2f} {symbol}
        - Price change: {price_data['price_change']:+.2f}%
        - Current position: {position_text}{pnl_text}

        {trend_reversal_criteria}  # 🆕 Add enhanced trend reversal criteria

        【Anti-Frequent Trading Important Principles】
        1. **Trend Continuity Priority**: Do not change overall trend judgment based on single K-line or short-term fluctuations
        2. **Position Stability**: Maintain existing position direction unless trend clearly reverses strongly
        3. **Reversal Confirmation**: Require at least 2-3 technical indicators to simultaneously confirm trend reversal before changing signal
        4. **Cost Awareness**: Reduce unnecessary position adjustments, every trade has costs

        【Trading Guidance Principles - Must Follow】
        1. **Technical Analysis Dominant** (Weight 60%): Trend, support resistance, K-line patterns are main basis
        2. **Market Sentiment Auxiliary** (Weight 30%): Sentiment data used to verify technical signals, cannot be used alone as trading reason
        - Sentiment and technical same direction → Enhance signal confidence
        - Sentiment and technical divergence → Mainly based on technical analysis, sentiment only as reference
        - Sentiment data delay → Reduce weight, use real-time technical indicators as main
        3. **Risk Management** (Weight 10%): Consider position, profit/loss status and stop loss position
        4. **Trend Following**: Take immediate action when clear trend appears, do not over-wait
        5. Because trading BTC, long position weight can be slightly higher
        6. **Signal Clarity**:
        - Strong uptrend → BUY signal
        - Strong downtrend → SELL signal
        - Only in narrow range consolidation, no clear direction → HOLD signal
        7. **Technical Indicator Weight**:
        - Trend (moving average arrangement) > RSI > MACD > Bollinger Bands
        - Price breaking key support/resistance levels is important signal

        【Current Technical Condition Analysis】
        - Overall trend: {price_data['trend_analysis'].get('overall', 'N/A')}
        - Short-term trend: {price_data['trend_analysis'].get('short_term', 'N/A')}
        - RSI status: {price_data['technical_data'].get('rsi', 0):.1f} ({'Overbought' if price_data['technical_data'].get('rsi', 0) > 70 else 'Oversold' if price_data['technical_data'].get('rsi', 0) < 30 else 'Neutral'})
        - MACD direction: {price_data['trend_analysis'].get('macd', 'N/A')}

        【Intelligent Position Management Rules - Must Follow】

        1. **Reduce Over-Conservatism**:
        - Do not over-HOLD due to slight overbought/oversold in clear trends
        - RSI in 30-70 range is healthy range, should not be main HOLD reason
        - Bollinger Band position in 20%-80% is normal fluctuation range

        2. **Trend Following Priority**:
        - Strong uptrend + any RSI value → Active BUY signal
        - Strong downtrend + any RSI value → Active SELL signal
        - Consolidation + no clear direction → HOLD signal

        3. **Breakout Trading Signals**:
        - Price breaks key resistance + volume amplification → High confidence BUY
        - Price breaks key support + volume amplification → High confidence SELL

        4. **Position Optimization Logic**:
        - Existing position and trend continues → Maintain or BUY/SELL signal
        - Clear trend reversal → Timely reverse signal
        - Do not over-HOLD because of existing position

        【Important】Please make clear judgments based on technical analysis, avoid missing trend opportunities due to over-caution!

        【Analysis Requirements】
        Based on above analysis, please provide clear trading signal

        Please reply in following JSON format:
        {{
            "signal": "BUY|SELL|HOLD",
            "reason": "Brief analysis reason (including trend judgment and technical basis)",
            "stop_loss": specific price,
            "take_profit": specific price,
            "confidence": "HIGH|MEDIUM|LOW"
        }}
        """

        try:
            response = client.chat.completions.create(
    model="deepseek-chat",
    messages=[
        {"role": "system",
         "content": f"""You are a professional trader specializing in {config.timeframe} period trend analysis and trend reversal detection. 
                Key Responsibilities:
                1. Analyze trend strength and identify potential reversal points
                2. Use multiple confirmation criteria for trend reversals
                3. Provide clear trading signals based on technical analysis
                4. Consider existing positions in your analysis
                5. Strictly follow JSON format requirements

                Trend Reversal Focus:
                - Pay special attention to breakouts of key support/resistance levels
                - Look for confirmation from multiple indicators (RSI divergence, MACD cross, volume)
                - Consider the broader market context in your analysis"""},
            {"role": "user", "content": prompt}
            ],
                stream=False,
                temperature=0.1
            )

            # Safely parse JSON
            result = response.choices[0].message.content.strip()
            logger.log_info(f"DeepSeek original reply: {result}")

            # 关键：清理非法引号（如 20-"period" → 20-period）
            cleaned_content = re.sub(r'(\d+)-"(\w+)"', r'\1-\2', result)  # 移除数字后的引号
            cleaned_content = re.sub(r'"(\w+)"-(\d+)', r'\1-\2', cleaned_content)  # 移除数字前的引号（如果有）

            # Extract JSON part
            start_idx = cleaned_content.find('{')
            end_idx = cleaned_content.rfind('}') + 1

            if start_idx != -1 and end_idx != 0:
                json_str = cleaned_content[start_idx:end_idx]
                signal_data = safe_json_parse(json_str)

                if signal_data is None:
                    signal_data = create_fallback_signal(price_data)
            else:
                signal_data = create_fallback_signal(price_data)

            # Verify required fields
            required_fields = ['signal', 'reason', 'stop_loss', 'take_profit', 'confidence']
            if not all(field in signal_data for field in required_fields):
                signal_data = create_fallback_signal(price_data)

            # Save signal to history record
            signal_data['timestamp'] = price_data['timestamp']
            add_to_signal_history(symbol, signal_data)

            # Signal statistics
            if symbol in signal_history:
                signal_count = len([s for s in signal_history[symbol] if s.get('signal') == signal_data['signal']])
                total_signals = len(signal_history[symbol])
            else:
                signal_count = 0
                total_signals = 0
            logger.log_info(f"Signal statistics: {signal_data['signal']} (Appeared {signal_count} times in recent {total_signals} signals)")

            # Signal continuity check
            if symbol in signal_history and len(signal_history[symbol]) >= 3:
                last_three = [s['signal'] for s in signal_history[symbol][-3:]]
                if len(set(last_three)) == 1:
                    logger.log_warning(f"⚠️ Note: Consecutive 3 {signal_data['signal']} signals")

            return signal_data

        except Exception as api_error:
                # 🔴API call or response processing failed
                logger.log_error("deepseek_api_call",  f"API调用失败: {str(api_error)}")
                return create_fallback_signal(price_data)
            
    except Exception as prep_error:
        # 🔴Preparation phase failed
        logger.log_error("analysis_preparation", f"API调用失败: {str(prep_error)}")
        return create_fallback_signal(price_data)

def check_market_conditions(symbol: str) -> bool:
    """Check if market conditions are suitable for trading."""
    config = SYMBOL_CONFIGS[symbol]
    try:
        ticker = exchange.fetch_ticker(config.symbol)
        spread = (ticker['ask'] - ticker['bid']) / ticker['bid']
        
        # If spread is too wide, avoid trading
        if spread > 0.002:  # 0.2%
            logger.log_warning(f"⚠️ Wide spread: {spread:.4%}, avoiding trade.")
            return False
            
        return True
    except Exception as e:
        logger.log_error("market_conditions", str(e))
        return False

def check_trading_frequency():
    """Check if we are trading too frequently."""
    global signal_history
    
    if len(signal_history) < 3:
        return True
    
    recent_signals = [s['signal'] for s in signal_history[-3:]]
    signal_changes = sum(1 for i in range(1, len(recent_signals)) 
                      if recent_signals[i] != recent_signals[i-1])
    
    # If there are too many signal changes, pause trading
    if signal_changes >= 2:
        logger.log_info("⚠️ Too frequent signal changes, pausing trading.")
        return False
    
    return True

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


def calculate_kline_based_stop_loss(side, entry_price, price_data, max_stop_loss_ratio=0.40):
    """
    基于K线结构计算止损价格 - 优化版本
    """
    try:
        df = price_data['full_data']
        current_price = price_data['price']
        
        # 计算ATR
        atr = calculate_atr(df)
        
        if side == 'long':
            # 多头止损：取支撑位和ATR止损中的较小值（更严格的止损）
            support_level = price_data['levels_analysis'].get('static_support', current_price * 0.98)
            
            # 基于ATR的止损
            stop_loss_by_atr = current_price - (atr * 1.5)  # 1.5倍ATR
            
            # 选择较严格的止损
            stop_loss_price = min(support_level, stop_loss_by_atr)
            
            # 确保止损不超过最大比例
            max_stop_loss_price = current_price * (1 - max_stop_loss_ratio)
            stop_loss_price = max(stop_loss_price, max_stop_loss_price)
            
            # 确保止损在合理范围内（不低于当前价格的2%）
            min_stop_loss = current_price * 0.98
            stop_loss_price = max(stop_loss_price, min_stop_loss)
            
        else:  # short
            # 空头止损：取阻力位和ATR止损中的较大值（更严格的止损）
            resistance_level = price_data['levels_analysis'].get('static_resistance', current_price * 1.02)
            
            # 基于ATR的止损
            stop_loss_by_atr = current_price + (atr * 1.5)
            
            # 选择较严格的止损
            stop_loss_price = max(resistance_level, stop_loss_by_atr)
            
            # 确保止损不超过最大比例
            max_stop_loss_price = current_price * (1 + max_stop_loss_ratio)
            stop_loss_price = min(stop_loss_price, max_stop_loss_price)
            
            # 确保止损在合理范围内（不高于当前价格的2%）
            max_stop_loss = current_price * 1.02
            stop_loss_price = min(stop_loss_price, max_stop_loss)
        
        stop_loss_ratio = abs(stop_loss_price - current_price) / current_price * 100
        logger.log_info(f"🎯 K线结构止损计算: {side}方向, 入场{current_price:.2f}, 止损{stop_loss_price:.2f} (距离{stop_loss_ratio:.2f}%)")
        return stop_loss_price
        
    except Exception as e:
        logger.log_error("stop_loss_calculation", str(e))
        # 备用止损计算
        if side == 'long':
            return current_price * (1 - 0.02)  # 2%止损
        else:
            return current_price * (1 + 0.02)  # 2%止损

def validate_and_adjust_prices(side, calculated_stop_loss, current_price, bid_price, ask_price):
    """验证并调整价格参数"""
    try:
        # 验证止损价格
        if side == 'buy':
            # 多头：止损必须低于当前价格
            if calculated_stop_loss >= current_price:
                logger.log_warning(f"⚠️ 多头止损价格调整: {calculated_stop_loss:.2f} >= {current_price:.2f}")
                calculated_stop_loss = current_price * 0.98
                logger.log_info(f"🔄 调整后止损: {calculated_stop_loss:.2f}")
            
            # 计算限价单价格（确保高于卖一价）
            limit_price = max(ask_price * 1.001, current_price * 1.001)
            
        else:  # sell
            # 空头：止损必须高于当前价格
            if calculated_stop_loss <= current_price:
                logger.log_warning(f"⚠️ 空头止损价格调整: {calculated_stop_loss:.2f} <= {current_price:.2f}")
                calculated_stop_loss = current_price * 1.02
                logger.log_info(f"🔄 调整后止损: {calculated_stop_loss:.2f}")
            
            # 计算限价单价格（确保低于买一价）
            limit_price = min(bid_price * 0.999, current_price * 0.999)
        
        logger.log_info(f"✅ 价格验证完成: 限价{limit_price:.2f}, 止损{calculated_stop_loss:.2f}")
        return limit_price, calculated_stop_loss
        
    except Exception as e:
        logger.log_error("price_validation", str(e))
        # 备用价格计算
        if side == 'buy':
            return current_price * 1.001, current_price * 0.98
        else:
            return current_price * 0.999, current_price * 1.02


def validate_stop_loss_for_order(side, stop_loss_price, current_price):
    """验证止损价格是否符合订单规则"""
    try:
        if side == 'buy':
            # 多头：止损价格必须低于当前价格
            if stop_loss_price >= current_price:
                logger.log_error("stop_loss_validation", 
                               f"多头止损价格无效: {stop_loss_price:.2f} >= {current_price:.2f}")
                # 自动调整为合理的止损价格
                adjusted_stop_loss = current_price * 0.98
                logger.log_warning(f"🔄 自动调整止损价格为: {adjusted_stop_loss:.2f}")
                return adjusted_stop_loss
            else:
                return stop_loss_price
        else:  # sell - 这是平仓方向，不是开仓方向
            # 空头持仓的止损是买入平仓，但止损价格应该高于当前价格（对空头不利）
            if stop_loss_price <= current_price:
                logger.log_error("stop_loss_validation", 
                               f"空头止损价格无效: {stop_loss_price:.2f} <= {current_price:.2f}")
                # 自动调整为合理的止损价格
                adjusted_stop_loss = current_price * 1.02
                logger.log_warning(f"🔄 自动调整止损价格为: {adjusted_stop_loss:.2f}")
                return adjusted_stop_loss
            else:
                return stop_loss_price
                
    except Exception as e:
        logger.log_error("stop_loss_validation", str(e))
        # 备用：使用默认止损
        if side == 'buy':
            return current_price * 0.98
        else:
            return current_price * 1.02

def calculate_limit_price(side, current_price, ticker):
    """计算限价单价格"""
    try:
        if side == 'buy':
            # 开多仓：使用卖一价或稍高价格确保成交
            ask_price = ticker['ask']
            limit_price = ask_price * 1.001  # 比卖一价高0.1%
            logger.log_info(f"📊 多头限价单价格: {limit_price:.2f} (卖一价: {ask_price:.2f})")
        else:  # sell
            # 开空仓：使用买一价或稍低价格确保成交
            bid_price = ticker['bid']
            limit_price = bid_price * 0.999  # 比买一价低0.1%
            logger.log_info(f"📊 空头限价单价格: {limit_price:.2f} (买一价: {bid_price:.2f})")
        
        return limit_price
        
    except Exception as e:
        logger.log_error("limit_price_calculation", str(e))
        # 备用计算：使用当前价格
        return current_price

def set_stop_loss_and_take_profit(symbol: str, position: dict, stop_loss_price: float, take_profit_price: float) -> bool:
    """设置止损和止盈 - 使用OKX策略委托接口"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        position_side = position['side']  # 'long' or 'short'
        position_size = position['size']
        
        logger.log_info(f"🎯 {symbol}: 设置止损止盈 - 持仓{position_side}, 止损{stop_loss_price:.2f}, 止盈{take_profit_price:.2f}")
        
        # 根据持仓方向确定委托方向
        if position_side == 'long':
            # 多头持仓：止损是卖出，止盈也是卖出
            side = 'sell'
            # 使用条件单分别设置止损和止盈
            sl_success = create_algo_order(
                symbol=symbol,
                side='sell',  # 止损平仓
                sz=position_size,
                trigger_price=stop_loss_price,
                order_type='conditional'
            )
            
            tp_success = create_algo_order(
                symbol=symbol,
                side='sell',  # 止盈平仓
                sz=position_size,
                trigger_price=take_profit_price,
                order_type='conditional'
            )
            
        else:  # short
            # 空头持仓：止损是买入，止盈也是买入
            side = 'buy'
            # 使用条件单分别设置止损和止盈
            sl_success = create_algo_order(
                symbol=symbol,
                side='buy',  # 止损平仓
                sz=position_size,
                trigger_price=stop_loss_price,
                order_type='conditional'
            )
            
            tp_success = create_algo_order(
                symbol=symbol,
                side='buy',  # 止盈平仓
                sz=position_size,
                trigger_price=take_profit_price,
                order_type='conditional'
            )
        
        if sl_success and tp_success:
            logger.log_info(f"✅ {symbol}: 止损止盈设置成功")
            return True
        else:
            logger.log_error(f"stop_loss_take_profit_failed_{symbol}", "止损止盈设置失败")
            return False
            
    except Exception as e:
        logger.log_error(f"set_stop_loss_take_profit_{symbol}", f"止损止盈设置异常: {str(e)}")
        return False

def check_existing_algo_orders(symbol: str, position: dict) -> dict:
    """检查现有的策略委托订单，返回详细的订单分析"""
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
        
        # 检查条件单（单向止盈止损）
        conditional_params = {
            'instType': 'SWAP',
            'algoOrdType': 'conditional'
        }
        
        conditional_response = exchange.private_get_trade_orders_algo_pending(conditional_params)
        
        if conditional_response['code'] == '0' and conditional_response['data']:
            inst_id = get_correct_inst_id(symbol)
            
            for order in conditional_response['data']:
                if order['instId'] == inst_id:
                    order_size = float(order.get('sz', 0))
                    
                    # 判断是止损单还是止盈单
                    if 'slTriggerPx' in order and order['slTriggerPx']:
                        algo_orders_analysis['has_stop_loss'] = True
                        algo_orders_analysis['stop_loss_orders'].append({
                            'algoId': order['algoId'],
                            'size': order_size,
                            'triggerPrice': float(order['slTriggerPx'])
                        })
                        algo_orders_analysis['total_covered_size'] += order_size
                    
                    if 'tpTriggerPx' in order and order['tpTriggerPx']:
                        algo_orders_analysis['has_take_profit'] = True
                        algo_orders_analysis['take_profit_orders'].append({
                            'algoId': order['algoId'],
                            'size': order_size,
                            'triggerPrice': float(order['tpTriggerPx'])
                        })
                        algo_orders_analysis['total_covered_size'] += order_size
        
        # 检查双向止盈止损单（OCO）
        oco_params = {
            'instType': 'SWAP',
            'algoOrdType': 'oco'
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
                        'takeProfitPrice': float(order.get('tpTriggerPx', 0))
                    })
                    algo_orders_analysis['has_stop_loss'] = True
                    algo_orders_analysis['has_take_profit'] = True
                    algo_orders_analysis['total_covered_size'] += order_size
        
        # 计算剩余需要设置的数量
        algo_orders_analysis['remaining_size'] = max(0, position['size'] - algo_orders_analysis['total_covered_size'])
        
        logger.log_info(f"📊 {symbol}: 策略委托分析 - 止损: {algo_orders_analysis['has_stop_loss']}, "
                      f"止盈: {algo_orders_analysis['has_take_profit']}, "
                      f"已覆盖: {algo_orders_analysis['total_covered_size']}/{position['size']}张, "
                      f"剩余: {algo_orders_analysis['remaining_size']}张")
        
        return algo_orders_analysis
            
    except Exception as e:
        logger.log_error(f"check_existing_algo_orders_{symbol}", f"检查策略委托订单失败: {str(e)}")
        return {
            'has_stop_loss': False,
            'has_take_profit': False,
            'stop_loss_orders': [],
            'take_profit_orders': [],
            'oco_orders': [],
            'total_covered_size': 0,
            'remaining_size': position['size']
        }

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
                logger.log_info(f"✅ {symbol}: 取消策略委托订单: {order['algoId']}")
                canceled_count += 1
            else:
                logger.log_warning(f"⚠️ {symbol}: 取消策略委托订单失败: {cancel_response}")
        
        if canceled_count > 0:
            logger.log_info(f"✅ {symbol}: 成功取消 {canceled_count} 个策略委托订单")
        
        return canceled_count
                    
    except Exception as e:
        logger.log_error(f"cancel_specific_algo_orders_{symbol}", str(e))
        return 0

def setup_missing_stop_loss_take_profit(symbol: str, position: dict, price_data: dict, orders_analysis: dict):
    """设置缺失的止损止盈订单"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        current_price = price_data['price']
        position_side = position['side']
        remaining_size = orders_analysis['remaining_size']
        
        if remaining_size <= 0:
            logger.log_info(f"✅ {symbol}: 止损止盈已完全覆盖持仓，无需设置")
            return True
        
        # 计算止损价格
        risk_config = config.get_risk_config()
        stop_loss_config = risk_config['stop_loss']
        
        if position_side == 'long':
            if stop_loss_config['kline_based_stop_loss']:
                stop_loss_price = calculate_kline_based_stop_loss(
                    'long', current_price, price_data, stop_loss_config['max_stop_loss_ratio']
                )
            else:
                stop_loss_price = current_price * (1 - stop_loss_config['min_stop_loss_ratio'])
                
            # 多头止盈计算
            take_profit_price = calculate_intelligent_take_profit(
                symbol, 'long', position['entry_price'], price_data, risk_reward_ratio=2.0
            )
            
        else:  # short
            if stop_loss_config['kline_based_stop_loss']:
                stop_loss_price = calculate_kline_based_stop_loss(
                    'short', current_price, price_data, stop_loss_config['max_stop_loss_ratio']
                )
            else:
                stop_loss_price = current_price * (1 + stop_loss_config['min_stop_loss_ratio'])
                
            # 空头止盈计算
            take_profit_price = calculate_intelligent_take_profit(
                symbol, 'short', position['entry_price'], price_data, risk_reward_ratio=2.0
            )
        
        # 根据缺失情况设置相应的订单
        success = True
        
        # 情况1：完全没有止损止盈，设置双向止盈止损
        if not orders_analysis['has_stop_loss'] and not orders_analysis['has_take_profit']:
            logger.log_info(f"🆕 {symbol}: 设置双向止盈止损 - 数量{remaining_size}张")
            
            if position_side == 'long':
                result = create_algo_order(
                    symbol=symbol,
                    side='sell',
                    sz=remaining_size,
                    trigger_price=stop_loss_price,
                    order_type='oco',
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price
                )
            else:  # short
                result = create_algo_order(
                    symbol=symbol,
                    side='buy',
                    sz=remaining_size,
                    trigger_price=stop_loss_price,
                    order_type='oco',
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price
                )
            
            if not result:
                success = False
                logger.log_error(f"oco_order_failed_{symbol}", "双向止盈止损设置失败")
        
        # 情况2：只有止损没有止盈，设置止盈单
        elif orders_analysis['has_stop_loss'] and not orders_analysis['has_take_profit']:
            logger.log_info(f"🎯 {symbol}: 设置止盈单 - 数量{remaining_size}张")
            
            if position_side == 'long':
                result = create_algo_order(
                    symbol=symbol,
                    side='sell',
                    sz=remaining_size,
                    trigger_price=take_profit_price,
                    order_type='conditional'
                )
            else:  # short
                result = create_algo_order(
                    symbol=symbol,
                    side='buy',
                    sz=remaining_size,
                    trigger_price=take_profit_price,
                    order_type='conditional'
                )
            
            if not result:
                success = False
                logger.log_error(f"take_profit_order_failed_{symbol}", "止盈单设置失败")
        
        # 情况3：只有止盈没有止损，设置止损单
        elif not orders_analysis['has_stop_loss'] and orders_analysis['has_take_profit']:
            logger.log_info(f"🛡️ {symbol}: 设置止损单 - 数量{remaining_size}张")
            
            if position_side == 'long':
                result = create_algo_order(
                    symbol=symbol,
                    side='sell',
                    sz=remaining_size,
                    trigger_price=stop_loss_price,
                    order_type='conditional'
                )
            else:  # short
                result = create_algo_order(
                    symbol=symbol,
                    side='buy',
                    sz=remaining_size,
                    trigger_price=stop_loss_price,
                    order_type='conditional'
                )
            
            if not result:
                success = False
                logger.log_error(f"stop_loss_order_failed_{symbol}", "止损单设置失败")
        
        # 情况4：部分覆盖，补充剩余数量的双向止盈止损
        elif orders_analysis['remaining_size'] > 0:
            logger.log_info(f"📦 {symbol}: 补充设置剩余仓位止盈止损 - 数量{remaining_size}张")
            
            if position_side == 'long':
                result = create_algo_order(
                    symbol=symbol,
                    side='sell',
                    sz=remaining_size,
                    trigger_price=stop_loss_price,
                    order_type='oco',
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price
                )
            else:  # short
                result = create_algo_order(
                    symbol=symbol,
                    side='buy',
                    sz=remaining_size,
                    trigger_price=stop_loss_price,
                    order_type='oco',
                    stop_loss_price=stop_loss_price,
                    take_profit_price=take_profit_price
                )
            
            if not result:
                success = False
                logger.log_error(f"supplementary_order_failed_{symbol}", "补充订单设置失败")
        
        if success:
            logger.log_info(f"✅ {symbol}: 缺失止盈止损设置完成")
            logger.log_info(f"📊 {symbol}: 止损价 {stop_loss_price:.2f}, 止盈价 {take_profit_price:.2f}")
        else:
            logger.log_error(f"missing_orders_setup_{symbol}", "缺失止盈止损设置失败")
            
        return success
            
    except Exception as e:
        logger.log_error(f"setup_missing_stop_loss_take_profit_{symbol}", f"设置缺失止盈止损失败: {str(e)}")
        return False

def check_and_set_stop_loss(symbol: str, position: dict, price_data: dict):
    """检查并设置止损和止盈订单 - 增强版本"""
    try:
        config = SYMBOL_CONFIGS[symbol]
        
        # 🆕 详细检查现有的策略委托订单
        orders_analysis = check_existing_algo_orders(symbol, position)
        
        # 情况分析并记录日志
        if not orders_analysis['has_stop_loss'] and not orders_analysis['has_take_profit']:
            logger.log_warning(f"⚠️ {symbol}: 未设置任何止盈止损订单")
        elif orders_analysis['has_stop_loss'] and not orders_analysis['has_take_profit']:
            logger.log_warning(f"⚠️ {symbol}: 已设置止损但未设置止盈")
        elif not orders_analysis['has_stop_loss'] and orders_analysis['has_take_profit']:
            logger.log_warning(f"⚠️ {symbol}: 已设置止盈但未设置止损")
        elif orders_analysis['remaining_size'] > 0:
            logger.log_warning(f"⚠️ {symbol}: 止盈止损未完全覆盖持仓 (剩余{orders_analysis['remaining_size']}张)")
        else:
            logger.log_info(f"✅ {symbol}: 止盈止损已完全设置")
            return True
        
        # 设置缺失的止盈止损
        success = setup_missing_stop_loss_take_profit(symbol, position, price_data, orders_analysis)
        
        return success
            
    except Exception as e:
        logger.log_error(f"stop_loss_check_{symbol}", f"止损止盈检查设置失败: {str(e)}")
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
                logger.log_warning(f"⚠️ {symbol}: 取消不合理的多头止损单 - 触发价{trigger_price:.2f}过于接近当前价{current_price:.2f}")
                canceled_count += cancel_specific_algo_orders(symbol, [stop_loss_order], 'conditional')
            
            # 空头：止损价格不合理（低于当前价格或过于接近）
            elif position_side == 'short' and trigger_price <= current_price * 1.01:
                logger.log_warning(f"⚠️ {symbol}: 取消不合理的空头止损单 - 触发价{trigger_price:.2f}过于接近当前价{current_price:.2f}")
                canceled_count += cancel_specific_algo_orders(symbol, [stop_loss_order], 'conditional')
        
        # 检查并取消不合理的止盈单
        for take_profit_order in orders_analysis['take_profit_orders']:
            trigger_price = take_profit_order['triggerPrice']
            
            # 多头：止盈价格不合理（低于当前价格）
            if position_side == 'long' and trigger_price <= current_price:
                logger.log_warning(f"⚠️ {symbol}: 取消不合理的多头止盈单 - 触发价{trigger_price:.2f}低于当前价{current_price:.2f}")
                canceled_count += cancel_specific_algo_orders(symbol, [take_profit_order], 'conditional')
            
            # 空头：止盈价格不合理（高于当前价格）
            elif position_side == 'short' and trigger_price >= current_price:
                logger.log_warning(f"⚠️ {symbol}: 取消不合理的空头止盈单 - 触发价{trigger_price:.2f}高于当前价{current_price:.2f}")
                canceled_count += cancel_specific_algo_orders(symbol, [take_profit_order], 'conditional')
        
        # 如果有取消的订单，重新设置止盈止损
        if canceled_count > 0:
            logger.log_info(f"🔄 {symbol}: 重新设置被取消的止盈止损订单")
            time.sleep(1)  # 等待取消操作完成
            return check_and_set_stop_loss(symbol, position, price_data)
        
        return True
            
    except Exception as e:
        logger.log_error(f"optimize_existing_orders_{symbol}", f"优化现有订单失败: {str(e)}")
        return False


def execute_intelligent_trade(symbol: str, signal_data: dict, price_data: dict):
    """执行智能交易 - 改进版，使用动态盈亏比"""
    global position
    config = SYMBOL_CONFIGS[symbol]
    
    # 对于HOLD信号，直接返回
    if signal_data['signal'] == 'HOLD':
        logger.log_info(f"⏸️ {symbol}: 保持观望，不执行交易")
        return

    # 🆕 步骤1: 计算动态盈亏比阈值
    dynamic_min_rr = calculate_dynamic_risk_reward_threshold(symbol, price_data)
    logger.log_info(f"🎯 {symbol}: 使用动态盈亏比阈值: {dynamic_min_rr:.2f}")

    current_price = price_data['price']
    side = 'long' if signal_data['signal'] == 'BUY' else 'short'

    # 🆕 步骤2: 计算自适应止损
    stop_loss_price = calculate_adaptive_stop_loss(symbol, side, current_price, price_data)

    # 🆕 步骤3: 计算现实止盈
    tp_result = calculate_realistic_take_profit(symbol, side, current_price, stop_loss_price, 
                                              price_data, dynamic_min_rr)
    
    take_profit_price = tp_result['take_profit']
    actual_rr = tp_result['actual_risk_reward']

    # 🆕 步骤4: 放宽接受条件
    if not tp_result['is_acceptable']:
        # 即使不满足完整阈值，如果盈亏比合理也可以考虑
        if actual_rr >= 0.8:  # 最低可接受盈亏比
            logger.log_warning(f"⚠️ {symbol}: 盈亏比{actual_rr:.2f}略低于阈值{dynamic_min_rr:.2f}，但仍可接受")
        else:
            logger.log_warning(f"🚫 {symbol}: 盈亏比{actual_rr:.2f}过低，放弃开仓")
            return

    # 计算仓位
    position_size = calculate_enhanced_position(symbol, signal_data, price_data, get_current_position(symbol))

    # 记录交易分析
    trade_analysis = f"""
    🎯 {symbol} 改进版交易分析:
    ├── 信号: {signal_data['signal']}
    ├── 入场价格: {current_price:.2f}
    ├── 止损位置: {stop_loss_price:.2f}
    ├── 止盈位置: {take_profit_price:.2f}
    ├── 实际盈亏比: {actual_rr:.2f}:1
    ├── 目标阈值: {dynamic_min_rr:.2f}:1
    ├── 仓位大小: {position_size:.2f}张
    └── 状态: {'✅ 满足开仓条件' if tp_result['is_acceptable'] else '⚠️ 条件放宽'}
    """
    logger.log_info(trade_analysis)

    # 更新信号数据
    signal_data['stop_loss'] = stop_loss_price
    signal_data['take_profit'] = take_profit_price

    # 🆕 安全地记录日志
    try:
        logger.log_info(f"🎯 {symbol}: 交易执行 - {signal_data['signal']} | 仓位: {position_size:.2f}张 | 止损: {stop_loss_price:.2f} | 止盈: {take_profit_price:.2f}")
    except Exception as log_error:
        logger.log_info(f"🎯 {symbol}: 交易执行 - {signal_data['signal']} | 仓位: {position_size:.2f}张")
        logger.log_warning(f"⚠️ 日志格式化失败: {str(log_error)}")

    if config.test_mode:
        logger.log_info("测试模式 - 仅模拟交易")
        return

    # 🆕 只有通过所有验证才执行实际交易
    try:
        # 获取当前市场数据
        ticker = exchange.fetch_ticker(config.symbol)
        current_price = ticker['last']
        bid_price = ticker['bid']
        ask_price = ticker['ask']
        
        logger.log_info(f"📊 {symbol}: 执行开仓 - 价格{current_price:.2f}, 买一{bid_price:.2f}, 卖一{ask_price:.2f}")
        
        current_position = get_current_position(symbol)
        # 执行交易逻辑（保持原有的交易执行代码）
        if signal_data['signal'] == 'BUY':
            # 检查是否有现有空头持仓，先平仓
            if current_position and current_position['side'] == 'short':
                logger.log_info(f"🔄 {symbol}: 平空仓开多仓 - 平{current_position['size']}张，开{position_size}张")
                
                close_params = {
                    'reduceOnly': True,
                }
                exchange.create_market_order(
                    config.symbol,
                    'buy',
                    current_position['size'],
                    params=close_params
                )
                time.sleep(1)

            # 开多仓（同步设置止损止盈）
            inst_id = get_correct_inst_id(symbol)
            open_params = {
                'instId': inst_id,
                'tdMode': getattr(config, 'margin_mode', 'isolated'),
                'side': 'buy',
                'ordType': 'market',
                'sz': str(round(position_size, 2)),
                'slTriggerPx': str(round(stop_loss_price, 2)),
                'slOrdPx': '-1',
                'tpTriggerPx': str(round(take_profit_price, 2)),
                'tpOrdPx': '-1',
                'slTriggerPxType': 'last',
                'tpTriggerPxType': 'last',
            }

            log_order_params("市价Buy单带止损止盈", open_params, "execute_intelligent_trade")
            logger.log_info(f"✅ {symbol}: 市价开多仓提交 - {position_size}张")

            response = exchange.private_post_trade_order(open_params)
            log_api_response(response, "execute_intelligent_trade")

        elif signal_data['signal'] == 'SELL':
            # 检查是否有现有多头持仓，先平仓
            if current_position and current_position['side'] == 'long':
                logger.log_info(f"🔄 {symbol}: 平多仓开空仓 - 平{current_position['size']}张，开{position_size}张")
                
                close_params = {
                    'reduceOnly': True,
                }
                exchange.create_market_order(
                    config.symbol,
                    'sell',
                    current_position['size'],
                    params=close_params
                )
                time.sleep(1)

            # 开空仓（同步设置止损止盈）
            inst_id = get_correct_inst_id(symbol)
            open_params = {
                'instId': inst_id,
                'tdMode': getattr(config, 'margin_mode', 'isolated'),
                'side': 'sell',
                'ordType': 'market',
                'sz': str(round(position_size, 2)),
                'slTriggerPx': str(round(stop_loss_price, 2)),
                'slOrdPx': '-1',
                'tpTriggerPx': str(round(take_profit_price, 2)),
                'tpOrdPx': '-1',
                'slTriggerPxType': 'last',
                'tpTriggerPxType': 'last',
            }
            log_order_params("市价Sell单带止损止盈", open_params, "execute_intelligent_trade")
            logger.log_info(f"✅ {symbol}: 市价开空仓提交 - {position_size}张")

            response = exchange.private_post_trade_order(open_params)
            log_api_response(response, "execute_intelligent_trade")

        # 处理订单响应
        if response.get('code') == '0':
            order_id = response['data'][0]['ordId']
            logger.log_info(f"✅ {symbol}: 开仓订单创建成功 (带止损止盈): {order_id}")
            
            # 🆕 记录成功的交易
            logger.log_info(f"💰 {symbol}: 交易执行成功! 盈亏比: {actual_rr:.2f}:1")
        else:
            logger.log_error(f"❌ {symbol}: 开仓订单创建失败: {response.get('msg')}")
            return

    except Exception as e:
        logger.log_error(f"trade_execution_{symbol}", str(e))
        logger.log_warning(f"⚠️ {symbol}: 交易执行失败，但盈亏比分析仍然有效")

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


def trading_bot(symbol: str):
    """
    主要交易逻辑循环 - 现在接受 symbol 参数
    """
    global CURRENT_SYMBOL
    CURRENT_SYMBOL = symbol  # 设置当前品种，以便日志记录器使用
    
    # 从全局字典中获取该品种的配置
    config = SYMBOL_CONFIGS[symbol]

    logger.log_info(f"\n=====================================")
    logger.log_info(f"🎯 运行交易品种: {symbol}")
    logger.log_info(f"配置摘要: {config.get_config_summary()}")  # 打印品种配置摘要
    logger.log_info(f"=====================================")

    try:
        # 1. 获取市场和价格数据 (使用 symbol)
        df, price_data = fetch_ohlcv(symbol)

        if df is None or price_data is None:
            logger.log_warning(f"❌ Could not fetch data for {symbol}.")
            return
            
        # 2. 获取当前持仓 (使用 symbol)
        current_position = get_current_position(symbol)

        # 3. 使用DeepSeek分析市场
        signal_data = analyze_with_deepseek(symbol, price_data)
        
        if not signal_data:
            logger.log_warning(f"❌ Could not get signal for {symbol}.")
            return

        # 4. 过滤信号
        filtered_signal = filter_signal(signal_data, price_data)
        
        # 5. 添加到历史记录
        add_to_signal_history(symbol, filtered_signal)
        add_to_price_history(symbol, price_data)

        # 6. 记录信号
        logger.log_info(f"📊 {symbol} 交易信号: {filtered_signal['signal']} | 信心: {filtered_signal['confidence']}")
        logger.log_info(f"📝 原因: {filtered_signal['reason']}")

        # 7. 执行智能交易
        execute_intelligent_trade(symbol, filtered_signal, price_data)
        
    except Exception as e:
        logger.log_error(f"trading_bot_{symbol}", str(e))

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
    logger.log_info(f"🔍 系统健康检查: {status_emoji} | {details}")
    
    return overall_status

def close_position_due_to_trend_reversal(symbol: str, position: dict, price_data: dict, reason: str):
    """因趋势反转而平仓"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        order_tag = create_order_tag()
        position_size = position['size']
        
        logger.log_warning(f"🔄 执行趋势反转平仓: {reason}")
        
        if position['side'] == 'long':
            # 平多仓
            close_params = {
                'reduceOnly': True,
                'tag': order_tag
            }
            log_order_params("趋势反转平仓", close_params, "close_position_due_to_trend_reversal")
            log_perpetual_order_details(symbol,'sell', position_size, 'market', reduce_only=True)
            
            if not config.test_mode:
                exchange.create_market_order(
                    config.symbol,
                    'sell',
                    position_size,
                    params=close_params
                )
        else:  # short
            # 平空仓
            close_params = {
                'reduceOnly': True,
                'tag': order_tag
            }
            log_order_params("趋势反转平仓", close_params, "close_position_due_to_trend_reversal")
            log_perpetual_order_details(symbol,'buy', position_size, 'market', reduce_only=True)
            
            if not config.test_mode:
                exchange.create_market_order(
                    config.symbol,
                    'buy',
                    position_size,
                    params=close_params
                )
        
        logger.log_info("✅ 趋势反转平仓执行完成")
        return False  # 表示持仓已平
        
    except Exception as e:
        logger.log_error("trend_reversal_close", f"趋势反转平仓失败: {str(e)}")
        return True  # 平仓失败，保持持仓

def check_existing_positions_on_startup():
    """启动时检查所有交易品种的现有持仓"""
    logger.log_info("🔍 启动时持仓检查开始...")
    
    for symbol, config in SYMBOL_CONFIGS.items():
        try:
            logger.log_info(f"📊 检查 {symbol} 的持仓状态...")
            
            # 获取当前持仓
            current_position = get_current_position(symbol)
            
            if current_position is None:
                logger.log_info(f"✅ {symbol}: 无持仓")
                continue
                
            logger.log_warning(f"⚠️ {symbol}: 发现现有持仓 - {current_position['side']} {current_position['size']}张")
            
            # 获取市场数据进行分析
            df, price_data = fetch_ohlcv(symbol)
            
            # 🆕 修复：使用明确的 None 检查而不是真值判断
            if df is None or price_data is None:
                logger.log_warning(f"❌ {symbol}: 无法获取市场数据，跳过分析")
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
            logger.log_error(f"startup_check_{symbol}", f"启动检查失败: {str(e)}")
    
    logger.log_info("✅ 启动时持仓检查完成")

def analyze_should_hold_position(symbol: str, position: dict, price_data: dict) -> bool:
    """分析是否应该继续持有现有持仓"""
    try:
        config = SYMBOL_CONFIGS[symbol]
        
        # 获取技术信号
        signal_data = analyze_with_deepseek(symbol, price_data)
        
        # 🆕 修复：使用明确的 None 检查而不是真值判断
        if signal_data is None:
            logger.log_warning(f"⚠️ {symbol}: 无法获取分析信号，保守处理：继续持有")
            return True
        
        position_side = position['side']  # 'long' or 'short'
        signal_side = signal_data['signal']  # 'BUY', 'SELL', 'HOLD'
        
        logger.log_info(f"📊 {symbol} 持仓分析: 持仓{position_side}, 信号{signal_side}, 信心{signal_data['confidence']}")
        
        # 判断逻辑
        if signal_side == 'HOLD':
            logger.log_info(f"✅ {symbol}: 信号建议持有，继续持仓")
            return True
            
        elif (position_side == 'long' and signal_side == 'BUY') or \
             (position_side == 'short' and signal_side == 'SELL'):
            logger.log_info(f"✅ {symbol}: 信号与持仓方向一致，继续持仓")
            return True
            
        elif (position_side == 'long' and signal_side == 'SELL') or \
             (position_side == 'short' and signal_side == 'BUY'):
            # 趋势反转，需要进一步分析强度
            reversal_strength = analyze_trend_reversal_strength(position_side, signal_side, price_data, signal_data)
            
            if reversal_strength in ['STRONG', 'MEDIUM']:
                logger.log_warning(f"🔄 {symbol}: 检测到{reversal_strength}强度趋势反转，建议平仓")
                return False
            else:
                logger.log_info(f"✅ {symbol}: 弱强度反转信号，继续持有观察")
                return True
        else:
            logger.log_warning(f"⚠️ {symbol}: 未知信号组合，保守处理：继续持有")
            return True
            
    except Exception as e:
        logger.log_error(f"hold_analysis_{symbol}", f"持仓分析失败: {str(e)}")
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

def close_position_with_reason(symbol: str, position: dict, reason: str):
    """根据原因平仓"""
    try:
        config = SYMBOL_CONFIGS[symbol]
        order_tag = create_order_tag()
        
        logger.log_warning(f"🔄 {symbol}: 执行平仓 - {reason}")
        
        if position['side'] == 'long':
            # 平多仓
            close_params = {
                'reduceOnly': True,
                'tag': order_tag
            }
            log_order_params("启动平仓", close_params, "close_position_with_reason")
            log_perpetual_order_details(symbol,'sell', position['size'], 'market', reduce_only=True)
            
            if not config.test_mode:
                exchange.create_market_order(
                    config.symbol,
                    'sell',
                    position['size'],
                    params=close_params
                )
        else:  # short
            # 平空仓
            close_params = {
                'reduceOnly': True,
                'tag': order_tag
            }
            log_order_params("启动平仓", close_params, "close_position_with_reason")
            log_perpetual_order_details(symbol,'buy', position['size'], 'market', reduce_only=True)
            
            if not config.test_mode:
                exchange.create_market_order(
                    config.symbol,
                    'buy',
                    position['size'],
                    params=close_params
                )
        
        logger.log_info(f"✅ {symbol}: 平仓执行完成")
        return True
        
    except Exception as e:
        logger.log_error(f"close_position_{symbol}", f"平仓失败: {str(e)}")
        return False

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
        'buy_signals': f"{buy_count}/{total}",
        'sell_signals': f"{sell_count}/{total}", 
        'hold_signals': f"{hold_count}/{total}",
        'total_signals': total
    }
    logger.log_performance(performance_metrics)

def main():
    """
    主程序入口 - 支持多交易品种
    """
    global SYMBOL_CONFIGS
    
    # TEST : 列出所有可用的私有API方法
    # exchge = ccxt.okx()
    # print("所有可用的私有API方法:")
    # private_methods = [method for method in dir(exchge) if method.startswith('private')]
    # for method in private_methods:
    #     print(method)

    # 1. 动态加载交易品种列表
    symbols_to_trade_str = os.getenv('TRADING_SYMBOLS', '')
    if symbols_to_trade_str:
        symbols_to_trade = [s.strip() for s in symbols_to_trade_str.split(',') if s.strip()]
    else:
        symbols_to_trade = list(MULTI_SYMBOL_CONFIGS.keys())
        
    if not symbols_to_trade:
        logger.log_error("config_error", "未找到任何交易品种配置")
        return

    # 2. 初始化所有品种的配置
    for symbol in symbols_to_trade:
        try:
            if symbol not in MULTI_SYMBOL_CONFIGS:
                logger.log_warning(f"⚠️ 跳过未配置的品种: {symbol}")
                continue
                
            symbol_config = MULTI_SYMBOL_CONFIGS[symbol]
            config = TradingConfig(symbol=symbol, config_data=symbol_config)
            
            # 验证配置
            is_valid, errors, warnings = config.validate_config(symbol)
            if not is_valid:
                logger.log_error(f"config_validation_{symbol}", f"配置验证失败: {errors}")
                continue
                
            SYMBOL_CONFIGS[symbol] = config
            logger.log_info(f"✅ 加载配置: {symbol} | 杠杆 {config.leverage}x | 基础金额 {config.position_management['base_usdt_amount']} USDT")
            
        except Exception as e:
            logger.log_error(f"config_loading_{symbol}", str(e))
            
    if not SYMBOL_CONFIGS:
        logger.log_error("program_exit", "所有交易品种配置加载失败")
        return

    # 类型安全检查
    if not SYMBOL_CONFIGS or not isinstance(SYMBOL_CONFIGS, dict):
        logger.log_error("program_exit", "交易品种配置加载失败或类型错误")
        return
        
    # 确保 first_config 是 TradingConfig 对象
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

    # 3. 设置交易所
    for symbol in list(SYMBOL_CONFIGS.keys()):
        if not setup_exchange(symbol):
            logger.log_error("exchange_setup", f"交易所设置失败: {symbol}")
            del SYMBOL_CONFIGS[symbol]

    symbols_to_trade = list(SYMBOL_CONFIGS.keys())
    if not symbols_to_trade:
        logger.log_error("program_exit", "所有交易品种初始化失败")
        return
        
    # 🆕 启动时持仓检查
    check_existing_positions_on_startup()      

    logger.log_info(f"🚀 主循环启动，交易品种: {', '.join(symbols_to_trade)}")
    
    # 原始 TRADE_CONFIG 的引用需要替换为 SYMBOL_CONFIGS 中任一个（例如第一个）
    # 以获取通用的 max_consecutive_errors 等参数。
    first_config = list(SYMBOL_CONFIGS.values())[0]

    # Initialize control variables
    consecutive_errors = 0
    last_health_check = 0
    health_check_interval = 3600  # 1 hour
    last_config_check = 0
    config_check_interval = first_config.config_check_interval # 使用任一配置的检查间隔
    last_perf_log = 0
    perf_log_interval = first_config.perf_log_interval

    while True:
        try:
            current_time = time.time()
            
            # Health check - 修复这里
            if current_time - last_health_check >= health_check_interval:
                logger.log_info("🔍 Running scheduled health check...")
                
                # 对每个交易品种执行健康检查
                health_ok = True
                for symbol in SYMBOL_CONFIGS.keys():
                    if not health_check(symbol):
                        health_ok = False
                        break
                
                if not health_ok:
                    consecutive_errors += 1
                    # 安全地获取配置限制
                    try:
                        max_errors = first_config.max_consecutive_errors
                    except (AttributeError, TypeError):
                        max_errors = 5  # 默认值
                    
                    if consecutive_errors >= max_errors:
                        logger.log_warning("🚨 Too many consecutive errors, exiting.")
                        break
                else:
                    consecutive_errors = 0
                last_health_check = current_time
        
        # Configuration reload check - every 5 minutes
        if current_time - last_config_check >= config_check_interval:
            last_config_check = current_time

        # Run trading bot for all symbols
        for symbol in symbols_to_trade:
            trading_bot(symbol)
        
        # Log performance for each symbol
        for symbol in symbols_to_trade:
            log_performance_metrics(symbol)

        # Wait for next cycle
        time.sleep(60)
        
    except KeyboardInterrupt:
        logger.log_warning("\n🛑 User interrupted the program.")
        break

    except Exception as e:
        logger.log_error("main_loop", f"Error: {str(e)}")
        consecutive_errors += 1
    
        # 安全地获取配置限制
        try:
            max_errors = first_config.max_consecutive_errors
        except (AttributeError, TypeError):
            max_errors = 5  # 默认值
            
        if consecutive_errors >= max_errors:
            logger.log_warning("🚨 Too many consecutive errors, exiting.")
        break

        time.sleep(60)

if __name__ == "__main__":
    main()
