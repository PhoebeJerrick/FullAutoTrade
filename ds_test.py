#!/usr/bin/env python3
"""
ds_perfect2.py - 止损止盈API测试专用程序
支持限价开仓时同步设置止损止损和止盈价格
"""

import os
import time
import sys
import json
import hmac
import hashlib
import base64
from datetime import datetime
from typing import Optional, Dict, Any
import ccxt
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from ccxt.base.exchange import Exchange  # 导入Exchange基类用于拦截请求

# 加载环境变量
env_path = '../ExApiConfig/ExApiConfig.env'
load_dotenv(dotenv_path=env_path)

# 简单的日志系统
class TestLogger:
    def __init__(self):
        self.log_file = f"stop_loss_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    
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

# 自定义交易所类，仅拦截create_order相关的请求和响应
class CustomOKX(ccxt.okx):
    def __init__(self, config):
        super().__init__(config)
    
    # 重写request方法，只记录create_order相关的请求
    def request(self, path, method='GET', params=None, headers=None, body=None):
        # 判断是否为创建订单的请求路径
        is_create_order = path.endswith('/order') and method == 'POST'
        
        if is_create_order:
            logger.debug("📤 原始请求:")
            logger.debug(f"   路径: {path}")
            logger.debug(f"   方法: {method}")
            logger.debug(f"   参数: {params}")
            logger.debug(f"   头部: {headers}")
            logger.debug(f"    body: {body}")
        
        # 执行原始请求
        response = super().request(path, method, params, headers, body)
        
        if is_create_order:
            logger.debug("📥 原始响应:")
            logger.debug(f"   响应数据: {response}")
        
        return response

# 交易配置
class TestConfig:
    def __init__(self):
        self.symbol = 'BTC/USDT:USDT'
        self.leverage = 3  # 低杠杆测试
        self.test_mode = False  # 真实交易
        self.margin_mode = 'isolated'
        self.base_usdt_amount = 10  # 小金额测试
        self.min_amount = 0.01  # 最小交易量
        self.stop_loss_percent = 0.1  # 止损百分比
        self.take_profit_percent = 0.2  # 止盈百分比
        self.order_price_offset = 0.01  # 限价单价格偏移比例（确保成交）
        self.max_retry = 3  # 开仓最大重试次数
        self.retry_delay = 3  # 重试延迟（秒）

# 账号配置
def get_account_config(account_name="default"):
    """根据账号名称获取对应的配置"""
    return {
        'api_key': os.getenv('OKX_API_KEY_2'),
        'secret': os.getenv('OKX_SECRET_2'),
        'password': os.getenv('OKX_PASSWORD_2')
    }

# 初始化交易所（使用自定义的OKX类）
account_config = get_account_config()
exchange = CustomOKX({
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
        
        # 设置杠杆 - 使用ccxt标准方法并传递正确参数
        leverage_params = {
            'instId': get_correct_inst_id(),  # OKX需要的合约ID
            'mgnMode': config.margin_mode     # 保证金模式
        }
        
        log_order_params("设置杠杆", leverage_params, "setup_exchange")
        
        # 使用ccxt标准方法设置杠杆，传递正确的参数
        response = exchange.set_leverage(
            config.leverage, 
            config.symbol,
            params=leverage_params
        )
        
        log_api_response(response, "setup_exchange")
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
    """计算仓位大小"""
    try:
        # 简单计算：使用基础USDT金额除以当前价格
        current_price = get_current_price()
        if current_price == 0:
            return config.min_amount
            
        # 计算合约数量
        contract_size = (config.base_usdt_amount * config.leverage) / current_price
        contract_size = round(contract_size, 2)  # 保留2位小数
        
        # 确保不低于最小交易量
        if contract_size < config.min_amount:
            contract_size = config.min_amount
            
        logger.info(f"📏 计算仓位大小: {contract_size} 张合约")
        return contract_size
        
    except Exception as e:
        logger.error(f"计算仓位大小失败: {str(e)}")
        return config.min_amount

def get_limit_order_price(side: str, current_price: float):
    """
    根据方向获取合适的限价单价格
    买单价格略高于当前价，卖单价格略低于当前价，确保快速成交
    """
    offset = current_price * config.order_price_offset / 100
    if side == 'buy':  # 做多时，买单价格略高
        return round(current_price + offset, 1)
    else:  # 做空时，卖单价格略低
        return round(current_price - offset, 1)

def create_limit_order_with_sl_tp(side: str, amount: float, price: float, 
                                stop_loss_price: float, take_profit_price: float):
    """创建带止损止盈的限价订单"""
    try:
        inst_id = get_correct_inst_id()
        
        # 构建基本参数，包含止损止盈（符合OKX API要求）
        params = {
            'tdMode': config.margin_mode,
            'instId': inst_id,
            'ordType': 'limit',  # 明确指定订单类型
            # 止损参数
            'slTriggerPx': str(round(stop_loss_price, 1)),
            'slOrdPx': '-1',  # 市价止损
            # 止盈参数
            'tpTriggerPx': str(round(take_profit_price, 1)),
            'tpOrdPx': '-1'   # 市价止盈
        }
        
        order_params = {
            'symbol': config.symbol,
            'side': side,
            'amount': amount,
            'type': 'limit',
            'price': price,
            'params': params
        }
        
        log_order_params("带止损止盈的限价开仓", order_params, "create_limit_order_with_sl_tp")
        
        logger.info(f"🎯 执行限价{side}开仓(带止损止盈): {amount} 张合约 @ {price:.1f}")
        logger.info(f"   止损价格: {stop_loss_price:.1f}")
        logger.info(f"   止盈价格: {take_profit_price:.1f}")
        
        order = exchange.create_order(
            config.symbol,
            'limit',
            side,
            amount,
            price,
            params
        )
        
        log_api_response(order, "create_limit_order_with_sl_tp")
        return order
            
    except Exception as e:
        logger.error(f"带止损止盈的限价开仓失败: {str(e)}")
        import traceback
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return None

def wait_for_order_fill(order_id, timeout=30):
    """等待订单成交"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            order = exchange.fetch_order(order_id, config.symbol)
            status = order.get('status')
            
            if status == 'closed':
                logger.info(f"✅ 订单 {order_id} 已完全成交")
                return True
            elif status in ['canceled', 'rejected']:
                logger.error(f"❌ 订单 {order_id} 被取消或拒绝: {status}")
                return False
                
            logger.info(f"⌛ 等待订单 {order_id} 成交... 当前状态: {status}")
            time.sleep(2)
            
        except Exception as e:
            logger.error(f"查询订单状态失败: {str(e)}")
            time.sleep(2)
    
    logger.warning(f"⏰ 订单 {order_id} 超时未成交，尝试取消并重新下单")
    # 超时未成交则取消订单
    try:
        exchange.cancel_order(order_id, config.symbol)
        logger.info(f"✅ 已取消超时订单 {order_id}")
    except Exception as e:
        logger.error(f"取消超时订单失败: {str(e)}")
    
    return False

def check_sl_tp_orders(position_id):
    """检查止损止盈订单是否设置成功"""
    try:
        logger.info("🔍 检查止损止盈订单状态...")
        
        # 查询当前所有条件订单
        params = {
            'instType': 'SWAP',
            'ordType': 'conditional'
        }
        
        response = exchange.private_get_trade_orders_algo(params)
        log_api_response(response, "check_sl_tp_orders")
        
        if response.get('code') != '0':
            logger.error(f"查询条件订单失败: {response.get('msg')}")
            return False
        
        # 查找与当前持仓相关的止损止盈订单
        sl_tp_orders = []
        for order in response.get('data', []):
            if order.get('posId') == position_id:
                sl_tp_orders.append(order)
        
        if not sl_tp_orders:
            logger.warning("⚠️ 未找到相关的止损止盈订单")
            return False
            
        # 检查每个止损止盈订单的状态
        all_active = True
        for order in sl_tp_orders:
            order_type = "止损" if order.get('slTriggerPx') else "止盈"
            status = order.get('state')
            
            logger.info(f"   {order_type}订单 {order.get('algoId')}: 状态={status}")
            
            if status != 'live':
                all_active = False
                logger.warning(f"   {order_type}订单 {order.get('algoId')} 未激活")
        
        if all_active:
            logger.info("✅ 所有止损止盈订单设置成功并激活")
            return True
        else:
            logger.warning("⚠️ 部分止损止盈订单未激活")
            return False
            
    except Exception as e:
        logger.error(f"检查止损止盈订单失败: {str(e)}")
        return False

def cancel_existing_algo_orders():
    """取消现有的算法订单"""
    try:
        logger.info("🔄 取消现有算法订单...")
        
        params = {
            'instType': 'SWAP',
            'ordType': 'conditional'
        }
        
        log_order_params("查询算法订单", params, "cancel_existing_algo_orders")
        
        # 使用ccxt的方法查询待处理订单
        pending_orders = exchange.fetch_open_orders(config.symbol)
        conditional_orders = [o for o in pending_orders if o.get('type') == 'conditional']
        
        if conditional_orders:
            for order in conditional_orders:
                logger.info(f"📋 发现条件单: {order['id']} - {order['side']} {order['amount']}")
                
                # 取消订单
                cancel_result = exchange.cancel_order(order['id'], config.symbol)
                if cancel_result:
                    logger.info(f"✅ 取消条件单成功: {order['id']}")
                else:
                    logger.warning(f"⚠️ 取消条件单失败: {order['id']}")
        else:
            logger.info("✅ 没有找到待取消的条件单")
                    
    except Exception as e:
        logger.error(f"取消算法订单失败: {str(e)}")

def get_current_position():
    """获取当前持仓，包含持仓ID"""
    try:
        positions = exchange.fetch_positions([config.symbol])
        if not positions:
            return None
        
        for pos in positions:
            if pos['symbol'] == config.symbol:
                contracts = float(pos['contracts']) if pos['contracts'] else 0
                if contracts > 0:
                    return {
                        'side': pos['side'],
                        'size': contracts,
                        'entry_price': float(pos['entryPrice']) if pos['entryPrice'] else 0,
                        'unrealized_pnl': float(pos['unrealizedPnl']) if pos['unrealizedPnl'] else 0,
                        'leverage': float(pos['leverage']) if pos['leverage'] else config.leverage,
                        'position_id': pos.get('id')  # 获取持仓ID，用于关联止损止盈订单
                    }
        return None
        
    except Exception as e:
        logger.error(f"获取持仓失败: {str(e)}")
        return None

def monitor_position_and_orders(timeout=60):
    """监控持仓和订单状态"""
    logger.info("🔍 开始监控持仓和订单状态...")
    
    start_time = time.time()
    position_closed = False
    order_triggered = False
    
    while time.time() - start_time < timeout:
        try:
            # 检查持仓
            position = get_current_position()
            if position:
                logger.info(f"📊 当前持仓: {position['side']} {position['size']}张, 入场价: {position['entry_price']:.1f}, 浮动盈亏: {position['unrealized_pnl']:.4f}")
            else:
                if not position_closed:
                    logger.info("✅ 持仓已平仓 - 止损或止盈可能已触发!")
                    position_closed = True
                    order_triggered = True
            
            # 检查待处理订单
            pending_orders = exchange.fetch_open_orders(config.symbol)
            conditional_orders = [o for o in pending_orders if o.get('type') in ['conditional', 'oco']]
            
            if conditional_orders:
                logger.info(f"📋 有待处理条件单: {len(conditional_orders)}个")
                for order in conditional_orders:
                    logger.info(f"   - {order['id']}: {order['side']} {order['amount']}")
            else:
                if not order_triggered and position_closed:
                    logger.info("✅ 条件单已全部处理完成")
                    order_triggered = True
            
            # 如果持仓已平且条件单已处理，结束监控
            if position_closed and order_triggered:
                logger.info("🎉 测试完成: 止损或止盈成功触发并平仓!")
                return True
                
            time.sleep(5)  # 每5秒检查一次
            
        except Exception as e:
            logger.error(f"监控过程中出错: {str(e)}")
            time.sleep(5)
    
    logger.warning("⏰ 监控超时，测试可能未完成")
    return False

def run_stop_loss_take_profit_test():
    """运行止损止盈测试"""
    logger.info("🚀 开始止损止盈API测试")
    logger.info("=" * 50)
    
    # 1. 设置交易所
    if not setup_exchange():
        logger.error("❌ 交易所设置失败，测试中止")
        return False
    
    # 2. 获取当前价格并计算止损止盈价格
    current_price = get_current_price()
    if current_price == 0:
        logger.error("❌ 无法获取当前价格，测试中止")
        return False
    
    # 设置开仓方向
    side = 'sell'  # 开空仓，可改为'buy'开多仓
    
    # 根据开仓方向计算止损和止盈价格
    if side == 'sell':  # 空头
        stop_loss_price = current_price * (1 + config.stop_loss_percent / 100)  # 止损价格（上方）
        take_profit_price = current_price * (1 - config.take_profit_percent / 100)  # 止盈价格（下方）
    else:  # 多头
        stop_loss_price = current_price * (1 - config.stop_loss_percent / 100)  # 止损价格（下方）
        take_profit_price = current_price * (1 + config.take_profit_percent / 100)  # 止盈价格（上方）
    
    logger.info(f"🎯 测试参数:")
    logger.info(f"   开仓方向: {side}")
    logger.info(f"   当前价格: {current_price:.2f}")
    logger.info(f"   止损价格: {stop_loss_price:.2f} (±{config.stop_loss_percent}%)")
    logger.info(f"   止盈价格: {take_profit_price:.2f} (±{config.take_profit_percent}%)")
    
    # 3. 计算仓位大小
    position_size = calculate_position_size()
    
    # 4. 取消现有条件单
    cancel_existing_algo_orders()
    
    # 5. 执行带止损止盈的限价开仓（带重试机制）
    logger.info("📝 执行带止损止盈的限价开仓...")
    order_result = None
    for retry in range(config.max_retry):
        # 获取最新价格并计算限价单价格
        current_price = get_current_price()
        if current_price == 0:
            logger.warning(f"⚠️ 重试 {retry+1}/{config.max_retry} - 无法获取当前价格，稍后重试")
            time.sleep(config.retry_delay)
            continue
            
        limit_price = get_limit_order_price(side, current_price)
        
        # 尝试开仓
        order_result = create_limit_order_with_sl_tp(
            side=side,
            amount=position_size,
            price=limit_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price
        )
        
        if order_result:
            # 等待订单成交
            order_id = order_result.get('id')
            if wait_for_order_fill(order_id):
                break  # 成交成功，退出重试循环
            else:
                logger.warning(f"⚠️ 重试 {retry+1}/{config.max_retry} - 订单未成交")
        else:
            logger.warning(f"⚠️ 重试 {retry+1}/{config.max_retry} - 订单创建失败")
            
        time.sleep(config.retry_delay)
    
    if not order_result:
        logger.error("❌ 所有开仓尝试均失败，测试中止")
        return False
    
    # 6. 检查开仓结果
    position = get_current_position()
    if not position:
        logger.error("❌ 开仓后未检测到持仓，测试中止")
        return False
    
    logger.info(f"✅ 开仓成功:")
    logger.info(f"   方向: {position['side']}")
    logger.info(f"   数量: {position['size']} 张")
    logger.info(f"   入场价: {position['entry_price']:.2f}")
    logger.info(f"   持仓ID: {position['position_id']}")
    
    # 7. 检查止损止盈是否设置成功
    sl_tp_success = check_sl_tp_orders(position['position_id'])
    if not sl_tp_success:
        logger.warning("⚠️ 止损止盈订单设置可能未成功，继续监控")
    
    # 8. 监控持仓和订单状态
    monitor_position_and_orders()
    
    logger.info("=" * 50)
    logger.info("🏁 止损止盈API测试结束")
    return True

if __name__ == "__main__":
    run_stop_loss_take_profit_test()