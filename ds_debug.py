#!/usr/bin/env python3

# ds_debug.py - 单项止盈止损测试-conditional
# 流程：
# 开始
# │
# ├─ 初始化环境
# │  ├─ 加载环境变量（API密钥等）
# │  ├─ 初始化日志系统（TestLogger）
# │  ├─ 加载交易配置（TestConfig：交易对、杠杆、保证金模式等）
# │  └─ 初始化交易所连接（ccxt.okx）
# │
# ├─ 交易所设置（setup_exchange）
# │  ├─ 获取市场信息（最小交易单位、精度等）
# │  ├─ 设置杠杆倍数
# │  └─ 查看USDT余额
# │
# ├─ 核心交易流程
# │  │
# │  ├─ 步骤1：开BTC空单（带止盈止损1%）
# │  │  ├─ 计算仓位大小（calculate_position_size）
# │  │  ├─ 获取当前价格（get_current_price）
# │  │  ├─ 计算止损/止盈价格（calculate_stop_loss_take_profit_prices）
# │  │  └─ 创建带止损止盈的空单（create_order_with_sl_tp）
# │  │
# │  ├─ 步骤2：10秒后限价平仓
# │  │  ├─ 等待10秒（time.sleep）
# │  │  └─ 市价平仓（close_position）
# │  │
# │  ├─ 步骤3：开BTC多单（无止损止盈）
# │  │  ├─ 计算仓位大小
# │  │  └─ 创建无止损止盈的多单（create_order_without_sl_tp）
# │  │
# │  ├─ 步骤4：检查仓位信息
# │  │  ├─ 获取当前持仓（get_current_position）
# │  │  └─ 确认无止损止盈（check_sl_tp_orders）
# │  │
# │  ├─ 步骤5：设置止盈（1%）
# │  │  └─ 调用set_take_profit_order
# │  │
# │  └─ 步骤6：设置止损（1%）
# │     └─ 调用set_stop_loss_order
# │
# 结束


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
        self.leverage = 5
        self.test_mode = False
        self.margin_mode = 'isolated'
        self.base_usdt_amount = 1
        self.min_contract_size = None  # 将在运行时从市场信息获取
        self.stop_loss_percent = 0.03
        self.take_profit_percent = 0.05
        self.price_offset_percent = 0.001
        self.wait_time_seconds = 10
        self.contract_size = 0.01

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

        # 先获取市场信息
        market_info = get_lot_size_info()
        min_amount = market_info['min_amount']
        logger.info(f"📊 最小交易单位: {min_amount}")
        
        # 更新配置
        config.min_contract_size = min_amount

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
    
def get_lot_size_info():
    """获取交易对的最小交易单位信息"""
    try:
        markets = exchange.load_markets()
        symbol = config.symbol
        
        if symbol in markets:
            market = markets[symbol]
            limits = market.get('limits', {})
            amount_limits = limits.get('amount', {})
            
            min_amount = amount_limits.get('min', config.min_contract_size)
            precision = market.get('precision', {}).get('amount', 4)
            
            logger.info(f"📊 市场交易量信息:")
            logger.info(f"   最小交易量: {min_amount}")
            logger.info(f"   数量精度: {precision}")
            
            return {
                'min_amount': min_amount,
                'precision': precision,
                'market_info': market
            }
        else:
            logger.warning(f"⚠️ 未找到交易对 {symbol} 的市场信息")
            return {
                'min_amount': config.min_contract_size,
                'precision': 4
            }
            
    except Exception as e:
        logger.error(f"获取市场信息失败: {str(e)}")
        return {
            'min_amount': config.min_contract_size,
            'precision': 4
        }

def adjust_position_size(calculated_size: float) -> float:
    """根据市场规则调整仓位大小"""
    try:
        market_info = get_lot_size_info()
        min_amount = market_info['min_amount']
        precision = market_info['precision']
        
        logger.info(f"📏 调整仓位大小:")
        logger.info(f"   计算大小: {calculated_size}")
        logger.info(f"   最小交易量: {min_amount}")
        logger.info(f"   精度: {precision}")
        
        # 确保不低于最小交易量
        if calculated_size < min_amount:
            adjusted_size = min_amount
            logger.info(f"   调整后: {adjusted_size} (使用最小值)")
        else:
            # 根据精度调整
            adjusted_size = round(calculated_size, precision)
            logger.info(f"   调整后: {adjusted_size}")
        
        # 验证是否为最小交易量的整数倍
        if min_amount > 0:
            multiple = adjusted_size / min_amount
            if not multiple.is_integer():
                # 如果不是整数倍，向下取整到最近的倍数
                adjusted_size = (int(multiple) * min_amount)
                logger.info(f"   最终调整: {adjusted_size} (lot size的整数倍)")
        
        return adjusted_size
        
    except Exception as e:
        logger.error(f"调整仓位大小失败: {str(e)}")
        return calculated_size

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
            
        # 根据市场规则调整大小
        contract_size = adjust_position_size(contract_size)
        
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

    # 确保价格精度正确（BTC通常是1位小数）
    stop_loss_price = round(stop_loss_price, 1)
    take_profit_price = round(take_profit_price, 1)
    
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

def close_position(side: str, amount: float, cancel_sl_tp=True):
    """
    平仓函数 - 增强版本，可选撤销止损止盈
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
            
            # 等待平仓成交
            if wait_for_order_fill(order_id, 30):
                # 平仓成交后再次确认撤销所有止损止盈
                logger.info("🔄 平仓成交后确认撤销止损止盈订单...")
                cancel_all_sl_tp_orders()
                return response
            else:
                logger.error(f"❌ 平仓订单未在30秒内成交")
                return None
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

def analyze_algo_order_type(order):
    """智能分析条件单类型"""
    algo_id = order.get('algoId', 'Unknown')
    
    # 判断订单类型（通过字段存在性判断）
    has_tp = order.get('tpTriggerPx') not in [None, '']
    has_sl = order.get('slTriggerPx') not in [None, '']
    
    if has_tp and has_sl:
        return "OCO"
    elif has_sl:
        return "止损"
    elif has_tp:
        return "止盈"
    else:
        # 进一步检查其他条件单类型
        ord_type = order.get('ordType', '')
        if ord_type == 'move_order_stop':
            return "移动止损"
        elif ord_type == 'iceberg':
            return "冰山订单"
        elif ord_type == 'twap':
            return "TWAP"
        else:
            return "其他条件单"

def check_sl_tp_orders():
    """检查止损止盈订单状态 - 修复版本，支持OCO和特定品种过滤"""
    try:
        inst_id = get_correct_inst_id()
        
        # 使用条件单查询API来检查止损止盈订单
        params = {
            'instType': 'SWAP',  # 永续合约
            'instId': inst_id,   # 只查询特定品种
            'ordType': 'conditional,oco',  # 条件单类型
        }
        
        logger.info(f"📋 查询 {inst_id} 的止损止盈条件单...")
        response = exchange.private_get_trade_orders_algo_pending(params)
        
        if response and response.get('code') == '0':
            orders = response.get('data', [])
            
            if orders:
                logger.info(f"✅ 发现止损止盈条件单: {len(orders)}个")
                
                # 分类显示订单
                sl_orders = []
                tp_orders = [] 
                oco_orders = []
                other_orders = []
                
                for order in orders:
                    # 判断订单类型（通过字段存在性判断）
                    has_tp = order.get('tpTriggerPx') not in [None, '']
                    has_sl = order.get('slTriggerPx') not in [None, '']
                    
                    if has_tp and has_sl:
                        oco_orders.append(order)
                    elif has_sl:
                        sl_orders.append(order)
                    elif has_tp:
                        tp_orders.append(order)
                    else:
                        other_orders.append(order)
                
                # 显示止损订单
                if sl_orders:
                    logger.info(f"   🛡️ 止损订单 ({len(sl_orders)}个):")
                    for order in sl_orders:
                        _log_algo_order_detail(order)
                
                # 显示止盈订单
                if tp_orders:
                    logger.info(f"   🎯 止盈订单 ({len(tp_orders)}个):")
                    for order in tp_orders:
                        _log_algo_order_detail(order)
                
                # 显示OCO订单
                if oco_orders:
                    logger.info(f"   🔄 OCO订单 ({len(oco_orders)}个):")
                    for order in oco_orders:
                        _log_algo_order_detail(order)
                
                # 显示其他类型订单
                if other_orders:
                    logger.info(f"   ❓ 其他条件单 ({len(other_orders)}个):")
                    for order in other_orders:
                        _log_algo_order_detail(order)
                
                return True
            else:
                logger.info(f"📋 未发现 {inst_id} 的止损止盈条件单")
                return False
        else:
            logger.warning(f"⚠️ 查询 {inst_id} 的止损止盈订单失败")
            return False
            
    except Exception as e:
        logger.error(f"检查止损止盈订单失败: {str(e)}")
        import traceback
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return False

def _log_algo_order_detail(order):
    """记录条件单详细信息 - 改进版本"""
    algo_id = order.get('algoId', 'Unknown')
    order_type = analyze_algo_order_type(order)
    state = order.get('state', 'Unknown')
    side = order.get('side', 'Unknown')
    pos_side = order.get('posSide', 'Unknown')
    sz = order.get('sz', 'Unknown')
    
    logger.info(f"      ID: {algo_id}")
    logger.info(f"       类型: {order_type}")
    logger.info(f"       状态: {state}")
    logger.info(f"       方向: {side}/{pos_side}")
    logger.info(f"       数量: {sz}")
    
    # 根据类型显示不同的价格信息
    if order_type == "OCO":
        logger.info(f"       止损触发: {order.get('slTriggerPx', 'Unknown')}, 委托: {order.get('slOrdPx', 'Unknown')}")
        logger.info(f"       止盈触发: {order.get('tpTriggerPx', 'Unknown')}, 委托: {order.get('tpOrdPx', 'Unknown')}")
    elif order_type == "止损":
        logger.info(f"       触发价: {order.get('slTriggerPx', 'Unknown')}")
        logger.info(f"       委托价: {order.get('slOrdPx', 'Unknown')}")
    elif order_type == "止盈":
        logger.info(f"       触发价: {order.get('tpTriggerPx', 'Unknown')}")
        logger.info(f"       委托价: {order.get('tpOrdPx', 'Unknown')}")
    else:
        logger.info(f"       触发价: {order.get('triggerPx', 'Unknown')}")
        logger.info(f"       委托价: {order.get('ordPx', 'Unknown')}")

def create_oco_order(side: str, amount: float, stop_loss_price: float, take_profit_price: float):
    """
    创建OCO订单（一个订单同时设置止损和止盈）
    """
    try:
        inst_id = get_correct_inst_id()
        
        # OCO订单参数
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': 'buy' if side == 'short' else 'sell',  # 平仓方向
            'ordType': 'oco',  # OCO订单类型
            'sz': str(amount),
            'tpTriggerPx': str(take_profit_price),
            'tpOrdPx': '-1',  # 市价止盈
            'slTriggerPx': str(stop_loss_price),
            'slOrdPx': '-1',  # 市价止损
        }
        
        log_order_params("OCO订单", params, "create_oco_order")
        logger.info(f"🔄 创建OCO订单: {side} {amount}张")
        logger.info(f"   止损: {stop_loss_price:.2f}")
        logger.info(f"   止盈: {take_profit_price:.2f}")
        
        response = exchange.private_post_trade_order_algo(params)
        
        log_api_response(response, "create_oco_order")
        
        if response and response.get('code') == '0':
            algo_id = response['data'][0]['algoId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ OCO订单创建成功: {algo_id}")
            return response
        else:
            logger.error(f"❌ OCO订单创建失败: {response}")
            return response
            
    except Exception as e:
        logger.error(f"创建OCO订单失败: {str(e)}")
        import traceback
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return None

def cancel_all_sl_tp_orders():
    """撤销所有止损止盈订单"""
    try:
        inst_id = get_correct_inst_id()
        
        logger.info(f"🔄 撤销 {inst_id} 的所有止损止盈订单...")
        
        # 获取所有待处理的条件单
        params = {
            'instType': 'SWAP',
            'instId': inst_id,
            'ordType': 'conditional,oco',
        }
        
        response = exchange.private_get_trade_orders_algo_pending(params)
        
        if response and response.get('code') == '0':
            orders = response.get('data', [])
            
            if not orders:
                logger.info(f"✅ 没有找到需要撤销的止损止盈订单")
                return True
            
            cancel_count = 0
            for order in orders:
                algo_id = order.get('algoId')
                if algo_id:
                    # 撤销单个条件单 - 使用正确的CCXT方法
                    cancel_params = [
                        {
                            'algoId': algo_id,
                            'instId': inst_id,
                        }
                    ]
                    
                    # 使用批量撤销条件单的API
                    cancel_response = exchange.private_post_trade_cancel_algos(cancel_params)
                    
                    if cancel_response and cancel_response.get('code') == '0':
                        logger.info(f"✅ 已撤销条件单: {algo_id}")
                        cancel_count += 1
                    else:
                        logger.error(f"❌ 撤销条件单失败: {algo_id} - {cancel_response}")
            
            logger.info(f"📊 总计撤销 {cancel_count}/{len(orders)} 个条件单")
            return cancel_count > 0
        else:
            logger.error(f"❌ 获取待撤销订单失败: {response}")
            return False
            
    except Exception as e:
        logger.error(f"撤销止损止盈订单失败: {str(e)}")
        import traceback
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return False

def cancel_specific_algo_order(algo_id: str):
    """撤销特定的条件单"""
    try:
        inst_id = get_correct_inst_id()
        
        # 使用批量撤销API，即使只有一个订单
        cancel_params = [
            {
                'algoId': algo_id,
                'instId': inst_id,
            }
        ]
        
        logger.info(f"🔄 撤销特定条件单: {algo_id}")
        
        # 使用批量撤销条件单的API
        response = exchange.private_post_trade_cancel_algos(cancel_params)
        
        if response and response.get('code') == '0':
            logger.info(f"✅ 条件单撤销成功: {algo_id}")
            return True
        else:
            logger.error(f"❌ 条件单撤销失败: {algo_id} - {response}")
            return False
            
    except Exception as e:
        logger.error(f"撤销特定条件单失败: {str(e)}")
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


def verify_sl_tp_setup(expected_sl_tp_count=2):
    """验证止损止盈设置是否正确 - 支持OCO和独立订单"""
    try:
        logger.info("🔍 验证止损止盈设置...")
        
        # 检查持仓
        position = get_current_position()
        if not position:
            logger.warning("⚠️ 无持仓，无法验证止损止盈")
            return False
        
        # 检查止损止盈订单
        has_sl_tp = check_sl_tp_orders()
        
        if has_sl_tp:
            logger.info("✅ 止损止盈验证通过 - 发现止损止盈订单")
            
            # 进一步验证订单数量（如果是独立订单）
            # 注意：如果是OCO订单，可能只有一个订单包含止损止盈
            return True
        else:
            logger.error("❌ 止损止盈验证失败 - 未发现止损止盈订单")
            return False
            
    except Exception as e:
        logger.error(f"验证止损止盈设置失败: {str(e)}")
        return False

def get_specific_algo_order(algo_id: str):
    """获取特定的条件单信息"""
    try:
        params = {
            'algoId': algo_id,
        }
        
        response = exchange.private_get_trade_order_algo(params)
        
        if response and response.get('code') == '0':
            orders = response.get('data', [])
            if orders:
                return orders[0]
        return None
        
    except Exception as e:
        logger.error(f"获取特定条件单失败: {str(e)}")
        return None



def verify_by_algo_history():
    """通过条件单历史记录验证"""
    try:
        inst_id = get_correct_inst_id()
        
        params = {
            'instType': 'SWAP',
            'ordType': 'conditional',
            'state': 'live',  # 存活状态
        }
        
        response = exchange.private_get_trade_orders_algo_pending(params)
        
        if response and response.get('code') == '0':
            orders = response.get('data', [])
            target_orders = [o for o in orders if o.get('instId') == inst_id]
            
            if target_orders:
                logger.info(f"📊 通过条件单历史找到 {len(target_orders)} 个活跃订单")
                for order in target_orders:
                    logger.info(f"   条件单: {order.get('algoId')} - {order.get('ordType')}")
            else:
                logger.info("📊 条件单历史中未找到相关订单")
                
    except Exception as e:
        logger.error(f"通过条件单历史验证失败: {str(e)}")

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
        
        # 先获取市场信息
        market_info = get_lot_size_info()
        min_amount = market_info['min_amount']
        
        # 尝试使用不同的订单大小，从最小交易量开始
        test_sizes = [min_amount, min_amount * 2, min_amount * 5]
        
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

def manage_sl_tp_orders():
    """止损止盈订单管理函数"""
    try:
        inst_id = get_correct_inst_id()
        
        # 获取当前持仓
        position = get_current_position()
        if not position:
            logger.info("📊 当前无持仓，检查是否需要清理止损止盈订单...")
            # 无持仓时撤销所有止损止盈订单
            return cancel_all_sl_tp_orders()
        
        # 有持仓时，检查止损止盈订单是否匹配
        logger.info(f"📊 当前持仓: {position['side']} {position['size']}张")
        
        # 获取所有止损止盈订单
        params = {
            'instType': 'SWAP',
            'instId': inst_id,
            'ordType': 'conditional,oco',
        }
        
        response = exchange.private_get_trade_orders_algo_pending(params)
        
        if response and response.get('code') == '0':
            orders = response.get('data', [])
            
            if not orders:
                logger.info("✅ 当前无止损止盈订单")
                return True
            
            # 检查订单是否与持仓匹配
            valid_orders = []
            invalid_orders = []
            
            for order in orders:
                order_side = order.get('side', '')
                order_size = float(order.get('sz', 0))
                
                # 判断订单方向是否与持仓匹配
                # 多头持仓：止损止盈应该是卖出
                # 空头持仓：止损止盈应该是买入
                if position['side'] == 'long' and order_side == 'sell':
                    valid_orders.append(order)
                elif position['side'] == 'short' and order_side == 'buy':
                    valid_orders.append(order)
                else:
                    invalid_orders.append(order)
            
            # 撤销不匹配的订单
            for order in invalid_orders:
                algo_id = order.get('algoId')
                logger.warning(f"⚠️ 发现不匹配的止损止盈订单，将撤销: {algo_id}")
                cancel_specific_algo_order(algo_id)
            
            logger.info(f"📊 止损止盈订单状态: {len(valid_orders)}个有效, {len(invalid_orders)}个无效")
            return True
            
        else:
            logger.error("❌ 获取止损止盈订单失败")
            return False
            
    except Exception as e:
        logger.error(f"止损止盈订单管理失败: {str(e)}")
        return False

def safe_close_position(side: str, amount: float):
    """
    安全平仓函数 - 确保平仓后止损止盈被撤销
    """
    logger.info(f"🔒 安全平仓: {side} {amount}张")
    
    # 步骤1: 撤销止损止盈订单
    logger.info("步骤1: 撤销止损止盈订单...")
    cancel_all_sl_tp_orders()
    
    # 步骤2: 执行平仓
    logger.info("步骤2: 执行平仓...")
    close_result = close_position(side, amount, cancel_sl_tp=False)  # 这里设为False因为我们已经撤销过了
    
    # 步骤3: 确认平仓后再次检查
    logger.info("步骤3: 确认平仓状态...")
    time.sleep(3)
    position_after = get_current_position()
    if position_after:
        logger.error(f"❌ 平仓后仍有持仓: {position_after}")
        return False
    
    # 步骤4: 最终确认无止损止盈订单
    logger.info("步骤4: 最终确认无止损止盈订单...")
    cancel_all_sl_tp_orders()
    
    return close_result is not None

def cleanup_after_test():
    """测试结束后的清理工作"""
    try:
        logger.info("🧹 测试结束，执行清理...")
        
        # 1. 检查并平掉所有持仓
        position = get_current_position()
        if position:
            logger.warning(f"⚠️ 测试结束发现未平持仓: {position}")
            logger.info("🔄 自动平仓...")
            safe_close_position(position['side'], position['size'])
        
        # 2. 撤销所有止损止盈订单
        logger.info("🔄 撤销所有止损止盈订单...")
        cancel_all_sl_tp_orders()
        
        # 3. 取消所有待处理订单
        logger.info("🔄 取消所有待处理订单...")
        cancel_existing_orders()
        
        logger.info("✅ 清理完成")
        return True
        
    except Exception as e:
        logger.error(f"清理失败: {str(e)}")
        return False

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
    
    # 平空单（自动撤销止损止盈）
    logger.info("🔄 执行空单平仓（将自动撤销止损止盈）...")
    close_result = close_position('short', short_position['size'], cancel_sl_tp=True)
    
    if not close_result:
        logger.error("❌ 空单平仓失败")
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
    
    # 立即验证止盈设置
    logger.info("🔍 验证止盈设置...")
    time.sleep(2)  # 等待系统处理
    has_tp = check_sl_tp_orders()
    if not has_tp:
        logger.error("❌ 止盈设置验证失败 - 未发现止盈订单")
        return False
    
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
    
    # 立即验证止损设置
    logger.info("🔍 验证止损设置...")
    time.sleep(2)  # 等待系统处理
    has_sl_tp = check_sl_tp_orders()
    if not has_sl_tp:
        logger.error("❌ 止损设置验证失败 - 未发现止损止盈订单")
        return False
    
    # 最终检查
    logger.info("")
    logger.info("🔹 最终状态检查")
    logger.info("-" * 40)
    
    # 最终验证止损止盈设置
    logger.info("📋 最终止损止盈订单状态:")
    final_verification = verify_sl_tp_setup()
    
    if not final_verification:
        logger.error("❌ 最终验证失败 - 止损止盈设置有问题")
        return False
    
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
        
        # 无论测试成功与否，都执行清理
        logger.info("")
        logger.info("🧹 执行测试后清理...")
        cleanup_after_test()
        
        if success:
            logger.info("🎊 所有测试完成!")
        else:
            logger.error("💥 测试失败!")
            
    except KeyboardInterrupt:
        logger.info("🛑 用户中断测试")
        cleanup_after_test()
    except Exception as e:
        logger.error(f"💥 测试程序异常: {str(e)}")
        cleanup_after_test()
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()