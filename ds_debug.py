#!/usr/bin/env python3
"""
ds_test.py - 改进的交易测试程序
流程：
1. 开1USDT保证金BTC空单，同时设置止盈止损(1%)
2. 10秒后限价平仓
3. 开1USDT保证金BTC多单
4. 检查仓位信息，确认无止损止盈
5. 设置止盈(1%)
6. 设置止损(1%)
"""

import os
import time
import sys
import json
import hmac
import hashlib
import base64
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
import ccxt
import pandas as pd
import numpy as np
from dotenv import load_dotenv

# 加载环境变量
env_path = '../ExApiConfig/ExApiConfig.env'
load_dotenv(dotenv_path=env_path)

# 简单的日志系统
class TestLogger:
    def __init__(self, log_dir="../Output/okxSub1", file_name="Enhanced_Test_{timestamp}.log"):
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.log_file = f"{log_dir}/{file_name.format(timestamp=timestamp)}"
        os.makedirs(log_dir, exist_ok=True)

    def log(self, level: str, message: str):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_entry = f"{timestamp} - {level} - {message}"
        print(log_entry)
        
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(log_entry + '\n')
    
    def info(self, message: str):
        self.log("INFO", message)
    
    def error(self, message: str):
        self.log("ERROR", message)
    
    def warning(self, message: str):
        self.log("WARNING", message)
    
    def debug(self, message: str):
        self.log("DEBUG", message)

logger = TestLogger()

# 交易配置
class TestConfig:
    def __init__(self):
        self.symbol = 'BTC/USDT:USDT'
        self.leverage = 5  # 杠杆
        self.test_mode = False  # 真实交易
        self.margin_mode = 'isolated'
        self.base_usdt_amount = 1  # 1 USDT保证金
        self.min_contract_size = 0.0001  # 最小0.0001张合约
        self.stop_loss_percent = 0.03  # 3% 止损
        self.take_profit_percent = 0.05  # 5% 止盈
        self.price_offset_percent = 0.001  # 限价单价格偏移
        self.wait_time_seconds = 10  # 等待10秒后平仓
        self.contract_size = 0.01  # BTC合约大小，1张=0.01 BTC

# 账号配置
def get_account_config(account_name="default"):
    """根据账号名称获取对应的配置"""
    return {
        'api_key': os.getenv('OKX_API_KEY_2'),
        'secret': os.getenv('OKX_SECRET_2'),
        'password': os.getenv('OKX_PASSWORD_2')
    }

# 初始化交易所
account_config = get_account_config()
exchange = ccxt.okx({
    'options': {
        'defaultType': 'swap',
    },
    'apiKey': account_config['api_key'],
    'secret': account_config['secret'],
    'password': account_config['password'],
})

config = TestConfig()

def log_order_params(order_type: str, params: Dict[str, Any], function_name: str = ""):
    """记录订单参数到日志"""
    try:
        # 隐藏敏感信息
        safe_params = params.copy()
        sensitive_keys = ['apiKey', 'secret', 'password', 'signature']
        for key in sensitive_keys:
            if key in safe_params:
                safe_params[key] = '***'
        
        logger.info(f"📋 {function_name} - {order_type}订单参数:")
        for key, value in safe_params.items():
            logger.info(f"   {key}: {value}")
            
    except Exception as e:
        logger.error(f"记录订单参数失败: {str(e)}")

def log_api_response(response: Any, function_name: str = ""):
    """记录API响应到日志"""
    try:
        logger.info(f"📡 {function_name} - API响应:")
        if isinstance(response, dict):
            for key, value in response.items():
                if key == 'data' and isinstance(value, list) and len(value) > 0:
                    logger.info(f"   {key}: [列表，共{len(value)}条记录]")
                    for i, item in enumerate(value[:3]):
                        logger.info(f"      [{i}]: {item}")
                else:
                    logger.info(f"   {key}: {value}")
        else:
            logger.info(f"   响应: {response}")
    except Exception as e:
        logger.error(f"记录API响应失败: {str(e)}")

def get_correct_inst_id():
    """获取正确的合约ID"""
    symbol = config.symbol
    if symbol == 'BTC/USDT:USDT':
        return 'BTC-USDT-SWAP'
    elif symbol == 'ETH/USDT:USDT':
        return 'ETH-USDT-SWAP'
    else:
        return symbol.replace('/', '-').replace(':USDT', '-SWAP')

def setup_exchange():
    """设置交易所参数"""
    try:
        logger.info("🔄 设置交易所参数...")
        
        # 设置杠杆
        leverage_params = {
            'symbol': config.symbol,
            'leverage': config.leverage
        }
        log_order_params("设置杠杆", leverage_params, "setup_exchange")
        
        exchange.set_leverage(config.leverage, config.symbol)
        logger.info(f"✅ 杠杆设置成功: {config.leverage}x")
        
        # 获取账户余额
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']
        logger.info(f"💰 USDT余额: {usdt_balance:.2f}")
        
        return True
        
    except Exception as e:
        logger.error(f"交易所设置失败: {str(e)}")
        return False

def get_current_price():
    """获取当前价格"""
    try:
        ticker = exchange.fetch_ticker(config.symbol)
        price = ticker['last']
        logger.info(f"📊 当前价格: {price:.2f}")
        return price
    except Exception as e:
        logger.error(f"获取价格失败: {str(e)}")
        return 0
    
def calculate_position_size():
    """计算仓位大小 - 精确计算最小可用仓位"""
    try:
        current_price = get_current_price()
        if current_price == 0:
            return config.min_contract_size
            
        # 计算需要的BTC数量
        required_btc = (config.base_usdt_amount * config.leverage) / current_price
        
        # 转换为合约张数
        contract_size = required_btc / config.contract_size
        
        # 确保不低于最小交易量
        if contract_size < config.min_contract_size:
            contract_size = config.min_contract_size
            
        # 根据精度调整（4位小数）
        contract_size = round(contract_size, 4)
        
        actual_btc = contract_size * config.contract_size
        logger.info(f"📏 仓位计算详情:")
        logger.info(f"   保证金: {config.base_usdt_amount} USDT")
        logger.info(f"   杠杆: {config.leverage}x")
        logger.info(f"   总价值: {config.base_usdt_amount * config.leverage} USDT")
        logger.info(f"   当前价格: {current_price:.2f} USDT")
        logger.info(f"   需要BTC: {required_btc:.8f} BTC")
        logger.info(f"   合约张数: {contract_size} 张")
        logger.info(f"   实际BTC: {actual_btc:.8f} BTC")
        
        return contract_size
        
    except Exception as e:
        logger.error(f"计算仓位大小失败: {str(e)}")
        return config.min_contract_size

def calculate_stop_loss_take_profit_prices(side: str, entry_price: float) -> Tuple[float, float]:
    """计算止损和止盈价格"""
    if side == 'long':  # 多头
        stop_loss_price = entry_price * (1 - config.stop_loss_percent)
        take_profit_price = entry_price * (1 + config.take_profit_percent)
    else:  # 空头
        stop_loss_price = entry_price * (1 + config.stop_loss_percent)
        take_profit_price = entry_price * (1 - config.take_profit_percent)
    
    logger.info(f"🎯 价格计算 - 入场: {entry_price:.2f}, 止损: {stop_loss_price:.2f}, 止盈: {take_profit_price:.2f}")
    return stop_loss_price, take_profit_price

def create_order_with_sl_tp(side: str, amount: float, order_type: str = 'market', 
                           limit_price: float = None, stop_loss_price: float = None, 
                           take_profit_price: float = None):
    """
    创建订单并同时设置止损止盈 - 使用OKX新的attachAlgoOrds API
    """
    try:
        inst_id = get_correct_inst_id()
        
        # 基础参数
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': side,
            'ordType': order_type,
            'sz': str(amount),
        }
        
        # 限价单需要价格参数
        if order_type == 'limit':
            if limit_price is None:
                logger.error("❌ 限价单必须提供limit_price参数")
                return None
            params['px'] = str(limit_price)
        
        # 添加止损止盈参数（如果提供了止损止盈价格）
        if stop_loss_price is not None and take_profit_price is not None:
            params['attachAlgoOrds'] = [
                {
                    'tpTriggerPx': str(take_profit_price),
                    'tpOrdPx': '-1',  # 市价止盈
                    'slTriggerPx': str(stop_loss_price),
                    'slOrdPx': '-1',  # 市价止损
                    'algoOrdType': 'conditional',  # 条件单类型
                    'sz': str(amount),  # 止损止盈数量与主订单相同
                    'side': 'buy' if side == 'short' else 'sell'  # 止损止盈方向与开仓方向相反
                }
            ]
        
        # 记录订单参数
        order_type_name = "市价单" if order_type == 'market' else "限价单"
        log_order_params(f"{order_type_name}带止损止盈", params, "create_order_with_sl_tp")
        
        # 记录订单详情
        if order_type == 'market':
            logger.info(f"🎯 执行市价{side}开仓: {amount} 张")
        else:
            logger.info(f"🎯 执行限价{side}开仓: {amount} 张 @ {limit_price:.2f}")
        
        if stop_loss_price is not None:
            logger.info(f"🛡️ 止损价格: {stop_loss_price:.2f}")
        if take_profit_price is not None:
            logger.info(f"🎯 止盈价格: {take_profit_price:.2f}")
        
        # 使用CCXT的私有API方法调用/trade/order接口
        response = exchange.private_post_trade_order(params)
        
        log_api_response(response, "create_order_with_sl_tp")
        
        if response and response.get('code') == '0':
            order_id = response['data'][0]['ordId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ {order_type_name}创建成功: {order_id}")
            return response
        else:
            logger.error(f"❌ {order_type_name}创建失败: {response}")
            return response
            
    except Exception as e:
        logger.error(f"{order_type_name}开仓失败: {str(e)}")
        import traceback
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return None

def create_order_without_sl_tp(side: str, amount: float, order_type: str = 'market', 
                              limit_price: float = None):
    """
    创建订单但不设置止损止盈
    """
    try:
        inst_id = get_correct_inst_id()
        
        # 基础参数
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': side,
            'ordType': order_type,
            'sz': str(amount),
        }
        
        # 限价单需要价格参数
        if order_type == 'limit':
            if limit_price is None:
                logger.error("❌ 限价单必须提供limit_price参数")
                return None
            params['px'] = str(limit_price)
        
        # 记录订单参数
        order_type_name = "市价单" if order_type == 'market' else "限价单"
        log_order_params(f"{order_type_name}无止损止盈", params, "create_order_without_sl_tp")
        
        # 记录订单详情
        if order_type == 'market':
            logger.info(f"🎯 执行市价{side}开仓: {amount} 张 (无止损止盈)")
        else:
            logger.info(f"🎯 执行限价{side}开仓: {amount} 张 @ {limit_price:.2f} (无止损止盈)")
        
        # 使用CCXT的私有API方法调用/trade/order接口
        response = exchange.private_post_trade_order(params)
        
        log_api_response(response, "create_order_without_sl_tp")
        
        if response and response.get('code') == '0':
            order_id = response['data'][0]['ordId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ {order_type_name}创建成功: {order_id}")
            return response
        else:
            logger.error(f"❌ {order_type_name}创建失败: {response}")
            return response
            
    except Exception as e:
        logger.error(f"{order_type_name}开仓失败: {str(e)}")
        import traceback
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return None

def close_position(side: str, amount: float):
    """
    平仓函数
    """
    try:
        inst_id = get_correct_inst_id()
        
        # 平仓方向与开仓方向相反
        close_side = 'buy' if side == 'short' else 'sell'
        
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': close_side,
            'ordType': 'market',  # 市价平仓
            'sz': str(amount),
        }
        
        log_order_params("市价平仓", params, "close_position")
        logger.info(f"🔄 执行{side}仓位平仓: {amount} 张")
        
        response = exchange.private_post_trade_order(params)
        
        log_api_response(response, "close_position")
        
        if response and response.get('code') == '0':
            order_id = response['data'][0]['ordId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ 平仓订单创建成功: {order_id}")
            return response
        else:
            logger.error(f"❌ 平仓订单创建失败: {response}")
            return response
            
    except Exception as e:
        logger.error(f"平仓失败: {str(e)}")
        import traceback
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return None

def set_take_profit_order(side: str, amount: float, trigger_price: float):
    """
    设置止盈订单
    """
    try:
        inst_id = get_correct_inst_id()
        
        # 止盈方向与开仓方向相反
        tp_side = 'buy' if side == 'short' else 'sell'
        
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': tp_side,
            'ordType': 'conditional',
            'sz': str(amount),
            'tpTriggerPx': str(trigger_price),
            'tpOrdPx': '-1',  # 市价止盈
        }
        
        log_order_params("设置止盈", params, "set_take_profit_order")
        logger.info(f"🎯 设置止盈: {trigger_price:.2f}, 方向: {tp_side}, 数量: {amount}")
        
        response = exchange.private_post_trade_order_algo(params)
        
        log_api_response(response, "set_take_profit_order")
        
        if response and response.get('code') == '0':
            algo_id = response['data'][0]['algoId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ 止盈订单设置成功: {algo_id}")
            return response
        else:
            logger.error(f"❌ 止盈订单设置失败: {response}")
            return response
            
    except Exception as e:
        logger.error(f"设置止盈失败: {str(e)}")
        import traceback
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return None

def set_stop_loss_order(side: str, amount: float, trigger_price: float):
    """
    设置止损订单
    """
    try:
        inst_id = get_correct_inst_id()
        
        # 止损方向与开仓方向相反
        sl_side = 'buy' if side == 'short' else 'sell'
        
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': sl_side,
            'ordType': 'conditional',
            'sz': str(amount),
            'slTriggerPx': str(trigger_price),
            'slOrdPx': '-1',  # 市价止损
        }
        
        log_order_params("设置止损", params, "set_stop_loss_order")
        logger.info(f"🛡️ 设置止损: {trigger_price:.2f}, 方向: {sl_side}, 数量: {amount}")
        
        response = exchange.private_post_trade_order_algo(params)
        
        log_api_response(response, "set_stop_loss_order")
        
        if response and response.get('code') == '0':
            algo_id = response['data'][0]['algoId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ 止损订单设置成功: {algo_id}")
            return response
        else:
            logger.error(f"❌ 止损订单设置失败: {response}")
            return response
            
    except Exception as e:
        logger.error(f"设置止损失败: {str(e)}")
        import traceback
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return None

def get_current_position():
    """获取当前持仓 - 改进版本"""
    try:
        # 使用CCXT的fetch_positions方法获取所有持仓
        positions = exchange.fetch_positions()
        
        if not positions:
            logger.info("📊 没有找到任何持仓")
            return None
        
        # 查找当前交易对的持仓
        target_symbol = config.symbol
        logger.info(f"📊 查找持仓: {target_symbol}")
        
        for pos in positions:
            symbol = pos.get('symbol', '')
            contracts = float(pos.get('contracts', 0))
            
            # 记录所有持仓信息用于调试
            logger.info(f"📊 持仓信息: 符号={symbol}, 合约数={contracts}, 方向={pos.get('side')}, 入场价={pos.get('entryPrice')}")
            
            # 检查是否为目标交易对且有持仓
            if symbol == target_symbol and contracts > 0:
                position_info = {
                    'side': pos.get('side', 'unknown'),
                    'size': contracts,
                    'entry_price': float(pos.get('entryPrice', 0)),
                    'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                    'leverage': float(pos.get('leverage', config.leverage))
                }
                logger.info(f"✅ 找到目标持仓: {position_info}")
                return position_info
        
        logger.info("❌ 未找到目标交易对的持仓")
        return None
        
    except Exception as e:
        logger.error(f"获取持仓失败: {str(e)}")
        return None

def check_sl_tp_orders():
    """检查止损止盈订单状态"""
    try:
        # 获取待处理订单
        pending_orders = exchange.fetch_open_orders(config.symbol)
        sl_tp_orders = [o for o in pending_orders if o.get('type') in ['stop', 'stop_limit', 'take_profit', 'take_profit_limit']]
        
        if sl_tp_orders:
            logger.info(f"📋 发现止损止盈订单: {len(sl_tp_orders)}个")
            for order in sl_tp_orders:
                logger.info(f"   - {order['id']}: {order['side']} {order['amount']} @ {order.get('price', '市价')}")
            return True
        else:
            logger.info("📋 未发现止损止盈订单")
            return False
            
    except Exception as e:
        logger.error(f"检查止损止盈订单失败: {str(e)}")
        return False

def cancel_existing_orders():
    """取消现有的订单"""
    try:
        logger.info("🔄 取消现有订单...")
        
        # 获取待处理订单
        pending_orders = exchange.fetch_open_orders(config.symbol)
        
        if pending_orders:
            for order in pending_orders:
                order_id = order.get('id')
                logger.info(f"📋 发现待处理订单: {order_id} - {order.get('side')} {order.get('amount')}")
                
                # 取消订单
                cancel_result = exchange.cancel_order(order_id, config.symbol)
                if cancel_result:
                    logger.info(f"✅ 取消订单成功: {order_id}")
                else:
                    logger.warning(f"⚠️ 取消订单失败: {order_id}")
        else:
            logger.info("✅ 没有找到待取消的订单")
                    
    except Exception as e:
        logger.error(f"取消订单失败: {str(e)}")

def wait_for_order_fill(order_id: str, timeout: int = 60) -> bool:
    """等待订单成交"""
    logger.info(f"⏳ 等待订单 {order_id} 成交...")
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            order = exchange.fetch_order(order_id, config.symbol)
            status = order['status']
            
            if status == 'closed':
                logger.info(f"✅ 订单已成交: {order_id}")
                return True
            elif status == 'canceled':
                logger.warning(f"❌ 订单已取消: {order_id}")
                return False
            else:
                logger.info(f"📊 订单状态: {status}, 等待中...")
                
            time.sleep(3)  # 每3秒检查一次
            
        except Exception as e:
            logger.error(f"检查订单状态失败: {str(e)}")
            time.sleep(3)
    
    logger.warning(f"⏰ 订单等待超时: {order_id}")
    return False

def wait_for_position(side: str, timeout: int = 30) -> Dict[str, Any]:
    """等待持仓出现"""
    logger.info(f"⏳ 等待{side}持仓出现...")
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        position = get_current_position()
        if position and position['side'] == side:
            logger.info(f"✅ {side}持仓已建立")
            return position
        time.sleep(2)  # 每2秒检查一次
    
    logger.error(f"❌ {side}持仓未在{timeout}秒内出现")
    return None

def get_market_info():
    """获取市场信息，包括最小交易量"""
    try:
        markets = exchange.load_markets()
        symbol = config.symbol
        if symbol in markets:
            market = markets[symbol]
            limits = market.get('limits', {})
            amount_limits = limits.get('amount', {})
            min_amount = amount_limits.get('min')
            precision = market.get('precision', {}).get('amount')
            
            logger.info(f"📊 市场信息 - 最小数量: {min_amount}")
            logger.info(f"📊 市场信息 - 数量精度: {precision}")
            logger.info(f"📊 市场信息 - 完整信息: {market}")
            
            return {
                'min_amount': min_amount,
                'precision': precision,
                'market_info': market
            }
        return None
    except Exception as e:
        logger.error(f"获取市场信息失败: {str(e)}")
        return None


def test_minimum_order():
    """测试最小订单大小"""
    try:
        logger.info("🧪 测试最小订单大小...")
        
        # 尝试使用不同的订单大小
        test_sizes = [0.0001, 0.0005, 0.001, 0.01]
        
        for size in test_sizes:
            logger.info(f"🧪 测试订单大小: {size} 张")
            
            # 尝试开一个小仓位
            order_result = create_order_without_sl_tp(
                side='buy',
                amount=size,
                order_type='market'
            )
            
            if order_result and order_result.get('code') == '0':
                logger.info(f"✅ 订单大小 {size} 张 - 成功")
                order_id = order_result['data'][0]['ordId']
                
                # 等待订单成交
                if wait_for_order_fill(order_id, 10):
                    # 检查持仓
                    position = get_current_position()
                    if position:
                        logger.info(f"📊 持仓建立: {position['size']} 张")
                        # 立即平仓
                        close_position('long', position['size'])
                        time.sleep(2)
                    break
                else:
                    logger.info(f"❌ 订单大小 {size} 张 - 成交失败")
            else:
                logger.info(f"❌ 订单大小 {size} 张 - 创建失败")
                
    except Exception as e:
        logger.error(f"最小订单测试失败: {str(e)}")

def run_enhanced_test():
    """运行增强测试流程"""
    logger.info("🚀 开始增强测试流程")
    logger.info("=" * 60)
    
    # 1. 设置交易所
    if not setup_exchange():
        logger.error("❌ 交易所设置失败，测试中止")
        return False
    
    # 2. 先测试最小订单大小
    logger.info("🧪 先测试最小订单大小...")
    test_minimum_order()
    
    # 3. 获取当前价格
    current_price = get_current_price()
    if current_price == 0:
        logger.error("❌ 无法获取当前价格，测试中止")
        return False
    
    logger.info(f"🎯 测试参数:")
    logger.info(f"   保证金: {config.base_usdt_amount} USDT")
    logger.info(f"   杠杆: {config.leverage}x")
    logger.info(f"   止损: {config.stop_loss_percent*100}%")
    logger.info(f"   止盈: {config.take_profit_percent*100}%")
    logger.info(f"   等待时间: {config.wait_time_seconds}秒")
    
    # 4. 计算仓位大小
    position_size = calculate_position_size()
    
    # 阶段1: 开空单同时设置止损止盈
    logger.info("")
    logger.info("🔹 阶段1: 开空单同时设置止损止盈")
    logger.info("-" * 40)
    
    # 计算止损止盈价格
    stop_loss_price, take_profit_price = calculate_stop_loss_take_profit_prices('sell', current_price)
    
    # 取消现有订单
    cancel_existing_orders()
    
    # 开空单同时设置止损止盈
    short_order_result = create_order_with_sl_tp(
        side='sell',
        amount=position_size,
        order_type='market',
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price
    )
    
    if not short_order_result or short_order_result.get('code') != '0':
        logger.error("❌ 空单开仓失败")
        return False
    
    short_order_id = short_order_result['data'][0]['ordId']
    
    # 等待空单成交
    if not wait_for_order_fill(short_order_id, 30):
        logger.error("❌ 空单未在30秒内成交")
        return False
    
    # 等待空单持仓出现
    short_position = wait_for_position('short', 30)
    if not short_position:
        logger.error("❌ 空单持仓未找到")
        return False
    
    logger.info(f"✅ 空单持仓建立: {short_position['size']}张, 入场价: {short_position['entry_price']:.2f}")
    
    # 检查止损止盈订单
    logger.info("📋 检查空单止损止盈订单...")
    check_sl_tp_orders()
    
    # 阶段2: 等待10秒后限价平仓
    logger.info("")
    logger.info("🔹 阶段2: 等待10秒后平仓")
    logger.info("-" * 40)
    
    logger.info(f"⏳ 等待 {config.wait_time_seconds} 秒...")
    for i in range(config.wait_time_seconds, 0, -1):
        logger.info(f"   {i}秒后平仓...")
        time.sleep(1)
    
    # 平空单
    logger.info("🔄 执行空单平仓...")
    close_result = close_position('short', short_position['size'])
    
    if not close_result or close_result.get('code') != '0':
        logger.error("❌ 空单平仓失败")
        return False
    
    close_order_id = close_result['data'][0]['ordId']
    
    # 等待平仓成交
    if not wait_for_order_fill(close_order_id, 30):
        logger.error("❌ 平仓订单未在30秒内成交")
        return False
    
    # 确认持仓已平
    time.sleep(3)  # 等待系统更新
    position_after_close = get_current_position()
    if position_after_close:
        logger.error(f"❌ 持仓未完全平仓，剩余: {position_after_close['size']}张")
        return False
    
    logger.info("✅ 空单平仓完成")
    
    # 阶段3: 开多单（无止损止盈）
    logger.info("")
    logger.info("🔹 阶段3: 开多单（无止损止盈）")
    logger.info("-" * 40)
    
    # 获取新的当前价格
    current_price = get_current_price()
    
    # 开多单（无止损止盈）
    long_order_result = create_order_without_sl_tp(
        side='buy',
        amount=position_size,
        order_type='market'
    )
    
    if not long_order_result or long_order_result.get('code') != '0':
        logger.error("❌ 多单开仓失败")
        return False
    
    long_order_id = long_order_result['data'][0]['ordId']
    
    # 等待多单成交
    if not wait_for_order_fill(long_order_id, 30):
        logger.error("❌ 多单未在30秒内成交")
        return False
    
    # 等待多单持仓出现
    long_position = wait_for_position('long', 30)
    if not long_position:
        logger.error("❌ 多单持仓未找到")
        return False
    
    logger.info(f"✅ 多单持仓建立: {long_position['size']}张, 入场价: {long_position['entry_price']:.2f}")
    
    # 阶段4: 检查仓位信息，确认无止损止盈
    logger.info("")
    logger.info("🔹 阶段4: 检查仓位止损止盈设置")
    logger.info("-" * 40)
    
    logger.info("📋 检查多单止损止盈订单...")
    has_sl_tp = check_sl_tp_orders()
    if has_sl_tp:
        logger.info("⚠️ 发现存在止损止盈订单，与预期不符")
        # 可以选择取消这些订单，但根据需求我们继续
    else:
        logger.info("✅ 确认未设置止损止盈，与预期一致")
    
    # 阶段5: 设置止盈
    logger.info("")
    logger.info("🔹 阶段5: 设置止盈(1%距离)")
    logger.info("-" * 40)
    
    _, take_profit_price = calculate_stop_loss_take_profit_prices('long', long_position['entry_price'])
    
    tp_result = set_take_profit_order(
        side='long',
        amount=long_position['size'],
        trigger_price=take_profit_price
    )
    
    if not tp_result or tp_result.get('code') != '0':
        logger.error("❌ 止盈设置失败")
        return False
    
    logger.info("✅ 止盈设置成功")
    
    # 阶段6: 设置止损
    logger.info("")
    logger.info("🔹 阶段6: 设置止损(1%距离)")
    logger.info("-" * 40)
    
    stop_loss_price, _ = calculate_stop_loss_take_profit_prices('long', long_position['entry_price'])
    
    sl_result = set_stop_loss_order(
        side='long',
        amount=long_position['size'],
        trigger_price=stop_loss_price
    )
    
    if not sl_result or sl_result.get('code') != '0':
        logger.error("❌ 止损设置失败")
        return False
    
    logger.info("✅ 止损设置成功")
    
    # 最终检查
    logger.info("")
    logger.info("🔹 最终状态检查")
    logger.info("-" * 40)
    
    # 检查最终持仓
    final_position = get_current_position()
    if final_position:
        logger.info(f"📊 最终持仓: {final_position['side']} {final_position['size']}张, 入场价: {final_position['entry_price']:.2f}")
    else:
        logger.info("📊 无持仓")
    
    # 检查最终止损止盈订单
    logger.info("📋 最终止损止盈订单状态:")
    check_sl_tp_orders()
    
    logger.info("")
    logger.info("🎉 增强测试流程完成!")
    logger.info("=" * 60)
    
    return True

def main():
    """主函数"""
    try:
        logger.info("=" * 60)
        logger.info("🔧 永续合约增强测试程序")
        logger.info("=" * 60)
        
        # 确认测试参数
        logger.info("📋 测试配置:")
        logger.info(f"   交易对: {config.symbol}")
        logger.info(f"   杠杆: {config.leverage}x")
        logger.info(f"   保证金模式: {config.margin_mode}")
        logger.info(f"   保证金金额: {config.base_usdt_amount} USDT")
        logger.info(f"   止损止盈距离: {config.stop_loss_percent*100}%")
        logger.info(f"   等待时间: {config.wait_time_seconds}秒")
        logger.info(f"   测试模式: {'是' if config.test_mode else '否'}")
        
        # 用户确认
        if not config.test_mode:
            logger.warning("⚠️ 注意: 这不是测试模式，将执行真实交易!")
            confirm = input("确认继续? (yes/no): ")
            if confirm.lower() != 'yes':
                logger.info("测试取消")
                return
        
        # 运行测试
        success = run_enhanced_test()
        
        if success:
            logger.info("🎊 所有测试完成!")
        else:
            logger.error("💥 测试失败!")
            
    except KeyboardInterrupt:
        logger.info("🛑 用户中断测试")
    except Exception as e:
        logger.error(f"💥 测试程序异常: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()