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
from trade_config import TRADE_CONFIG

# Global logger
from trade_logger import logger

# Use relative path
env_path = '../ExApiConfig/ExApiConfig.env'  # .env file in config folder of parent directory
logger.log_info(f"📁Add config file: {env_path}")
load_dotenv(dotenv_path=env_path)

# Initialize DeepSeek client with error handling
deepseek_client = None

def get_deepseek_client():
    global deepseek_client
    if deepseek_client is None:
        try:
            api_key = os.getenv('DEEPSEEK_API_KEY')
            if not api_key:
                raise ValueError("DEEPSEEK_API_KEY environment variable is not set")
            
            deepseek_client = OpenAI(
                api_key=api_key,
                base_url=TRADE_CONFIG.deepseek_base_url
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

def check_existing_positions():
    # Check existing positions and return whether there are isolated positions and the information of isolated positions.
    logger.log_info("🔍 Checking existing position mode..")
    positions = exchange.fetch_positions([TRADE_CONFIG.symbol])

    has_isolated_position = False
    isolated_position_info = None

    for pos in positions:
        if pos['symbol'] == TRADE_CONFIG.symbol:
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

def check_current_margin_mode():
    """检查当前仓位模式 - 简化版本"""
    try:
        positions = exchange.fetch_positions([TRADE_CONFIG.symbol])
        for pos in positions:
            if pos['symbol'] == TRADE_CONFIG.symbol:
                mode = pos.get('mgnMode', 'unknown')
                if mode != 'unknown':
                    return mode
        
        # 如果没有持仓，返回默认值
        return getattr(TRADE_CONFIG, 'margin_mode', 'isolated')
        
    except Exception as e:
        logger.log_error("margin_mode_check", str(e))
        return getattr(TRADE_CONFIG, 'margin_mode', 'isolated')


def setup_exchange():
    """智能交易所设置 - 简化保证金模式设置"""
    try:
        # 获取合约规格
        markets = exchange.load_markets()
        btc_market = markets[TRADE_CONFIG.symbol]
        
        TRADE_CONFIG.contract_size = float(btc_market['contractSize'])
        TRADE_CONFIG.min_amount = btc_market['limits']['amount']['min']
        
        logger.log_info(f"✅ Contract: 1 contract = {TRADE_CONFIG.contract_size} BTC")
        logger.log_info(f"📏 Min trade: {TRADE_CONFIG.min_amount} contracts")

        # 获取配置的保证金模式
        margin_mode = getattr(TRADE_CONFIG, 'margin_mode', 'isolated')
        logger.log_info(f"🎯 Target margin mode: {margin_mode}")

        # 简化设置流程 - 只设置必要的参数
        logger.log_info("🔄 Setting basic exchange parameters...")
        
        # 设置杠杆（这是最重要的）
        logger.log_info("⚙️ Setting leverage...")
        try:
            exchange.set_leverage(TRADE_CONFIG.leverage, TRADE_CONFIG.symbol)
            logger.log_warning(f"✅ Leverage {TRADE_CONFIG.leverage}x")
        except Exception as e:
            logger.log_warning(f"⚠️ Leverage setting: {e}")

        # 对于OKX，很多时候保证金模式在开仓时自动设置
        # 我们主要确保杠杆设置正确即可
        
        # 账户信息
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']
        logger.log_info(f"💰 USDT balance: {usdt_balance:.2f}")
        
        # 记录当前模式（但不强制设置）
        current_mode = check_current_margin_mode()
        logger.log_info(f"📊 Current margin mode: {current_mode}")
        
        return True

    except Exception as e:
        logger.log_error("exchange_setup", str(e))
        return False

# Global variables to store historical data
price_history = []
signal_history = []
position = None


def calculate_intelligent_position(signal_data: dict, price_data: dict, current_position: Optional[dict]) -> float:
    """Calculate intelligent position size - fixed version"""
    config = TRADE_CONFIG.position_management

    # 🆕 New: If intelligent position is disabled, use fixed position
    if not config.get('enable_intelligent_position', True):
        fixed_contracts = 0.1
        logger.log_info(f"🔧 智能仓位已禁用，使用固定仓位: {fixed_contracts}张")
        return fixed_contracts

    try:
        # Get account balance
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']

        # Base USDT investment
        base_usdt = config['base_usdt_amount']
        logger.log_warning(f"💰 Available USDT balance: {usdt_balance:.2f}, base investment {base_usdt}")

        # Adjust based on confidence level - fix here
        confidence_multiplier = {
            'HIGH': config['high_confidence_multiplier'],
            'MEDIUM': config['medium_confidence_multiplier'],
            'LOW': config['low_confidence_multiplier']
        }.get(signal_data['confidence'], 1.0)  # Add default value

        # Adjust based on trend strength
        trend = price_data['trend_analysis'].get('overall', 'Consolidation')
        if trend in ['Strong uptrend', 'Strong downtrend']:
            trend_multiplier = config['trend_strength_multiplier']
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
        max_usdt = usdt_balance * config['max_position_ratio']
        final_usdt = min(suggested_usdt, max_usdt)

        # Correct contract quantity calculation!
        # Formula: Contract quantity = (Investment USDT) / (Current price * Contract multiplier)
        contract_size = (final_usdt) / (price_data['price'] * TRADE_CONFIG.contract_size)

        # Precision handling: OKX BTC contract minimum trading unit is 0.01 contracts
        contract_size = round(contract_size, 2)  # Keep 2 decimal places

        # Ensure minimum trading volume
        min_contracts = getattr(TRADE_CONFIG, 'min_amount', 0.01)
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
        base_usdt = config['base_usdt_amount']
        contract_size = (base_usdt * TRADE_CONFIG.leverage) / (
                    price_data['price'] * getattr(TRADE_CONFIG, 'contract_size', 0.01))
        return round(max(contract_size, getattr(TRADE_CONFIG, 'min_amount', 0.01)), 2)


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
    
def verify_margin_mode():
    """验证保证金模式设置是否正确"""
    try:
        positions = exchange.fetch_positions([TRADE_CONFIG.symbol])
        target_mode = getattr(TRADE_CONFIG, 'margin_mode', 'isolated')
        
        for pos in positions:
            if pos['symbol'] == TRADE_CONFIG.symbol:
                current_mode = pos.get('mgnMode', 'unknown')
                logger.log_info(f"📊 当前持仓保证金模式: {current_mode}, 目标模式: {target_mode}")
                
                if current_mode == target_mode:
                    logger.log_info(f"✅ 保证金模式验证成功: {current_mode}")
                    return True
                else:
                    logger.log_warning(f"⚠️ 保证金模式不匹配: 当前={current_mode}, 目标={target_mode}")
                    # 尝试重新设置
                    try:
                        exchange.set_margin_mode(target_mode, TRADE_CONFIG.symbol)
                        logger.log_info(f"🔄 重新设置保证金模式为: {target_mode}")
                        return True
                    except Exception as e:
                        logger.log_error("margin_mode_recovery", str(e))
                        return False
        
        # 如果没有持仓，检查账户配置
        try:
            response = exchange.private_get_account_config()
            if response and response.get('code') == '0' and response.get('data'):
                for config in response['data']:
                    if config.get('instType') == 'SWAP':
                        mgn_mode = config.get('mgnMode', 'unknown')
                        logger.log_info(f"📊 账户配置保证金模式: {mgn_mode}")
                        if mgn_mode == target_mode:
                            return True
        except Exception as e:
            logger.log_warning(f"账户配置检查失败: {e}")
            
        logger.log_info(f"✅ 无持仓，假设保证金模式设置正确: {target_mode}")
        return True
        
    except Exception as e:
        logger.log_error("margin_mode_verification", str(e))
        return False

def get_correct_inst_id():
    """获取正确的合约ID"""
    # 对于 BTC/USDT:USDT，正确的instId是 BTC-USDT-SWAP
    symbol = TRADE_CONFIG.symbol
    if symbol == 'BTC/USDT:USDT':
        return 'BTC-USDT-SWAP'
    elif symbol == 'ETH/USDT:USDT':
        return 'ETH-USDT-SWAP'
    else:
        # 通用处理
        return symbol.replace('/', '-').replace(':USDT', '-SWAP')

def create_algo_order(inst_id, side, sz, trigger_price, algo_order_type='conditional'):
    """创建算法订单 - 永续合约条件单（不取消现有订单）"""
    try:
        # 确保使用正确的合约ID
        inst_id = get_correct_inst_id()
        
        # 确保参数类型正确
        if isinstance(trigger_price, str):
            trigger_price = float(trigger_price)
        if isinstance(sz, (int, float)):
            sz = str(round(sz, 2))
            
        margin_mode = getattr(TRADE_CONFIG, 'margin_mode', 'isolated')
        
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

def cancel_existing_algo_orders():
    """取消现有的算法订单 - 完全重写"""
    try:
        # 使用正确的API端点获取待处理算法订单
        params = {
            'instType': 'SWAP',
            'algoOrdType': 'conditional'
        }
        
        # 使用正确的API方法
        response = exchange.privateGetTradeOrdersAlgoPending(params)
        
        if response['code'] == '0' and response['data']:
            for order in response['data']:
                # 取消条件单
                cancel_params = {
                    'algoId': order['algoId'],
                    'instId': order['instId'],
                    'algoOrdType': 'conditional'
                }
                cancel_response = exchange.privatePostTradeCancelAlgoOrder(cancel_params)
                if cancel_response['code'] == '0':
                    logger.log_info(f"✅ 取消现有条件单: {order['algoId']}")
                else:
                    logger.log_warning(f"⚠️ 取消条件单失败: {cancel_response}")
        else:
            logger.log_info("✅ 没有找到待取消的条件单")
                    
    except Exception as e:
        logger.log_error("cancel_algo_orders", str(e))

def set_breakeven_stop(current_position, price_data):
    """使用OKX算法订单设置保本止损"""
    try:
        # 获取剩余仓位大小（假设已经止盈30%）
        remaining_size = current_position['size'] * 0.70  # 剩余70%
        remaining_size = round(remaining_size, 2)
        
        if remaining_size < getattr(TRADE_CONFIG, 'min_amount', 0.01):
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
            inst_id=TRADE_CONFIG.symbol.replace('/', '').replace(':', '-'),
            algo_order_type=algo_order_type,
            side=trigger_action,
            order_type=order_type,
            sz=str(remaining_size),
            trigger_price=str(trigger_price)
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

def calculate_kline_based_stop_loss(side, entry_price, price_data, max_stop_loss_ratio=0.40):
    """
    基于K线结构计算止损价格
    side: 'long' 或 'short'
    entry_price: 开仓价格
    price_data: 价格数据
    max_stop_loss_ratio: 最大止损比例
    """
    try:
        df = price_data['full_data']
        current_price = price_data['price']
        
        if side == 'long':
            # 多头止损：基于支撑位和ATR计算
            support_level = price_data['levels_analysis'].get('static_support', current_price)
            atr = calculate_atr(df)  # 需要添加ATR计算函数
            
            # 使用支撑位或基于ATR的止损，取较宽松的一个
            stop_loss_by_support = support_level
            stop_loss_by_atr = current_price - (atr * 2)  # 2倍ATR
            
            stop_loss_price = min(stop_loss_by_support, stop_loss_by_atr)
            
            # 确保止损不超过最大比例
            max_stop_loss_price = current_price * (1 - max_stop_loss_ratio)
            stop_loss_price = max(stop_loss_price, max_stop_loss_price)
            
        else:  # short
            # 空头止损：基于阻力位和ATR计算
            resistance_level = price_data['levels_analysis'].get('static_resistance', current_price)
            atr = calculate_atr(df)
            
            # 使用阻力位或基于ATR的止损，取较宽松的一个
            stop_loss_by_resistance = resistance_level
            stop_loss_by_atr = current_price + (atr * 2)
            
            stop_loss_price = max(stop_loss_by_resistance, stop_loss_by_atr)
            
            # 确保止损不超过最大比例
            max_stop_loss_price = current_price * (1 + max_stop_loss_ratio)
            stop_loss_price = min(stop_loss_price, max_stop_loss_price)
        
        logger.log_info(f"🎯 K线结构止损计算: {side}方向, 入场{entry_price:.2f}, 止损{stop_loss_price:.2f}")
        return stop_loss_price
        
    except Exception as e:
        logger.log_error("stop_loss_calculation", str(e))
        # 备用止损计算
        if side == 'long':
            return entry_price * (1 - max_stop_loss_ratio)
        else:
            return entry_price * (1 + max_stop_loss_ratio)

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


def fetch_ohlcv_with_retry(max_retries=None):
    if max_retries is None:
        max_retries = TRADE_CONFIG.max_retries
    
    for i in range(max_retries):
        try:
            return exchange.fetch_ohlcv(TRADE_CONFIG.symbol, TRADE_CONFIG.timeframe, limit=TRADE_CONFIG.data_points)
        except Exception as e:
            logger.log_error(f"Get K line fail, retry {i+1}/{max_retries}", str(e))
            time.sleep(1)
    return None

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

@retry_on_failure(max_retries=TRADE_CONFIG.max_retries, delay=TRADE_CONFIG.retry_delay)
def get_btc_ohlcv_enhanced():
    """Enhanced version: Get BTC K-line data and calculate technical indicators"""
    try:
        # Get K-line data
        ohlcv = fetch_ohlcv_with_retry()

        if ohlcv is None:
            logger.log_warning("❌ Failed to fetch K-line data")
            return None

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        # Calculate technical indicators
        df = calculate_technical_indicators(df)

        current_data = df.iloc[-1]
        previous_data = df.iloc[-2]

        # Get technical analysis data
        trend_analysis = get_market_trend(df)
        levels_analysis = get_support_resistance_levels(df)

        return {
            'price': current_data['close'],
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'high': current_data['high'],
            'low': current_data['low'],
            'volume': current_data['volume'],
            'timeframe': TRADE_CONFIG.timeframe,
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
    except Exception as e:
        logger.log_error("kline_data", str(e))
        return None
    
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


def get_current_position() -> Optional[dict]:
    """Get current position status - OKX version"""
    try:
        positions = exchange.fetch_positions([TRADE_CONFIG.symbol])
        if not positions:
            return None
        
        for pos in positions:
            if pos['symbol'] == TRADE_CONFIG.symbol:
                contracts = float(pos['contracts']) if pos['contracts'] else 0

                if contracts > 0:
                    return {
                        'side': pos['side'],  # 'long' or 'short'
                        'size': contracts,
                        'entry_price': float(pos['entryPrice']) if pos['entryPrice'] else 0,
                        'unrealized_pnl': float(pos['unrealizedPnl']) if pos['unrealizedPnl'] else 0,
                        'leverage': float(pos['leverage']) if pos['leverage'] else TRADE_CONFIG.leverage,
                        'symbol': pos['symbol']
                    }

        return None

    except Exception as e:
        logger.log_error("position_fetch", f"Failed to fetch positions: {str(e)}")
        return None


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

@retry_on_failure(max_retries=TRADE_CONFIG.max_retries, delay=TRADE_CONFIG.retry_delay)
def analyze_with_deepseek(price_data):
    """Use DeepSeek to analyze market and generate trading signals (enhanced version)"""
    try:
        # Get the client (will be initialized on the first call)
        client = get_deepseek_client()
    
        # Generate technical analysis text
        technical_analysis = generate_technical_analysis_text(price_data)

        # Build K-line data text
        kline_text = f"【Recent 5 {TRADE_CONFIG.timeframe} K-line Data】\n"
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
        current_pos = get_current_position()
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
         "content": f"""You are a professional trader specializing in {TRADE_CONFIG.timeframe} period trend analysis and trend reversal detection. 
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

def check_market_conditions():
    """Check if market conditions are suitable for trading."""
    try:
        ticker = exchange.fetch_ticker(TRADE_CONFIG.symbol)
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

def execute_profit_taking(current_position, profit_taking_signal, price_data):
    """执行多级止盈逻辑 - 永续合约市价平仓"""
    try:
        order_tag = create_order_tag()
        position_size = current_position['size']
        take_profit_ratio = profit_taking_signal['take_profit_ratio']
        
        # 计算需要平仓的数量
        close_size = position_size * take_profit_ratio
        close_size = round(close_size, 2)
        
        if close_size < getattr(TRADE_CONFIG, 'min_amount', 0.01):
            close_size = getattr(TRADE_CONFIG, 'min_amount', 0.01)
            
        logger.log_info(f"💰 执行部分止盈: 平仓{close_size:.2f}张合约 ({take_profit_ratio:.1%}仓位)")
        
        if not TRADE_CONFIG.test_mode:
            # 记录止盈订单参数 - 永续合约市价平仓
            if current_position['side'] == 'long':
                profit_params = {
                    'reduceOnly': True,
                    'tag': order_tag,
                    'symbol': TRADE_CONFIG.symbol,
                    'side': 'sell',
                    'amount': close_size,
                    'type': 'market',
                    'profit_taking_ratio': take_profit_ratio,
                    'original_position_size': position_size
                }
                log_order_params("永续合约止盈平仓", profit_params, "execute_profit_taking")
                log_perpetual_order_details('sell', close_size, 'market', reduce_only=True, take_profit=True)
                
                exchange.create_market_order(
                    TRADE_CONFIG.symbol,
                    'sell',
                    close_size,
                    params={'reduceOnly': True, 'tag': order_tag}
                )
            else:  # short
                profit_params = {
                    'reduceOnly': True,
                    'tag': order_tag,
                    'symbol': TRADE_CONFIG.symbol,
                    'side': 'buy',
                    'amount': close_size,
                    'type': 'market',
                    'profit_taking_ratio': take_profit_ratio,
                    'original_position_size': position_size
                }
                log_order_params("永续合约止盈平仓", profit_params, "execute_profit_taking")
                log_perpetual_order_details('buy', close_size, 'market', reduce_only=True, take_profit=True)
                
                exchange.create_market_order(
                    TRADE_CONFIG.symbol,
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

def set_initial_stop_loss(signal, position_size, stop_loss_price, current_price):
    """设置初始止损订单 - 先设置新止损单，再取消旧的"""
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
            
        # 获取正确的合约ID
        inst_id = get_correct_inst_id()
        
        logger.log_info(f"🛡️ 设置新止损单: {side} {position_size}张, 触发价{stop_loss_price:.1f}")
        
        # 先创建新的止损单
        result = create_algo_order(
            inst_id=inst_id,
            side=side,
            sz=position_size,
            trigger_price=stop_loss_price
        )
        
        if result:
            stop_loss_ratio = abs(stop_loss_price - current_price) / current_price * 100
            logger.log_info(f"✅ 新止损单设置成功: {stop_loss_price:.1f} (距离{stop_loss_ratio:.2f}%)")
            
            # 等待新止损单处理完成
            time.sleep(1)
            
            # 现在取消旧的止损单（如果有的话）
            cancel_existing_algo_orders()
            
            return True
        else:
            logger.log_error("stop_loss_failed", "新止损单设置失败")
            return False
            
    except Exception as e:
        logger.log_error("initial_stop_loss_setting", f"止损设置异常: {str(e)}")
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

def set_trailing_stop_order(current_position, stop_price):
    """设置移动止损订单 - 先设置新的，再取消旧的"""
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
            inst_id=TRADE_CONFIG.symbol.replace('/', '').replace(':', '-'),
            algo_order_type='conditional',
            side=trigger_action,
            order_type='market',
            sz=str(position_size),
            trigger_price=str(stop_price)
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

def get_current_price():
    """获取当前价格"""
    try:
        ticker = exchange.fetch_ticker(TRADE_CONFIG.symbol)
        return ticker['last']
    except Exception as e:
        logger.log_error("get_current_price", str(e))
        return 0
    
def verify_stop_loss_setting(signal, position_size, stop_loss_price):
    """验证止损订单是否设置成功 - 增强版本"""
    try:
        # 等待一段时间让订单处理
        time.sleep(2)
        
        # 获取未完成的算法订单
        params = {
            'instType': 'SWAP',
            'algoOrdType': 'conditional'
        }
        
        response = exchange.privateGetTradeOrdersAlgoPending(params)
        
        if response['code'] == '0' and response['data']:
            for order in response['data']:
                if order['instId'] == get_correct_inst_id():
                    # 检查止损订单 - 根据方向匹配
                    if signal == 'BUY':
                        # 多头持仓的止损应该是卖出
                        if order['side'] == 'sell' and 'slTriggerPx' in order:
                            trigger_price = float(order['slTriggerPx'])
                            if abs(trigger_price - stop_loss_price) < 0.1:  # 允许微小误差
                                logger.log_info(f"✅ 止损订单验证成功: {stop_loss_price}")
                                return True
                    else:  # SELL
                        # 空头持仓的止损应该是买入
                        if order['side'] == 'buy' and 'slTriggerPx' in order:
                            trigger_price = float(order['slTriggerPx'])
                            if abs(trigger_price - stop_loss_price) < 0.1:
                                logger.log_info(f"✅ 止损订单验证成功: {stop_loss_price}")
                                return True
        
        logger.log_warning(f"⚠️ 止损订单验证失败，未找到匹配的止损单")
        return False
        
    except Exception as e:
        logger.log_error("stop_loss_verification", str(e))
        return False

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

def log_limit_order_params(order_type, params, limit_price, stop_loss_price, function_name=""):
    """记录限价单参数到日志 - 永续合约专用"""
    try:
        # 隐藏敏感信息
        safe_params = params.copy()
        sensitive_keys = ['apiKey', 'secret', 'password', 'signature']
        for key in sensitive_keys:
            if key in safe_params:
                safe_params[key] = '***'
        
        logger.log_info(f"📋 {function_name} - 限价{order_type}订单参数:")
        logger.log_info(f"   限价价格: {limit_price:.2f}")
        logger.log_info(f"   止损价格: {stop_loss_price:.2f}")
        
        # 计算止损距离
        if order_type == "开仓" and 'stopLoss' in safe_params:
            stop_loss_trigger = safe_params['stopLoss'].get('triggerPrice', stop_loss_price)
            stop_loss_distance = abs(limit_price - stop_loss_trigger) / limit_price * 100
            logger.log_info(f"   止损距离: {stop_loss_distance:.2f}%")
        
        for key, value in safe_params.items():
            if key != 'stopLoss':  # 止损参数已经单独显示
                logger.log_info(f"   {key}: {value}")
            
        # 特别显示止损参数
        if 'stopLoss' in safe_params:
            sl_params = safe_params['stopLoss']
            logger.log_info(f"   止损参数:")
            for sl_key, sl_value in sl_params.items():
                logger.log_info(f"     {sl_key}: {sl_value}")
                
        # 特别标注订单类型
        logger.log_info(f"   🔍 订单类型确认: 永续合约限价{order_type}")
            
    except Exception as e:
        logger.log_error("log_limit_order_params", f"记录限价单参数失败: {str(e)}")

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

def execute_intelligent_trade(signal_data, price_data):
    """执行智能交易 - 限价单版本，开单同时设置止损"""
    global position

    # 订单标签
    order_tag = create_order_tag()

    # 市场条件检查
    if not check_market_conditions():
        return
    if not check_trading_frequency():
        return
    
    current_position = get_current_position()

    # 计算基于K线结构的止损
    calculated_stop_loss = None
    risk_config = TRADE_CONFIG.get_risk_config()
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
        
        # 🆕 在这里添加合并的止损日志（替换原来的详细日志）
        stop_loss_ratio = abs(calculated_stop_loss - current_price) / current_price * 100
        logger.log_info(f"📊 基于K线结构计算止损: {calculated_stop_loss:.2f} (距离{stop_loss_ratio:.2f}%)")

    # 计算智能仓位
    position_size = calculate_intelligent_position(signal_data, price_data, current_position)

    # 🆕 在这里添加合并的交易信号日志（替换原来的多条日志）
    logger.log_info(f"🎯 交易信号: {signal_data['signal']} | 仓位: {position_size:.2f}张 | 信心: {signal_data['confidence']}")

    if TRADE_CONFIG.test_mode:
        logger.log_info("测试模式 - 仅模拟交易")
        return
    
    try:
        # 获取当前市场数据
        ticker = exchange.fetch_ticker(TRADE_CONFIG.symbol)
        current_price = ticker['last']
        bid_price = ticker['bid']  # 买一价
        ask_price = ticker['ask']  # 卖一价
        
        # 🆕 合并市场数据日志
        logger.log_info(f"📊 当前市场: 价格{current_price:.2f}, 买一{bid_price:.2f}, 卖一{ask_price:.2f}")

        # 验证和调整价格参数
        if signal_data['signal'] in ['BUY', 'SELL'] and calculated_stop_loss:
            side = 'buy' if signal_data['signal'] == 'BUY' else 'sell'
            limit_price, calculated_stop_loss = validate_and_adjust_prices(
                side, calculated_stop_loss, current_price, bid_price, ask_price
            )
            
            # 🆕 添加止损设置合并日志
            logger.log_info(f"🛡️ 止损设置: {calculated_stop_loss:.2f} (距离{abs(calculated_stop_loss - current_price)/current_price*100:.2f}%)")

        # 执行交易逻辑 - 限价单同时设置止损
        if signal_data['signal'] == 'BUY':
            # 检查是否有现有空头持仓，先平仓
            if current_position and current_position['side'] == 'short':
                # 🆕 合并平仓日志
                logger.log_info(f"🔄 平空仓开多仓: 平{current_position['size']}张，开{position_size}张")
                
                close_params = {
                    'reduceOnly': True,
                    'tag': order_tag
                }
                log_order_params("永续合约平仓", close_params, "execute_intelligent_trade")
                log_perpetual_order_details('buy', current_position['size'], 'market', reduce_only=True)
                
                exchange.create_market_order(
                    TRADE_CONFIG.symbol,
                    'buy',
                    current_position['size'],
                    params=close_params
                )
                time.sleep(1)

            # 使用限价单开多仓，同时设置止损
            open_params = {
                'tag': order_tag,
                'stopLoss': {
                    'triggerPrice': calculated_stop_loss,
                    'price': calculated_stop_loss,
                    'type': 'market'
                }
            }
            
            log_limit_order_params("开仓", open_params, limit_price, calculated_stop_loss, "execute_intelligent_trade")
            log_perpetual_order_details('buy', position_size, 'limit', reduce_only=False, stop_loss_price=calculated_stop_loss)
            
            # 🆕 合并开仓提交日志
            logger.log_info(f"✅ 限价开多仓提交: {position_size}张 @ {limit_price:.2f}")

            # 创建限价开仓订单
            exchange.create_limit_order(
                TRADE_CONFIG.symbol,
                'buy',
                position_size,
                limit_price,
                params=open_params
            )

        elif signal_data['signal'] == 'SELL':
            # 检查是否有现有多头持仓，先平仓
            if current_position and current_position['side'] == 'long':
                # 🆕 合并平仓日志
                logger.log_info(f"🔄 平多仓开空仓: 平{current_position['size']}张，开{position_size}张")
                
                close_params = {
                    'reduceOnly': True,
                    'tag': order_tag
                }
                log_order_params("永续合约平仓", close_params, "execute_intelligent_trade")
                log_perpetual_order_details('sell', current_position['size'], 'market', reduce_only=True)
                
                exchange.create_market_order(
                    TRADE_CONFIG.symbol,
                    'sell',
                    current_position['size'],
                    params=close_params
                )
                time.sleep(1)

            # 使用限价单开空仓，同时设置止损
            open_params = {
                'tag': order_tag,
                'stopLoss': {
                    'triggerPrice': calculated_stop_loss,
                    'price': calculated_stop_loss,
                    'type': 'market'
                }
            }
            
            log_limit_order_params("开仓", open_params, limit_price, calculated_stop_loss, "execute_intelligent_trade")
            log_perpetual_order_details('sell', position_size, 'limit', reduce_only=False, stop_loss_price=calculated_stop_loss)
            
            # 🆕 合并开仓提交日志
            logger.log_info(f"✅ 限价开空仓提交: {position_size}张 @ {limit_price:.2f}")
            
            exchange.create_limit_order(
                TRADE_CONFIG.symbol,
                'sell',
                position_size,
                limit_price,
                params=open_params
            )

        elif signal_data['signal'] == 'HOLD':
            logger.log_info("建议观望，不执行交易")
            return

        # 🆕 合并订单提交成功日志
        logger.log_info("✅ 限价开仓订单提交成功")
        
        # 等待订单执行
        time.sleep(3)
        
        # 检查多级止盈
        actual_position = get_current_position()
        if actual_position:
            profit_taking_signal = position_manager.check_profit_taking(actual_position, price_data)
            if profit_taking_signal:
                # 🆕 合并止盈执行日志
                logger.log_info(f"🎯 执行多级止盈: {profit_taking_signal['description']} - 平仓{actual_position['size'] * profit_taking_signal['take_profit_ratio']:.2f}张")
                execute_profit_taking(actual_position, profit_taking_signal, price_data)
                position_manager.mark_level_executed(actual_position, profit_taking_signal['level'])

    except Exception as e:
        logger.log_error("trade_execution", str(e))
        
        # 如果限价单失败，尝试使用条件单
        logger.log_warning("⚠️ 限价单失败，尝试使用条件单...")
        try:
            if signal_data['signal'] == 'BUY':
                # 🆕 合并条件单日志
                logger.log_info(f"🔄 条件单开多仓: {position_size}张 @ {ask_price * 0.999:.2f}")
                
                result = create_algo_order(
                    inst_id=get_correct_inst_id(),
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
                    inst_id=get_correct_inst_id(),
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

def debug_algo_order_api():
    """调试算法订单API"""
    try:
        # 测试获取算法订单
        params = {'instType': 'SWAP', 'algoOrdType': 'conditional'}
        response = exchange.privateGetTradeOrdersAlgoPending(params)
        logger.log_info(f"🔍 算法订单API测试: {response}")
        
        # 测试合约ID
        inst_id = get_correct_inst_id()
        logger.log_info(f"🔍 正确合约ID: {inst_id}")
        
    except Exception as e:
        logger.log_error("api_debug", str(e))

def analyze_with_deepseek_with_retry(price_data, max_retries=TRADE_CONFIG.max_retries):
    """DeepSeek analysis with retry"""
    for attempt in range(max_retries):
        try:
            signal_data = analyze_with_deepseek(price_data)
            if signal_data and not signal_data.get('is_fallback', False):
                return signal_data

            logger.log_warning(f"Attempt {attempt + 1} failed, retrying...")
            time.sleep(1)

        except Exception as e:
            logger.log_error("DeepSeek analysis failed", str(e))
            if attempt == max_retries - 1:
                return create_fallback_signal(price_data)
            time.sleep(1)

    return create_fallback_signal(price_data)

def wait_for_next_period():
    """Wait until next 15-minute mark"""
    now = datetime.now()
    current_minute = now.minute
    current_second = now.second

    # Calculate next mark time (00, 15, 30, 45 minutes)
    next_period_minute = ((current_minute // 15) + 1) * 15
    if next_period_minute == 60:
        next_period_minute = 0

    # Calculate total seconds to wait
    if next_period_minute > current_minute:
        minutes_to_wait = next_period_minute - current_minute
    else:
        minutes_to_wait = 60 - current_minute + next_period_minute

    seconds_to_wait = minutes_to_wait * 60 - current_second

    # If the waiting time exceeds 10 minutes, reduce the waiting time to the next 5-minute interval.
    if seconds_to_wait > 600:  # 10 minutes
        logger.log_warning(f"🕒 Long wait detected ({seconds_to_wait}s), adjusting to shorter interval...")
        # Adjust to wait until the next 5-minute mark
        next_5min = ((current_minute // 5) + 1) * 5
        if next_5min == 60:
            next_5min = 0
        minutes_to_wait = next_5min - current_minute
        if minutes_to_wait < 0:
            minutes_to_wait += 60
        seconds_to_wait = minutes_to_wait * 60 - current_second

    # Display friendly waiting time
    display_minutes = int(seconds_to_wait // 60)
    display_seconds = int(seconds_to_wait % 60)

    if display_minutes > 0:
        logger.log_info(f"🕒 Waiting {display_minutes} minutes {display_seconds} seconds until mark...")
    else:
        logger.log_info(f"🕒 Waiting {display_seconds} seconds until mark...")

    return seconds_to_wait

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

def trading_bot():
    # Wait until mark before executing
    wait_seconds = wait_for_next_period()
    if wait_seconds > 0:
        time.sleep(wait_seconds)

    """Main trading bot function"""
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    logger.log_info(f"🔄 开始执行周期: {current_time}")

    # 1. Get enhanced K-line data
    price_data = get_btc_ohlcv_enhanced()
    if not price_data:
        return

    logger.log_info(f"💰 BTC价格: ${price_data['price']:,.2f} | 涨跌: {price_data['price_change']:+.2f}%")

    # 获取当前持仓（只获取一次，避免重复调用）
    current_position = get_current_position()

    # 检查当前持仓的多级止盈条件
    if current_position:
        profit_taking_signal = position_manager.check_profit_taking(current_position, price_data)
        if profit_taking_signal:
            logger.log_info(f"🎯 检测到止盈条件: {profit_taking_signal['description']}")
            execute_profit_taking(current_position, profit_taking_signal, price_data)
            position_manager.mark_level_executed(current_position, profit_taking_signal['level'])
            # 止盈后重新获取持仓状态
            current_position = get_current_position()

    # 检查当前持仓的止损状态（在获取价格数据后）
    if current_position:
        # 检查是否需要设置移动止损
        risk_config = TRADE_CONFIG.get_risk_config()
        trailing_config = risk_config['dynamic_stop_loss']
        
        if trailing_config['enable_trailing_stop']:
            setup_trailing_stop(
                current_position,
                trailing_config['trailing_activation_ratio'],
                trailing_config['trailing_distance_ratio'],
                price_data  # 添加价格数据参数
            )

    # 2. Use DeepSeek analysis (with retry)
    signal_data = analyze_with_deepseek_with_retry(price_data)

    # Filter signals
    signal_data = filter_signal(signal_data, price_data)

    if signal_data.get('is_fallback', False):
        logger.log_warning("⚠️ Using backup trading signal")

    # 3. Execute intelligent trading
    execute_intelligent_trade(signal_data, price_data)

def health_check():
    """Check the health of the system."""
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
        requests.get(TRADE_CONFIG.deepseek_base_url, timeout=5)
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

def close_position_due_to_trend_reversal(position, price_data, reason):
    """因趋势反转而平仓"""
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
            
            if not TRADE_CONFIG.test_mode:
                exchange.create_market_order(
                    TRADE_CONFIG.symbol,
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
            
            if not TRADE_CONFIG.test_mode:
                exchange.create_market_order(
                    TRADE_CONFIG.symbol,
                    'buy',
                    position_size,
                    params=close_params
                )
        
        logger.log_info("✅ 趋势反转平仓执行完成")
        return False  # 表示持仓已平
        
    except Exception as e:
        logger.log_error("trend_reversal_close", f"趋势反转平仓失败: {str(e)}")
        return True  # 平仓失败，保持持仓

def check_existing_stop_loss_orders_alternative(position):
    """备用方法检查止损单 - 通过持仓信息"""
    try:
        # 获取持仓信息，看是否有止损价格
        positions = exchange.fetch_positions([TRADE_CONFIG.symbol])
        
        for pos in positions:
            if pos['symbol'] == TRADE_CONFIG.symbol and float(pos.get('contracts', 0)) > 0:
                # 检查持仓中是否有止损价格信息
                if pos.get('stopLossPrice') or pos.get('liquidationPrice'):
                    stop_price = pos.get('stopLossPrice') or pos.get('liquidationPrice')
                    logger.log_info(f"✅ 通过持仓信息找到止损设置: {stop_price}")
                    return True
        
        return False
        
    except Exception as e:
        logger.log_error("alternative_stop_check", f"备用检查方法失败: {str(e)}")
        return False


def check_existing_stop_loss_simple(position):
    """简化检查 - 只检查基本订单状态"""
    try:
        # 获取最近订单记录
        logger.log_info("🔄 尝试使用fetch_open_orders检查...")
        open_orders = exchange.fetch_open_orders(TRADE_CONFIG.symbol)
        
        logger.log_info(f"📡 fetch_open_orders响应: 找到{len(open_orders)}个订单")
        
        for order in open_orders:
            # 记录订单详情
            logger.log_info(f"📋 订单详情: {order}")
            # 检查是否有未完成的止损相关订单
            if (order['status'] == 'open' and 
                ('stop' in order['type'] or 'stop' in order.get('id', '') or 
                 'stop' in str(order.get('info', {})).lower())):
                logger.log_info(f"✅ 通过订单记录找到止损单: {order['id']}")
                return True
        
        return False
    except Exception as e:
        logger.log_error("simple_stop_check", f"简化检查失败: {str(e)}")
        return True  # 保守处理

def check_existing_stop_loss_orders(position):
    """检查是否已有止损单 - 添加详细API日志"""
    try:
        # 使用更简单的参数，避免ordType错误
        params = {
            'instType': 'SWAP'
            # 移除algoOrdType参数，让API返回所有类型的算法订单
        }
        
        logger.log_info("🔍 检查现有算法订单...")
        
        # 记录原始请求参数
        logger.log_info(f"📡 API请求参数: {params}")
        logger.log_info(f"📡 API端点: /api/v5/trade/orders-algo-pending")
        logger.log_info(f"📡 请求方法: GET")
        
        # 执行API调用
        response = exchange.privateGetTradeOrdersAlgoPending(params)
        
        # 记录完整响应
        logger.log_info(f"📡 API完整响应: {response}")
        
        if response['code'] == '0':
            inst_id = get_correct_inst_id()
            found_orders = []
            
            for order in response.get('data', []):
                if order['instId'] == inst_id:
                    found_orders.append(order)
                    logger.log_info(f"📋 找到算法订单: {order}")
            
            # 根据持仓方向筛选
            for order in found_orders:
                if position['side'] == 'long' and order['side'] == 'sell':
                    # 多头持仓的止损单应该是卖出
                    trigger_price = order.get('slTriggerPx') or order.get('triggerPx') or '未知'
                    logger.log_info(f"✅ 匹配到多头止损单: {trigger_price}")
                    return True
                elif position['side'] == 'short' and order['side'] == 'buy':
                    # 空头持仓的止损单应该是买入
                    trigger_price = order.get('slTriggerPx') or order.get('triggerPx') or '未知'
                    logger.log_info(f"✅ 匹配到空头止损单: {trigger_price}")
                    return True
            
            logger.log_info(f"ℹ️ 找到{len(found_orders)}个算法订单，但无匹配的止损单")
            return False
        else:
            logger.log_warning(f"⚠️ 获取算法订单失败: {response.get('msg', '未知错误')}")
            # API调用失败时，保守返回True
            return True
            
    except Exception as e:
        error_msg = str(e)
        logger.log_error("check_existing_stop_loss", f"检查现有止损单失败: {error_msg}")
        
        # 根据错误类型决定是否保守处理
        if "Parameter ordType error" in error_msg:
            logger.log_warning("🔄 遇到参数错误，使用简化检查...")
            return check_existing_stop_loss_simple(position)
        else:
            # 其他错误时保守处理
            logger.log_warning("⚠️ 检查止损单失败，假设已有止损单")
            return True
        
def ensure_stop_loss_setting(position, price_data, strict=False):
    """确保持仓有止损设置 - 增强版本"""
    try:
        # 首先尝试主检查方法
        if check_existing_stop_loss_orders(position):
            logger.log_info("✅ 持仓已有止损单设置")
            return True
        
        # 主检查方法失败时，尝试备用方法
        if check_existing_stop_loss_orders_alternative(position):
            logger.log_info("✅ 通过备用方法确认有止损设置")
            return True
        
        # 如果两种方法都确认没有止损单，才进行设置
        logger.log_warning("⚠️ 确认持仓没有止损单，正在设置...")
        
        # 计算止损价格
        current_price = price_data['price']
        side = position['side']
        
        max_ratio = TRADE_CONFIG.get_risk_config()['stop_loss']['max_stop_loss_ratio']
        if strict:
            max_ratio = max_ratio * 0.5
            logger.log_info("🔒 严格止损模式: 使用更紧的止损距离")
        
        calculated_stop_loss = calculate_kline_based_stop_loss(
            side, 
            current_price, 
            price_data,
            max_ratio
        )
        
        # 设置止损
        if set_initial_stop_loss(
            'BUY' if side == 'long' else 'SELL', 
            position['size'], 
            calculated_stop_loss, 
            current_price
        ):
            stop_loss_ratio = abs(calculated_stop_loss - current_price) / current_price * 100
            logger.log_info(f"✅ 止损设置成功: 距离{stop_loss_ratio:.2f}%")
            return True
        else:
            logger.log_error("ensure_stop_loss", "止损设置失败")
            return False
            
    except Exception as e:
        logger.log_error("ensure_stop_loss_setting", f"确保止损设置失败: {str(e)}")
        # 出错时保守处理，假设已有止损
        return True

def is_trend_reversal_strong(position_side, signal_side, price_data, signal_data):
    """使用增强标准判断趋势是否强烈反转"""
    try:
        reversal_info = {
            'reversed': False,
            'strength': 'WEAK',
            'reason': ''
        }
        
        # 基础方向判断
        if position_side == 'long' and signal_side == 'SELL':
            direction_reversed = True
        elif position_side == 'short' and signal_side == 'BUY':
            direction_reversed = True
        else:
            direction_reversed = False
            
        if not direction_reversed:
            return reversal_info
        
        # 🆕 增强的技术指标确认
        tech = price_data['technical_data']
        confirmation_count = 0
        reasons = []
        
        # 1. RSI 背离确认
        rsi = tech.get('rsi', 50)
        if (position_side == 'long' and rsi > 70) or (position_side == 'short' and rsi < 30):
            confirmation_count += 1
            reasons.append("RSI in extreme zone")
        
        # 2. 移动平均线突破确认
        price = price_data['price']
        sma_20 = tech.get('sma_20', price)
        if (position_side == 'long' and price < sma_20) or (position_side == 'short' and price > sma_20):
            confirmation_count += 1
            reasons.append("Price crossed key moving average")
        
        # 3. MACD 信号确认
        macd = tech.get('macd', 0)
        macd_signal = tech.get('macd_signal', 0)
        if (position_side == 'long' and macd < macd_signal) or (position_side == 'short' and macd > macd_signal):
            confirmation_count += 1
            reasons.append("MACD shows reversal signal")
        
        # 4. 布林带位置确认
        bb_position = tech.get('bb_position', 0.5)
        if (position_side == 'long' and bb_position > 0.8) or (position_side == 'short' and bb_position < 0.2):
            confirmation_count += 1
            reasons.append("Price at Bollinger Band extreme")
        
        # 判断反转强度
        if confirmation_count >= 3:
            reversal_info.update({
                'reversed': True,
                'strength': 'STRONG',
                'reason': f"Strong reversal confirmed by {confirmation_count} indicators: {', '.join(reasons)}"
            })
        elif confirmation_count >= 2:
            reversal_info.update({
                'reversed': True,
                'strength': 'MEDIUM', 
                'reason': f"Medium reversal confirmed by {confirmation_count} indicators: {', '.join(reasons)}"
            })
        elif direction_reversed and signal_data.get('confidence') == 'HIGH':
            reversal_info.update({
                'reversed': True,
                'strength': 'MEDIUM',
                'reason': "Direction reversed with high confidence signal"
            })
            
        return reversal_info
        
    except Exception as e:
        logger.log_error("trend_reversal_analysis", f"趋势反转分析失败: {str(e)}")
        return {'reversed': False, 'strength': 'WEAK', 'reason': 'Analysis error'}

def analyze_existing_position_on_startup():
    """启动时分析现有持仓 - 优化版本"""
    try:
        current_position = get_current_position()
        if not current_position:
            logger.log_info("✅ 启动检查: 当前无持仓")
            return True
        
        logger.log_warning(f"🔍 启动检查: 发现现有持仓 - {current_position['side']} {current_position['size']}张")
        
        # 首先检查止损状态，但不强制设置
        has_stop_loss = check_existing_stop_loss_orders(current_position)
        
        if has_stop_loss:
            logger.log_info("✅ 持仓已有止损保护")
            # 即使有止损，也获取市场数据但不强制重新设置
            price_data = get_btc_ohlcv_enhanced()
            if price_data:
                # 只做趋势分析，不重新设置止损
                signal_data = analyze_with_deepseek_with_retry(price_data)
                if signal_data:
                    # 分析趋势但不强制平仓
                    position_side = current_position['side']
                    signal_side = signal_data['signal']
                    trend_reversed = is_trend_reversal_strong(position_side, signal_side, price_data, signal_data)
                    
                    if trend_reversed['reversed'] and trend_reversed['strength'] == 'STRONG':
                        logger.log_warning(f"🔄 检测到强烈趋势反转: {trend_reversed['reason']}")
                        return close_position_due_to_trend_reversal(current_position, price_data, trend_reversed['reason'])
            return True
        else:
            logger.log_warning("⚠️ 未检测到止损单，进行完整分析...")
            # 原有的完整分析逻辑
            price_data = get_btc_ohlcv_enhanced()
            if not price_data:
                logger.log_warning("⚠️ 无法获取市场数据，暂时保持现有持仓")
                return True
            
            signal_data = analyze_with_deepseek_with_retry(price_data)
            if not signal_data:
                logger.log_warning("⚠️ 无法获取分析信号，暂时保持现有持仓")
                return True
            
            # 分析趋势是否反转
            position_side = current_position['side']
            signal_side = signal_data['signal']
            
            logger.log_info(f"📊 持仓方向: {position_side}, 当前信号: {signal_side}")
            
            trend_reversed = is_trend_reversal_strong(position_side, signal_side, price_data, signal_data)
            
            if trend_reversed['reversed']:
                logger.log_warning(f"🔄 检测到趋势反转信号: {trend_reversed['reason']}")
                
                if trend_reversed['strength'] == 'STRONG':
                    logger.log_info("🎯 强烈反转信号，执行平仓")
                    return close_position_due_to_trend_reversal(current_position, price_data, trend_reversed['reason'])
                else:
                    logger.log_info("⚠️ 中等强度反转信号，设置止损继续观察")
                    ensure_stop_loss_setting(current_position, price_data, strict=True)
                    return True
            else:
                logger.log_info("✅ 趋势未反转，设置止损继续持有")
                ensure_stop_loss_setting(current_position, price_data)
                return True
                
    except Exception as e:
        logger.log_error("startup_position_analysis", f"启动持仓分析失败: {str(e)}")
        return True  # 出错时保持现状

def log_performance_metrics():
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
    logger.log_info("BTC/USDT OKX Automated Trading Bot Started!")
    
    # 添加API调试
    logger.log_info("🔍 Testing API connectivity...")
    debug_algo_order_api()

    # 🆕 先检查当前仓位模式
    current_mode = check_current_margin_mode()
    logger.log_info(f"🔍 Detected current margin mode: {current_mode}")
    
    # 🆕 配置验证和设置
    is_valid, errors, warnings = TRADE_CONFIG.validate_config()

    if not is_valid:
        logger.log_error("config_validation", "配置验证失败:")
        for error in errors:
            logger.log_error("config_error", f"  - {error}")
        logger.log_info("❌ 程序因配置错误而退出")
        return
    
    if warnings:
        logger.log_warning("配置警告:")
        for warning in warnings:
            logger.log_warning(f"  ⚠️ {warning}")
    

    # 记录配置摘要
        config_summary = f"""
            ✅ 交易所配置完成:
            - 合约: 1张 = {TRADE_CONFIG.contract_size} BTC
            - 最小交易: {TRADE_CONFIG.min_amount} 张
            - 目标保证金模式: {margin_mode}
            - 杠杆: {TRADE_CONFIG.leverage}x
            - USDT余额: {usdt_balance:.2f}
            - 当前保证金模式: {current_mode}
            """
        logger.log_info(config_summary)
        
    # 🆕 设置交易所（这里会设置逐仓模式）
    if not setup_exchange():
        logger.log_error("exchange_setup", "Initialization failed")
        return
    
    # 验证保证金模式设置
    if not verify_margin_mode():
        logger.log_warning("⚠️ 保证金模式验证失败，可能需要手动检查")

    # 🆕 启动时持仓分析 - 新增的关键步骤
    logger.log_info("🔍 执行启动时持仓分析...")
    position_handled = analyze_existing_position_on_startup()
    if not position_handled:
        logger.log_info("🔄 现有持仓已处理，等待下一次分析周期")
    else:
        logger.log_info("✅ 启动持仓分析完成")
    
    # 🆕 在健康检查前先获取一次数据
    logger.log_info("🔄 Initial data fetch...")
    initial_price_data = get_btc_ohlcv_enhanced()
    if initial_price_data:
        add_to_price_history(initial_price_data)
        logger.log_info("✅ Initial data fetched successfully")
    else:
        logger.log_warning("⚠️ Initial data fetch failed")
    
    consecutive_errors = 0
    TRADE_CONFIG.max_consecutive_errors = 5
    
    # Timing variables for different intervals
    last_health_check = time.time()  # 🆕 立即开始计时
    health_check_interval = TRADE_CONFIG.health_check_interval  # 300 seconds
    
    last_config_check = time.time()
    config_check_interval = TRADE_CONFIG.config_check_interval  # 300 seconds

    last_perf_log = time.time()
    perf_log_interval = TRADE_CONFIG.perf_log_interval  # 600 seconds

    while True:
        try:
            current_time = time.time()
            
            # Health check - every 5 minutes
            if current_time - last_health_check >= health_check_interval:
                logger.log_info("🔍 Running scheduled health check...")
                if not health_check():
                    consecutive_errors += 1
                    if consecutive_errors >= TRADE_CONFIG.max_consecutive_errors:
                        logger.log_info("🚨 Too many consecutive errors, exiting.")
                        break
                else:
                    consecutive_errors = 0
                
                last_health_check = current_time
            
            # Configuration reload check - every 5 minutes
            if current_time - last_config_check >= config_check_interval:
                if TRADE_CONFIG.should_reload():
                    TRADE_CONFIG.reload()  
                last_config_check = current_time

            # Run trading bot
            trading_bot()
            
            # Log performance
            log_performance_metrics()
            
            # Wait for next cycle
            time.sleep(60)
            
        except KeyboardInterrupt:
            logger.log_warning("\n🛑 User interrupted the program.")
            break
        except Exception as e:
            logger.log_error("main_loop", str(e))
            consecutive_errors += 1
            if consecutive_errors >= TRADE_CONFIG.max_consecutive_errors:
                logger.log_warning("🚨 Too many consecutive errors, exiting.")
                break
            time.sleep(60)

if __name__ == "__main__":
    main()
