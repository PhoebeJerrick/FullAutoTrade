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

def log_perpetual_order_details(side, amount, order_type, reduce_only=False, stop_loss=False, take_profit=False, stop_loss_price=None):
    """简化版订单详情日志"""
    try:
        action_types = []
        if reduce_only:
            action_types.append("只减仓")
        if stop_loss:
            action_types.append("止损")
        if take_profit:
            action_types.append("止盈")
            
        action_str = " | ".join(action_types) if action_types else "普通"
        
        log_msg = f"🎯 永续合约订单: {side} {amount}张 | {order_type} | {action_str}"
        if stop_loss_price:
            stop_loss_ratio = abs(stop_loss_price - get_current_price()) / get_current_price() * 100
            log_msg += f" | 止损价:{stop_loss_price:.2f}({stop_loss_ratio:.2f}%)"
            
        logger.log_info(log_msg)
            
    except Exception as e:
        logger.log_error("log_perpetual_order_details", f"记录订单详情失败: {str(e)}")

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

def check_current_margin_mode(symbol: str):
    """检查当前仓位模式 - 简化版本"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        positions = exchange.fetch_positions([config.symbol])
        for pos in positions:
            if pos['symbol'] == config.symbol:
                mode = pos.get('mgnMode', 'unknown')
                if mode != 'unknown':
                    return mode
        
        # 如果没有持仓，返回默认值
        return getattr(config, 'margin_mode', 'isolated')
        
    except Exception as e:
        logger.log_error(f"margin_mode_check_{symbol}", str(e))
        return getattr(config, 'margin_mode', 'isolated')


def setup_exchange(symbol: str): # 新增 symbol 参数
    """
    智能交易所设置：设置杠杆和保证金模式，并获取合约规格
    """
    # 动态加载当前 symbol 的配置
    config = SYMBOL_CONFIGS[symbol]
    
    try:
        # 1. 设置保证金模式 (全仓/逐仓)
        logger.log_info(f"⚙️ Setting margin mode for {symbol} to {config.margin_mode}...")
        try:
            exchange.set_margin_mode(config.margin_mode, symbol)
            logger.log_warning(f"✅ Margin mode {config.margin_mode} set for {symbol}")
        except Exception as e:
            logger.log_warning(f"⚠️ Margin mode setting failed for {symbol}: {e}")
            
        # 2. 设置杠杆
        leverage = getattr(config, 'leverage', 50)
        logger.log_info(f"⚙️ Setting leverage for {symbol} to {leverage}x...")
        try:
            exchange.set_leverage(leverage, symbol) # 使用 symbol 和 config.leverage
            logger.log_warning(f"✅ Leverage {leverage}x set for {symbol}")
        except Exception as e:
            logger.log_warning(f"⚠️ Leverage setting failed for {symbol}: {e}")
        
        # 3. 获取合约规格
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
        
        return True

    except Exception as e:
        logger.log_error(f"exchange_setup_{symbol}", str(e))
        return False

# Global variables to store historical data
price_history = []
signal_history = []
position = None


def calculate_intelligent_position(symbol: str, signal_data: dict, price_data: dict, current_position: Optional[dict]) -> float:
    """Calculate intelligent position size - fixed version"""
    config = SYMBOL_CONFIGS[symbol]
    posMngmt = config.position_management

    # 🆕 New: If intelligent position is disabled, use fixed position
    if not posMngmt.get('enable_intelligent_position', True):
        fixed_contracts = 0.1
        logger.log_info(f"🔧 智能仓位已禁用，使用固定仓位: {fixed_contracts}张")
        return fixed_contracts

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


def get_sentiment_indicators():
    """Get sentiment indicators - simplified version"""
    try:
        API_URL = TRADE_CONFIG.sentiment_api_url
        API_KEY = TRADE_CONFIG.sentiment_api_key

        # Get recent 4-hour data
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=4)

        request_body = {
            "apiKey": API_KEY,
            "endpoints": ["CO-A-02-01", "CO-A-02-02"],  # Keep only core indicators
            "startTime": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "timeType": "15m",
            "token": ["BTC"]
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

                        logger.log_warning(f"✅ Using sentiment data time: {period['startTime']} (Delay: {data_delay} minutes)")

                        return {
                            'positive_ratio': positive,
                            'negative_ratio': negative,
                            'net_sentiment': net_sentiment,
                            'data_time': period['startTime'],
                            'data_delay_minutes': data_delay
                        }

                logger.log_warning("❌ All time period data is empty")
                return None

        return None
    except Exception as e:
        logger.log_error("sentiment_data", str(e))
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

def create_algo_order(symbol: str, side: str, sz: Union[float, str], trigger_price: Union[float, str], algo_order_type='conditional'):
    """创建算法订单 - 永续合约条件单（不取消现有订单）"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        # 确保使用正确的合约ID
        inst_id = get_correct_inst_id(symbol)
        
        # 确保参数类型正确
        if isinstance(trigger_price, str):
            trigger_price = float(trigger_price)
        if isinstance(sz, (int, float)):
            sz = str(round(sz, 2))
            
        margin_mode = getattr(config, 'margin_mode', 'isolated')
        
        # 构建永续合约条件单参数
        params = {
            'instId': inst_id,
            'tdMode': margin_mode,
            'algoOrdType': algo_order_type,
            'side': side.upper(),
            'sz': sz,
            'tpTriggerPx': str(round(trigger_price, 1)),
            'tpOrdPx': '-1',
            'posSide': 'net',  # 单向持仓
            'ordType': 'market'  # 触发后使用市价单
        }
        
        # 记录完整的订单参数
        log_order_params("永续合约条件单", params, "create_algo_order")
        log_perpetual_order_details(side, sz, 'conditional_stop', stop_loss=True, stop_loss_price=trigger_price)
        
        logger.log_info(f"📊 创建永续合约条件单: {side} {sz} @ {trigger_price}")
        
        # 调用OKX算法订单API
        response = exchange.privatePostTradeOrderAlgo(params)
        
        # 记录API响应
        log_api_response(response, "create_algo_order")
        
        if response['code'] == '0':
            algo_id = response['data'][0]['algoId']
            logger.log_info(f"✅ 永续合约条件单创建成功: {algo_id}")
            return True
        else:
            logger.log_error("algo_order_failed", f"永续合约条件单创建失败: {response}")
            return False
            
    except Exception as e:
        logger.log_error("create_algo_order", f"创建永续合约条件单异常: {str(e)}")
        return False

def cancel_existing_algo_orders(symbol: str):
    """取消指定品种的现有算法订单"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        params = {
            'instType': 'SWAP',
            'algoOrdType': 'conditional'
        }
        
        response = exchange.privateGetTradeOrdersAlgoPending(params)
        
        if response['code'] == '0' and response['data']:
            inst_id = get_correct_inst_id(symbol)
            
            for order in response['data']:
                if order['instId'] == inst_id:
                    # 取消条件单
                    cancel_params = {
                        'algoId': order['algoId'],
                        'instId': order['instId'],
                        'algoOrdType': 'conditional'
                    }
                    cancel_response = exchange.privatePostTradeCancelAlgoOrder(cancel_params)
                    if cancel_response['code'] == '0':
                        logger.log_info(f"✅ {symbol}: 取消现有条件单: {order['algoId']}")
                    else:
                        logger.log_warning(f"⚠️ {symbol}: 取消条件单失败: {cancel_response}")
        else:
            logger.log_info(f"✅ {symbol}: 没有找到待取消的条件单")
                    
    except Exception as e:
        logger.log_error(f"cancel_algo_orders_{symbol}", str(e))

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
        cancel_existing_algo_orders()
        
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
        
    def check_profit_taking(self, current_position, price_data):
        """检查是否需要执行多级止盈"""
        if not current_position:
            return None
            
        position_key = f"{current_position['side']}_{current_position['entry_price']}"
        risk_config = TRADE_CONFIG.get_risk_config()
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
        
    def mark_level_executed(self, current_position, level):
        """标记止盈级别已执行"""
        position_key = f"{current_position['side']}_{current_position['entry_price']}"
        level_key = f"{position_key}_level_{level}"
        self.position_levels[level_key] = True

# 创建全局持仓管理器实例
position_manager = PositionManager()


def fetch_ohlcv_with_retry(symbol: str,max_retries=None):
    if max_retries is None:
        max_retries = TradingConfig.max_retries

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

# Optimization: Add a unified error handling and retry decorator
def retry_on_failure(max_retries=None, delay=None, exceptions=(Exception,)):
    # """Unified error handling and retry decorator"""
    if max_retries is None:
        max_retries = TRADE_CONFIG.max_retries
    if delay is None:
        delay = TRADE_CONFIG.retry_delay
        
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


def add_to_signal_history(signal_data):
    global signal_history
    
    signal_history.append(signal_data)  # 改为追加信号数据
    
    # Limit the history to 100 records
    max_history = 100
    if len(signal_history) > max_history:
        # Keep the latest 80% and remove the oldest 20%
        keep_count = int(max_history * 0.8)
        signal_history = signal_history[-keep_count:]

def add_to_price_history(price_data):
    global price_history
    
    price_history.append(price_data)
    
    # Limit the history to 200 records
    max_history = 200
    if len(price_history) > max_history:
        keep_count = int(max_history * 0.8)
        price_history = price_history[-keep_count:]

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
            cancel_existing_algo_orders()
            
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
        
        response = exchange.privateGetTradeOrdersAlgoPending(params)
        
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

def set_initial_take_profit(symbol: str, signal: str, position_size: float, take_profit_price: float, current_price: float) -> bool:
    """设置初始止盈订单"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        # 确定止盈方向
        if signal == 'BUY':
            # 多头持仓，止盈是卖出
            side = 'sell'
        else:  # SELL
            # 空头持仓，止盈是买入平仓
            side = 'buy'
        
        # 确保参数正确
        if isinstance(position_size, (int, float)):
            position_size = str(round(position_size, 2))
        if isinstance(take_profit_price, str):
            take_profit_price = float(take_profit_price)
        
        # 验证止盈价格
        if signal == 'BUY':
            if take_profit_price <= current_price:
                logger.log_warning(f"⚠️ {symbol}: 多头止盈价格无效，自动调整")
                take_profit_price = current_price * 1.03  # 默认3%止盈
        else:  # SELL
            if take_profit_price >= current_price:
                logger.log_warning(f"⚠️ {symbol}: 空头止盈价格无效，自动调整")
                take_profit_price = current_price * 0.97  # 默认3%止盈
        
        logger.log_info(f"🎯 {symbol}: 设置初始止盈单 - {side} {position_size}张, 触发价{take_profit_price:.1f}")
        
        # 创建止盈条件单
        result = create_take_profit_algo_order(
            symbol,
            side=side,
            sz=position_size,
            trigger_price=take_profit_price
        )
        
        if result:
            take_profit_ratio = abs(take_profit_price - current_price) / current_price * 100
            logger.log_info(f"✅ {symbol}: 初始止盈单设置成功 - {take_profit_price:.1f} (距离{take_profit_ratio:.2f}%)")
            return True
        else:
            logger.log_error(f"take_profit_failed_{symbol}", "初始止盈单设置失败")
            return False
            
    except Exception as e:
        logger.log_error(f"initial_take_profit_{symbol}", f"止盈设置异常: {str(e)}")
        return False

def create_take_profit_algo_order(symbol: str, side: str, sz: Union[float, str], trigger_price: Union[float, str]) -> bool:
    """创建止盈算法订单"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        inst_id = get_correct_inst_id(symbol)
        
        if isinstance(trigger_price, str):
            trigger_price = float(trigger_price)
        if isinstance(sz, (int, float)):
            sz = str(round(sz, 2))
            
        margin_mode = getattr(config, 'margin_mode', 'isolated')
        
        # 构建永续合约止盈条件单参数
        params = {
            'instId': inst_id,
            'tdMode': margin_mode,
            'algoOrdType': 'conditional',
            'side': side.upper(),
            'sz': sz,
            'tpTriggerPx': str(round(trigger_price, 1)),
            'tpOrdPx': '-1',  # 触发后市价单
            'posSide': 'net',
            'ordType': 'market'
        }
        
        log_order_params("永续合约止盈单", params, "create_take_profit_algo_order")
        
        logger.log_info(f"📈 {symbol}: 创建止盈条件单 - {side} {sz} @ {trigger_price}")
        
        response = exchange.privatePostTradeOrderAlgo(params)
        log_api_response(response, "create_take_profit_algo_order")
        
        if response['code'] == '0':
            algo_id = response['data'][0]['algoId']
            logger.log_info(f"✅ {symbol}: 止盈条件单创建成功: {algo_id}")
            return True
        else:
            logger.log_error(f"take_profit_order_failed_{symbol}", f"止盈条件单创建失败: {response}")
            return False
            
    except Exception as e:
        logger.log_error(f"create_take_profit_algo_order_{symbol}", f"创建止盈条件单异常: {str(e)}")
        return False

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
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.log_error("json_parsing", f"Failed to parse: {json_str}")
            logger.log_error(f"Error details: {e}")
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

@retry_on_failure(max_retries=TradingConfig.max_retries, delay=TradingConfig.retry_delay)
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
        if signal_history:
            last_signal = signal_history[-1]
            signal_text = f"\n【Previous Trading Signal】\nSignal: {last_signal.get('signal', 'N/A')}\nConfidence: {last_signal.get('confidence', 'N/A')}"

        # Get sentiment data
        sentiment_data = get_sentiment_indicators()
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

        【Key Technical Levels for BTC/USDT】
        - Strong Resistance: When price approaches recent high + Bollinger Band upper
        - Strong Support: When price approaches recent low + Bollinger Band lower
        - Breakout Confirmation: Requires closing price break + volume > 20-period average
        - False Breakout: Price breaks but fails to sustain, immediately reverses
        """

        prompt = f"""
        You are a professional cryptocurrency trading analyst. Please analyze based on the following BTC/USDT {TRADE_CONFIG.timeframe} period data:

        {kline_text}

        {technical_analysis}

        {signal_text}

        {sentiment_text}  # Add sentiment analysis

        【Current Market】
        - Current price: ${price_data['price']:,.2f}
        - Time: {price_data['timestamp']}
        - Current K-line high: ${price_data['high']:,.2f}
        - Current K-line low: ${price_data['low']:,.2f}
        - Current K-line volume: {price_data['volume']:.2f} BTC
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
            result = response.choices[0].message.content
            logger.log_info(f"DeepSeek original reply: {result}")

            # Extract JSON part
            start_idx = result.find('{')
            end_idx = result.rfind('}') + 1

            if start_idx != -1 and end_idx != 0:
                json_str = result[start_idx:end_idx]
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
            add_to_signal_history(signal_data)
            if len(signal_history) > 30:
                signal_history.pop(0)

            # Signal statistics
            signal_count = len([s for s in signal_history if s.get('signal') == signal_data['signal']])
            total_signals = len(signal_history)
            logger.log_info(f"Signal statistics: {signal_data['signal']} (Appeared {signal_count} times in recent {total_signals} signals)")

            # Signal continuity check
            if len(signal_history) >= 3:
                last_three = [s['signal'] for s in signal_history[-3:]]
                if len(set(last_three)) == 1:
                    logger.log_warning(f"⚠️ Note: Consecutive 3 {signal_data['signal']} signals")

            return signal_data

        except Exception as api_error:
                # 🔴API call or response processing failed
                logger.log_error("deepseek_api_call", str(api_error))
                return create_fallback_signal(price_data)
            
    except Exception as prep_error:
        # 🔴Preparation phase failed
        logger.log_error("analysis_preparation", str(prep_error))
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
                log_perpetual_order_details('sell', close_size, 'market', reduce_only=True, take_profit=True)
                
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
                log_perpetual_order_details('buy', close_size, 'market', reduce_only=True, take_profit=True)
                
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
                set_breakeven_stop(current_position, price_data)
                
        logger.log_info("✅ 多级止盈执行完成")
        
    except Exception as e:
        logger.log_error("profit_taking_execution", str(e))

def set_initial_stop_loss(symbol: str, signal: str, position_size: float, stop_loss_price: float, current_price: float) -> bool:
    """设置初始止损订单"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        # 确定止损方向
        if signal == 'BUY':
            # 多头持仓，止损是卖出
            side = 'sell'
        else:  # SELL
            # 空头持仓，止损是买入平仓
            side = 'buy'
        
        # 确保参数正确
        if isinstance(position_size, (int, float)):
            position_size = str(round(position_size, 2))
        if isinstance(stop_loss_price, str):
            stop_loss_price = float(stop_loss_price)
            
        # 验证止损价格
        stop_loss_price = validate_stop_loss_for_order(side.lower(), stop_loss_price, current_price)
        
        logger.log_info(f"🛡️ {symbol}: 设置新止损单 - {side} {position_size}张, 触发价{stop_loss_price:.1f}")
        
        # 先创建新的止损单
        result = create_algo_order(
            symbol,
            side=side,
            sz=position_size,
            trigger_price=stop_loss_price
        )
        
        if result:
            stop_loss_ratio = abs(stop_loss_price - current_price) / current_price * 100
            logger.log_info(f"✅ {symbol}: 新止损单设置成功 - {stop_loss_price:.1f} (距离{stop_loss_ratio:.2f}%)")
            
            # 等待新止损单处理完成
            time.sleep(1)
            
            # 现在取消旧的止损单（如果有的话）
            cancel_existing_algo_orders(symbol)
            
            return True
        else:
            logger.log_error(f"stop_loss_failed_{symbol}", "新止损单设置失败")
            return False
            
    except Exception as e:
        logger.log_error(f"initial_stop_loss_{symbol}", f"止损设置异常: {str(e)}")
        return False
    
def setup_trailing_stop(current_position, activation_ratio=0.50, trailing_ratio=0.20, price_data=None):
    """设置移动止损"""
    try:
        if not current_position:
            return False
            
        entry_price = current_position['entry_price']
        current_price = price_data['price'] if price_data else get_current_price()
        position_size = current_position['size']
        side = current_position['side']
        
        if side == 'long':
            profit_ratio = (current_price - entry_price) / entry_price
            if profit_ratio >= activation_ratio:
                # 计算移动止损价格
                trailing_stop_price = current_price * (1 - trailing_ratio)
                logger.log_info(f"📈 设置多头移动止损: {trailing_stop_price:.2f} (当前盈利: {profit_ratio:.2%})")
                # 这里可以调用设置移动止损的API
                return set_trailing_stop_order(current_position, trailing_stop_price)
        else:  # short
            profit_ratio = (entry_price - current_price) / entry_price
            if profit_ratio >= activation_ratio:
                # 计算移动止损价格
                trailing_stop_price = current_price * (1 + trailing_ratio)
                logger.log_info(f"📉 设置空头移动止损: {trailing_stop_price:.2f} (当前盈利: {profit_ratio:.2%})")
                # 这里可以调用设置移动止损的API
                return set_trailing_stop_order(current_position, trailing_stop_price)
                
        return False
        
    except Exception as e:
        logger.log_error("trailing_stop_setup", str(e))
        return False

def get_current_price(symbol: str): # 新增 symbol 参数
    """获取当前价格"""
    try:
        # 使用传入的 symbol
        ticker = exchange.fetch_ticker(symbol)
        return ticker['last']
    except Exception as e:
        logger.log_error("current_price", str(e))
        return None
    
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
        else:  # sell
            # 空头：止损价格必须高于当前价格
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
def execute_intelligent_trade(symbol: str, signal_data: dict, price_data: dict):
    """执行智能交易 - 包含完整止损止盈设置"""
    global position
    config = SYMBOL_CONFIGS[symbol]
    
    # 订单标签
    order_tag = create_order_tag()

    # 市场条件检查
    if not check_market_conditions(symbol):
        return
    if not check_trading_frequency():
        return
    
    current_position = get_current_position(symbol)

    # 计算基于K线结构的止损
    calculated_stop_loss = None
    risk_config = config.get_risk_config()
    stop_loss_config = risk_config['stop_loss']
    
    if signal_data['signal'] in ['BUY', 'SELL'] and stop_loss_config['kline_based_stop_loss']:
        current_price = price_data['price']
        side = 'long' if signal_data['signal'] == 'BUY' else 'short'
        
        calculated_stop_loss = calculate_kline_based_stop_loss(
            side, 
            current_price, 
            price_data,
            stop_loss_config['max_stop_loss_ratio']
        )
        
        signal_data['stop_loss'] = calculated_stop_loss
        
        stop_loss_ratio = abs(calculated_stop_loss - current_price) / current_price * 100
        logger.log_info(f"📊 {symbol}: 基于K线结构计算止损 - {calculated_stop_loss:.2f} (距离{stop_loss_ratio:.2f}%)")

    # 计算智能仓位
    position_size = calculate_intelligent_position(symbol, signal_data, price_data, current_position)

    logger.log_info(f"🎯 {symbol}: 交易信号 - {signal_data['signal']} | 仓位: {position_size:.2f}张 | 信心: {signal_data['confidence']}")

    if config.test_mode:
        logger.log_info("测试模式 - 仅模拟交易")
        return
    
    try:
        # 获取当前市场数据
        ticker = exchange.fetch_ticker(config.symbol)
        current_price = ticker['last']
        bid_price = ticker['bid']
        ask_price = ticker['ask']
        
        logger.log_info(f"📊 {symbol}: 当前市场 - 价格{current_price:.2f}, 买一{bid_price:.2f}, 卖一{ask_price:.2f}")

        # 验证和调整价格参数
        if signal_data['signal'] in ['BUY', 'SELL'] and calculated_stop_loss:
            side = 'buy' if signal_data['signal'] == 'BUY' else 'sell'
            limit_price, calculated_stop_loss = validate_and_adjust_prices(
                side, calculated_stop_loss, current_price, bid_price, ask_price
            )
            
            logger.log_info(f"🛡️ {symbol}: 止损设置 - {calculated_stop_loss:.2f} (距离{abs(calculated_stop_loss - current_price)/current_price*100:.2f}%)")

        # 执行交易逻辑
        if signal_data['signal'] == 'BUY':
            # 检查是否有现有空头持仓，先平仓
            if current_position and current_position['side'] == 'short':
                logger.log_info(f"🔄 {symbol}: 平空仓开多仓 - 平{current_position['size']}张，开{position_size}张")
                
                close_params = {
                    'reduceOnly': True,
                    'tag': order_tag
                }
                log_order_params("永续合约平仓", close_params, "execute_intelligent_trade")
                log_perpetual_order_details('buy', current_position['size'], 'market', reduce_only=True)
                
                exchange.create_market_order(
                    config.symbol,
                    'buy',
                    current_position['size'],
                    params=close_params
                )
                time.sleep(1)

            # 使用限价单开多仓
            open_params = {
                'tag': order_tag
            }
            
            log_limit_order_params("开仓", open_params, limit_price, calculated_stop_loss, "execute_intelligent_trade")
            log_perpetual_order_details('buy', position_size, 'limit', reduce_only=False, stop_loss_price=calculated_stop_loss)
            
            logger.log_info(f"✅ {symbol}: 限价开多仓提交 - {position_size}张 @ {limit_price:.2f}")

            # 创建限价开仓订单
            exchange.create_limit_order(
                config.symbol,
                'buy',
                position_size,
                limit_price,
                params=open_params
            )
            
            # 🆕 设置初始止损和止盈
            time.sleep(2)  # 等待开仓完成
            
            if calculated_stop_loss:
                set_initial_stop_loss(symbol, 'BUY', position_size, calculated_stop_loss, current_price)
            
            # 计算并设置智能止盈
            take_profit_price = calculate_intelligent_take_profit(
                symbol, 'long', current_price, price_data, risk_reward_ratio=2.0
            )
            set_initial_take_profit(symbol, 'BUY', position_size, take_profit_price, current_price)

        elif signal_data['signal'] == 'SELL':
            # 检查是否有现有多头持仓，先平仓
            if current_position and current_position['side'] == 'long':
                logger.log_info(f"🔄 {symbol}: 平多仓开空仓 - 平{current_position['size']}张，开{position_size}张")
                
                close_params = {
                    'reduceOnly': True,
                    'tag': order_tag
                }
                log_order_params("永续合约平仓", close_params, "execute_intelligent_trade")
                log_perpetual_order_details('sell', current_position['size'], 'market', reduce_only=True)
                
                exchange.create_market_order(
                    config.symbol,
                    'sell',
                    current_position['size'],
                    params=close_params
                )
                time.sleep(1)

            # 使用限价单开空仓
            open_params = {
                'tag': order_tag
            }
            
            log_limit_order_params("开仓", open_params, limit_price, calculated_stop_loss, "execute_intelligent_trade")
            log_perpetual_order_details('sell', position_size, 'limit', reduce_only=False, stop_loss_price=calculated_stop_loss)
            
            logger.log_info(f"✅ {symbol}: 限价开空仓提交 - {position_size}张 @ {limit_price:.2f}")
            
            exchange.create_limit_order(
                config.symbol,
                'sell',
                position_size,
                limit_price,
                params=open_params
            )
            
            # 🆕 设置初始止损和止盈
            time.sleep(2)  # 等待开仓完成
            
            if calculated_stop_loss:
                set_initial_stop_loss(symbol, 'SELL', position_size, calculated_stop_loss, current_price)
            
            # 计算并设置智能止盈
            take_profit_price = calculate_intelligent_take_profit(
                symbol, 'short', current_price, price_data, risk_reward_ratio=2.0
            )
            set_initial_take_profit(symbol, 'SELL', position_size, take_profit_price, current_price)

        elif signal_data['signal'] == 'HOLD':
            logger.log_info(f"✅ {symbol}: 建议观望，不执行交易")
            
            # 🆕 对现有持仓进行动态管理
            if current_position:
                # 检查移动止损
                setup_trailing_stop(symbol, current_position, price_data)
                
                # 检查动态止盈调整
                adjust_take_profit_dynamically(symbol, current_position, price_data)
                
                # 检查多级止盈
                profit_taking_signal = position_manager.check_profit_taking(current_position, price_data)
                if profit_taking_signal:
                    logger.log_info(f"🎯 {symbol}: 执行多级止盈 - {profit_taking_signal['description']}")
                    execute_profit_taking(symbol, current_position, profit_taking_signal, price_data)
                    position_manager.mark_level_executed(current_position, profit_taking_signal['level'])
            
            return

        logger.log_info(f"✅ {symbol}: 限价开仓订单提交成功")
        
        # 等待订单执行
        time.sleep(3)
        
        # 🆕 对新开仓位进行动态管理
        actual_position = get_current_position(symbol)
        if actual_position:
            # 检查移动止损
            setup_trailing_stop(symbol, actual_position, price_data)
            
            # 检查动态止盈调整
            adjust_take_profit_dynamically(symbol, actual_position, price_data)
            
            # 检查多级止盈
            profit_taking_signal = position_manager.check_profit_taking(actual_position, price_data)
            if profit_taking_signal:
                logger.log_info(f"🎯 {symbol}: 执行多级止盈 - {profit_taking_signal['description']}")
                execute_profit_taking(symbol, actual_position, profit_taking_signal, price_data)
                position_manager.mark_level_executed(actual_position, profit_taking_signal['level'])

    except Exception as e:
        logger.log_error(f"trade_execution_{symbol}", str(e))
        
        # 如果限价单失败，尝试使用条件单
        logger.log_warning("⚠️ 限价单失败，尝试使用条件单...")
        try:
            if signal_data['signal'] == 'BUY':
                # 🆕 合并条件单日志
                logger.log_info(f"🔄 条件单开多仓: {position_size}张 @ {ask_price * 0.999:.2f}")
                
                result = create_algo_order(
                    inst_id=get_correct_inst_id(symbol),
                    side='buy',
                    sz=position_size,
                    trigger_price=ask_price * 0.999,
                    algo_order_type='conditional'
                )
                if result and calculated_stop_loss:
                    set_initial_stop_loss('BUY', position_size, calculated_stop_loss, current_price)
                    
            elif signal_data['signal'] == 'SELL':
                # 🆕 合并条件单日志
                logger.log_info(f"🔄 条件单开空仓: {position_size}张 @ {bid_price * 1.001:.2f}")
                
                result = create_algo_order(
                    inst_id=get_correct_inst_id(symbol),
                    side='sell',
                    sz=position_size,
                    trigger_price=bid_price * 1.001,
                    algo_order_type='conditional'
                )
                if result and calculated_stop_loss:
                    set_initial_stop_loss('SELL', position_size, calculated_stop_loss, current_price)
                    
            logger.log_info("✅ 条件单开仓成功")
        except Exception as e2:
            logger.log_error("fallback_order", f"备用订单也失败: {str(e2)}")

        import traceback
        traceback.print_exc()


def filter_signal(signal_data, price_data):
    # If the signal is to buy, but the RSI is above 70, then change it to hold.
    rsi = price_data['technical_data'].get('rsi', 50)
    if signal_data['signal'] == 'BUY' and rsi > 70:
        return {
            **signal_data,
            'signal': 'HOLD',
            'reason': f'RSI overbought ({rsi:.2f}), hold instead',
            'confidence': 'LOW'
        }
    # Similarly, other filtering conditions can be added.
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
        add_to_signal_history(filtered_signal)
        add_to_price_history(price_data)

        # 6. 记录信号
        logger.log_info(f"📊 {symbol} 交易信号: {filtered_signal['signal']} | 信心: {filtered_signal['confidence']}")
        logger.log_info(f"📝 原因: {filtered_signal['reason']}")

        # 7. 执行智能交易
        execute_intelligent_trade(symbol, filtered_signal, price_data)
        
    except Exception as e:
        logger.log_error(f"trading_bot_{symbol}", str(e))

def health_check(symbol: str, price_history: list):
    """Check the health of the system."""
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
    
    # Check data freshness
    if price_history:
        latest_data = price_history[-1]
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
            log_perpetual_order_details('sell', position_size, 'market', reduce_only=True)
            
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
            log_perpetual_order_details('buy', position_size, 'market', reduce_only=True)
            
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
            
            if not current_position:
                logger.log_info(f"✅ {symbol}: 无持仓")
                continue
                
            logger.log_warning(f"⚠️ {symbol}: 发现现有持仓 - {current_position['side']} {current_position['size']}张")
            
            # 获取市场数据进行分析
            df, price_data = fetch_ohlcv(symbol)
            if not df or not price_data:
                logger.log_warning(f"❌ {symbol}: 无法获取市场数据，跳过分析")
                continue
            
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
        if not signal_data:
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


def check_existing_take_profit_orders(symbol: str, position: dict) -> bool:
    """检查是否已有止盈订单"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        params = {
            'instType': 'SWAP',
            'algoOrdType': 'conditional'
        }
        
        response = exchange.privateGetTradeOrdersAlgoPending(params)
        
        if response['code'] == '0':
            inst_id = get_correct_inst_id(symbol)
            
            for order in response.get('data', []):
                if order['instId'] == inst_id:
                    # 根据持仓方向匹配止盈单
                    if position['side'] == 'long' and order['side'] == 'sell' and 'tpTriggerPx' in order:
                        trigger_price = order.get('tpTriggerPx', '未知')
                        logger.log_info(f"✅ {symbol}: 匹配到多头止盈单 - 触发价: {trigger_price}")
                        return True
                    elif position['side'] == 'short' and order['side'] == 'buy' and 'tpTriggerPx' in order:
                        trigger_price = order.get('tpTriggerPx', '未知')
                        logger.log_info(f"✅ {symbol}: 匹配到空头止盈单 - 触发价: {trigger_price}")
                        return True
            
            logger.log_info(f"ℹ️ {symbol}: 找到算法订单但无匹配的止盈单")
            return False
        else:
            logger.log_warning(f"⚠️ {symbol}: 获取算法订单失败 - {response.get('msg', '未知错误')}")
            return False
            
    except Exception as e:
        logger.log_error(f"take_profit_check_{symbol}", f"止盈检查失败: {str(e)}")
        return False

def check_and_set_stop_loss(symbol: str, position: dict, price_data: dict):
    """检查并设置止损和止盈订单"""
    try:
        config = SYMBOL_CONFIGS[symbol]
        
        # 检查是否已有止损订单
        has_stop_loss = check_existing_stop_loss_orders(symbol, position)
        
        if not has_stop_loss:
            logger.log_warning(f"⚠️ {symbol}: 未检测到止损订单，正在设置...")
            
            # 计算止损价格
            current_price = price_data['price']
            risk_config = config.get_risk_config()
            stop_loss_config = risk_config['stop_loss']
            
            if position['side'] == 'long':
                if stop_loss_config['kline_based_stop_loss']:
                    stop_loss_price = calculate_kline_based_stop_loss(
                        'long', current_price, price_data, stop_loss_config['max_stop_loss_ratio']
                    )
                else:
                    stop_loss_price = current_price * (1 - stop_loss_config['min_stop_loss_ratio'])
            else:  # short
                if stop_loss_config['kline_based_stop_loss']:
                    stop_loss_price = calculate_kline_based_stop_loss(
                        'short', current_price, price_data, stop_loss_config['max_stop_loss_ratio']
                    )
                else:
                    stop_loss_price = current_price * (1 + stop_loss_config['min_stop_loss_ratio'])
            
            # 设置止损订单
            success_sl = set_initial_stop_loss(
                symbol,
                position['side'].upper(),
                position['size'],
                stop_loss_price,
                current_price
            )
            
            if success_sl:
                logger.log_info(f"✅ {symbol}: 止损订单设置成功 - {stop_loss_price:.2f}")
            else:
                logger.log_error(f"stop_loss_setup_{symbol}", "止损订单设置失败")
        
        # 🆕 检查是否已有止盈订单
        has_take_profit = check_existing_take_profit_orders(symbol, position)
        
        if not has_take_profit:
            logger.log_warning(f"⚠️ {symbol}: 未检测到止盈订单，正在设置...")
            
            # 计算止盈价格
            take_profit_price = calculate_intelligent_take_profit(
                symbol, position['side'], position['entry_price'], price_data, risk_reward_ratio=2.0
            )
            
            # 设置止盈订单
            success_tp = set_initial_take_profit(
                symbol,
                position['side'].upper(),
                position['size'],
                take_profit_price,
                price_data['price']
            )
            
            if success_tp:
                logger.log_info(f"✅ {symbol}: 止盈订单设置成功 - {take_profit_price:.2f}")
            else:
                logger.log_error(f"take_profit_setup_{symbol}", "止盈订单设置失败")
        
        # 🆕 设置移动止损
        setup_trailing_stop(symbol, position, price_data)
                
        return success_sl and success_tp
            
    except Exception as e:
        logger.log_error(f"stop_loss_check_{symbol}", f"止损止盈检查设置失败: {str(e)}")
        return False

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
            log_perpetual_order_details('sell', position['size'], 'market', reduce_only=True)
            
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
            log_perpetual_order_details('buy', position['size'], 'market', reduce_only=True)
            
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


def check_existing_stop_loss_orders(symbol: str, position: dict) -> bool:
    """检查是否已有止损订单 - 增强版本"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        # 使用算法订单API检查
        params = {
            'instType': 'SWAP',
            'algoOrdType': 'conditional'
        }
        
        response = exchange.privateGetTradeOrdersAlgoPending(params)
        
        if response['code'] == '0':
            inst_id = get_correct_inst_id(symbol)
            
            for order in response.get('data', []):
                if order['instId'] == inst_id:
                    # 根据持仓方向匹配止损单
                    if position['side'] == 'long' and order['side'] == 'sell':
                        trigger_price = order.get('slTriggerPx', '未知')
                        logger.log_info(f"✅ {symbol}: 匹配到多头止损单 - 触发价: {trigger_price}")
                        return True
                    elif position['side'] == 'short' and order['side'] == 'buy':
                        trigger_price = order.get('slTriggerPx', '未知')
                        logger.log_info(f"✅ {symbol}: 匹配到空头止损单 - 触发价: {trigger_price}")
                        return True
            
            logger.log_info(f"ℹ️ {symbol}: 找到算法订单但无匹配的止损单")
            return False
        else:
            logger.log_warning(f"⚠️ {symbol}: 获取算法订单失败 - {response.get('msg', '未知错误')}")
            # 备用检查：通过持仓信息
            return check_existing_stop_loss_alternative(symbol, position)
            
    except Exception as e:
        logger.log_error(f"stop_loss_check_{symbol}", f"止损检查失败: {str(e)}")
        # 异常时使用备用检查
        return check_existing_stop_loss_alternative(symbol, position)

def check_existing_stop_loss_alternative(symbol: str, position: dict) -> bool:
    """备用方法检查止损单 - 通过持仓信息"""
    config = SYMBOL_CONFIGS[symbol]
    try:
        # 获取持仓信息，看是否有止损价格
        positions = exchange.fetch_positions([config.symbol])
        
        for pos in positions:
            if pos['symbol'] == config.symbol and float(pos.get('contracts', 0)) > 0:
                # 检查持仓中是否有止损价格信息
                if pos.get('stopLossPrice') or pos.get('liquidationPrice'):
                    stop_price = pos.get('stopLossPrice') or pos.get('liquidationPrice')
                    logger.log_info(f"✅ {symbol}: 通过持仓信息找到止损设置: {stop_price}")
                    return True
        
        logger.log_info(f"❌ {symbol}: 备用检查也未找到止损设置")
        return False
        
    except Exception as e:
        logger.log_error(f"alternative_stop_check_{symbol}", f"备用检查方法失败: {str(e)}")
        return False


def log_performance_metrics(symbol: str):
    """Log performance metrics."""
    if not signal_history:
        return
    
    signals = [s['signal'] for s in signal_history]
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
            
            # Health check
            if current_time - last_health_check >= health_check_interval:
                logger.log_info("🔍 Running scheduled health check...")
                if not health_check(symbols_to_trade[0], []): # 仅检查第一个品种
                    consecutive_errors += 1
                    # 使用任一配置的错误限制
                    if consecutive_errors >= first_config.max_consecutive_errors:
                        logger.log_info("🚨 Too many consecutive errors, exiting.")
                        break
                else:
                    consecutive_errors = 0
                last_health_check = current_time
            
            # Configuration reload check - every 5 minutes
            if current_time - last_config_check >= config_check_interval:
                # 注意: 我们不能热重载所有配置，只能检查文件变动并重新初始化。
                # 由于采用了多配置模式，简化为跳过热重载逻辑，让用户重启以加载新配置。
                # 如果要实现热重载，需要复杂的文件监控和配置替换逻辑。
                # 原始代码: if TRADE_CONFIG.should_reload(): TRADE_CONFIG.reload()  
                # 新代码: 保持原样，但 TRADE_CONFIG 已被替换。为简单起见，我们跳过这部分
                # 或者可以检查第一个配置是否需要重载：
                # if first_config.should_reload(): 
                #    logger.log_warning("⚠️ Configuration reload requested, please restart the bot to load new multi-symbol configs.")
                last_config_check = current_time

            # Run trading bot for all symbols
            for symbol in symbols_to_trade: # 遍历所有品种
                trading_bot(symbol)
            
            # Log performance (可选: 可以修改 log_performance_metrics 来汇总所有品种)
            # log_performance_metrics() # 原始代码: 移除或修改
            for symbol in symbols_to_trade:
                log_performance_metrics(symbol) # 新代码

            # Wait for next cycle
            time.sleep(60)
            
        except KeyboardInterrupt:
            logger.log_warning("\n🛑 User interrupted the program.")
            break
        except Exception as e:
            logger.log_error("main_loop", str(e))
            consecutive_errors += 1
            # 使用任一配置的错误限制
            if consecutive_errors >= first_config.max_consecutive_errors:
                logger.log_warning("🚨 Too many consecutive errors, exiting.")
                break
            time.sleep(60)

if __name__ == "__main__":
    main()
