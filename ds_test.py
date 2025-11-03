#!/usr/bin/env python3
"""
ds_test.py - 限价单止损止盈API测试程序
修正止损止盈价格设置规则
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
    def __init__(self):
        self.log_file = f"limit_order_sl_tp_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    
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
        self.leverage = 3  # 低杠杆测试
        self.test_mode = False  # 真实交易
        self.margin_mode = 'isolated'
        self.base_usdt_amount = 10  # 小金额测试
        self.min_amount = 0.01  # 最小交易量
        self.stop_loss_percent = 0.005  # 0.5% 止损
        self.take_profit_percent = 0.01  # 1% 止盈
        self.price_offset_percent = 0.001  # 限价单价格偏移

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

def calculate_limit_price(side: str, current_price: float) -> float:
    """计算限价单价格"""
    if side == 'buy':
        # 买入限价单：价格低于当前价
        limit_price = current_price * (1 - config.price_offset_percent)
    else:
        # 卖出限价单：价格高于当前价
        limit_price = current_price * (1 + config.price_offset_percent)
    
    logger.info(f"🎯 限价单价格计算: {side} @ {limit_price:.2f} (当前价: {current_price:.2f})")
    return limit_price

def calculate_stop_loss_take_profit_prices(side: str, entry_price: float, current_price: float) -> Tuple[float, float]:
    """计算止损和止盈价格 - 修正版本"""
    if side == 'buy':  # 多头
        stop_loss_price = entry_price * (1 - config.stop_loss_percent)
        take_profit_price = entry_price * (1 + config.take_profit_percent)
        
        # 对于多头，止损必须低于当前价格，止盈必须高于当前价格
        if stop_loss_price >= current_price:
            stop_loss_price = current_price * 0.995  # 强制低于当前价
            logger.warning(f"🔄 调整止损价格至: {stop_loss_price:.2f} (原价格不符合规则)")
        
    else:  # 空头
        stop_loss_price = entry_price * (1 + config.stop_loss_percent)
        take_profit_price = entry_price * (1 - config.take_profit_percent)
        
        # 对于空头，止损必须高于当前价格，止盈必须低于当前价格
        if stop_loss_price <= current_price:
            stop_loss_price = current_price * 1.005  # 强制高于当前价
            logger.warning(f"🔄 调整止损价格至: {stop_loss_price:.2f} (原价格不符合规则)")
    
    logger.info(f"🎯 价格计算 - 入场: {entry_price:.2f}, 止损: {stop_loss_price:.2f}, 止盈: {take_profit_price:.2f}")
    return stop_loss_price, take_profit_price

def create_conditional_limit_order(side: str, amount: float, limit_price: float):
    """创建条件限价单（仅开仓）"""
    try:
        inst_id = get_correct_inst_id()
        
        # 创建条件限价单
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': side,
            'ordType': 'conditional',
            'sz': str(amount),
            'triggerPx': str(round(limit_price, 1)),
            'orderPx': str(round(limit_price, 1)),
        }
        
        log_order_params("条件限价单", params, "create_conditional_limit_order")
        
        logger.info(f"🎯 执行条件限价{side}开仓: {amount} 张 @ {limit_price:.2f}")
        
        response = exchange.private_post_trade_order_algo(params)
        
        log_api_response(response, "create_conditional_limit_order")
        
        if response and response.get('code') == '0':
            algo_id = response['data'][0]['algoId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ 条件限价单创建成功: {algo_id}")
            return response
        else:
            logger.error(f"❌ 条件限价单创建失败: {response}")
            return response
            
    except Exception as e:
        logger.error(f"条件限价单开仓失败: {str(e)}")
        import traceback
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return None

def create_stop_loss_order(side: str, amount: float, trigger_price: float):
    """创建止损订单"""
    try:
        # 确定止损方向（与开仓方向相反）
        stop_side = 'buy' if side == 'sell' else 'sell'
        inst_id = get_correct_inst_id()
        
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': stop_side,
            'ordType': 'conditional',
            'sz': str(amount),
            'slTriggerPx': str(round(trigger_price, 1)),
            'slOrdPx': '-1'  # 市价止损
        }
        
        # 根据方向调整参数名称
        if side == 'sell':  # 空头
            # 对于空头，使用tpTriggerPx作为止损（因为空头止损是价格上涨）
            params = {
                'instId': inst_id,
                'tdMode': config.margin_mode,
                'side': stop_side,
                'ordType': 'conditional',
                'sz': str(amount),
                'tpTriggerPx': str(round(trigger_price, 1)),  # 使用tpTriggerPx
                'tpOrdPx': '-1'
            }
        
        log_order_params("止损订单", params, "create_stop_loss_order")
        logger.info(f"🛡️ 设置止损: {stop_side} {amount}张 @ {trigger_price:.1f}")
        
        response = exchange.private_post_trade_order_algo(params)
        
        log_api_response(response, "create_stop_loss_order")
        
        if response and response.get('code') == '0':
            algo_id = response['data'][0]['algoId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ 止损订单创建成功: {algo_id}")
            return response
        else:
            logger.error(f"❌ 止损订单创建失败: {response}")
            return response
                
    except Exception as e:
        logger.error(f"创建止损订单异常: {str(e)}")
        return None

def create_take_profit_order(side: str, amount: float, trigger_price: float):
    """创建止盈订单"""
    try:
        # 确定止盈方向（与开仓方向相反）
        tp_side = 'buy' if side == 'sell' else 'sell'
        inst_id = get_correct_inst_id()
        
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': tp_side,
            'ordType': 'conditional',
            'sz': str(amount),
            'tpTriggerPx': str(round(trigger_price, 1)),
            'tpOrdPx': '-1'  # 市价止盈
        }
        
        # 根据方向调整参数名称
        if side == 'buy':  # 多头
            # 对于多头，使用slTriggerPx作为止盈（因为多头止盈是价格上涨）
            params = {
                'instId': inst_id,
                'tdMode': config.margin_mode,
                'side': tp_side,
                'ordType': 'conditional',
                'sz': str(amount),
                'slTriggerPx': str(round(trigger_price, 1)),  # 使用slTriggerPx
                'slOrdPx': '-1'
            }
        
        log_order_params("止盈订单", params, "create_take_profit_order")
        logger.info(f"🎯 设置止盈: {tp_side} {amount}张 @ {trigger_price:.1f}")
        
        response = exchange.private_post_trade_order_algo(params)
        
        log_api_response(response, "create_take_profit_order")
        
        if response and response.get('code') == '0':
            algo_id = response['data'][0]['algoId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ 止盈订单创建成功: {algo_id}")
            return response
        else:
            logger.error(f"❌ 止盈订单创建失败: {response}")
            return response
                
    except Exception as e:
        logger.error(f"创建止盈订单异常: {str(e)}")
        return None

def create_regular_limit_order(side: str, amount: float, limit_price: float):
    """创建普通限价单"""
    try:
        params = {
            'tdMode': config.margin_mode,
        }
        
        log_order_params("普通限价单", {
            'symbol': config.symbol,
            'side': side,
            'amount': amount,
            'price': limit_price,
            'type': 'limit',
            'params': params
        }, "create_regular_limit_order")
        
        logger.info(f"🎯 执行普通限价{side}开仓: {amount} 张 @ {limit_price:.2f}")
        
        order = exchange.create_order(
            config.symbol,
            'limit',
            side,
            amount,
            limit_price,
            params
        )
        
        log_api_response(order, "create_regular_limit_order")
        return order
            
    except Exception as e:
        logger.error(f"普通限价单开仓失败: {str(e)}")
        return None

def cancel_existing_algo_orders():
    """取消现有的算法订单"""
    try:
        logger.info("🔄 取消现有算法订单...")
        
        # 获取待处理算法订单
        algo_orders = get_algo_orders()
        
        if algo_orders:
            for order in algo_orders:
                algo_id = order.get('algoId')
                logger.info(f"📋 发现算法订单: {algo_id} - {order.get('side')} {order.get('sz')}")
                
                # 取消算法订单
                cancel_params = {
                    'instId': get_correct_inst_id(),
                    'algoId': algo_id
                }
                
                cancel_result = exchange.private_post_trade_cancel_algo_order(cancel_params)
                if cancel_result and cancel_result.get('code') == '0':
                    logger.info(f"✅ 取消算法订单成功: {algo_id}")
                else:
                    logger.warning(f"⚠️ 取消算法订单失败: {algo_id}")
        else:
            logger.info("✅ 没有找到待取消的算法订单")
                    
    except Exception as e:
        logger.error(f"取消算法订单失败: {str(e)}")

def get_algo_orders():
    """获取算法订单列表"""
    try:
        params = {
            'instType': 'SWAP',
            'ordType': 'conditional'
        }
        
        response = exchange.private_get_trade_orders_algo_pending(params)
        if response and response.get('code') == '0':
            return response.get('data', [])
        return []
    except Exception as e:
        logger.error(f"获取算法订单失败: {str(e)}")
        return []

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

def get_current_position():
    """获取当前持仓"""
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
                        'leverage': float(pos['leverage']) if pos['leverage'] else config.leverage
                    }
        return None
        
    except Exception as e:
        logger.error(f"获取持仓失败: {str(e)}")
        return None

def monitor_position_and_orders(timeout=300):
    """监控持仓和订单状态"""
    logger.info("🔍 开始监控持仓和订单状态...")
    
    start_time = time.time()
    position_created = False
    position_closed = False
    
    while time.time() - start_time < timeout:
        try:
            # 检查持仓
            position = get_current_position()
            if position and not position_created:
                logger.info(f"✅ 持仓建立: {position['side']} {position['size']}张, 入场价: {position['entry_price']:.1f}")
                position_created = True
            elif not position and position_created:
                logger.info("✅ 持仓已平仓 - 止损或止盈已触发!")
                position_closed = True
                break
            
            # 检查价格触发情况
            if position:
                current_price = get_current_price()
                stop_loss_price, take_profit_price = calculate_stop_loss_take_profit_prices(
                    position['side'], position['entry_price'], current_price
                )
                
                if position['side'] == 'buy':  # 多头
                    if current_price <= stop_loss_price:
                        logger.info("🛑 价格触及止损线!")
                    elif current_price >= take_profit_price:
                        logger.info("🎉 价格触及止盈线!")
                else:  # 空头
                    if current_price >= stop_loss_price:
                        logger.info("🛑 价格触及止损线!")
                    elif current_price <= take_profit_price:
                        logger.info("🎉 价格触及止盈线!")
            
            time.sleep(5)  # 每5秒检查一次
            
        except Exception as e:
            logger.error(f"监控过程中出错: {str(e)}")
            time.sleep(5)
    
    if position_closed:
        logger.info("🎉 测试完成: 止损或止盈触发!")
        return True
    else:
        logger.warning("⏰ 监控超时，测试可能未完成")
        return False

def run_limit_order_sl_tp_test():
    """运行限价单止损止盈测试"""
    logger.info("🚀 开始限价单止损止盈API测试")
    logger.info("=" * 50)
    
    # 1. 设置交易所
    if not setup_exchange():
        logger.error("❌ 交易所设置失败，测试中止")
        return False
    
    # 2. 获取当前价格
    current_price = get_current_price()
    if current_price == 0:
        logger.error("❌ 无法获取当前价格，测试中止")
        return False
    
    # 设置开仓方向为卖出（空头）
    side = 'sell'  # 开空仓
    
    logger.info(f"🎯 测试参数:")
    logger.info(f"   开仓方向: {side}")
    logger.info(f"   当前价格: {current_price:.2f}")
    logger.info(f"   止损比例: {config.stop_loss_percent*100}%")
    logger.info(f"   止盈比例: {config.take_profit_percent*100}%")
    
    # 3. 计算仓位大小
    position_size = calculate_position_size()
    
    # 4. 计算限价单价格
    limit_price = calculate_limit_price(side, current_price)
    
    # 5. 计算止损止盈价格（基于当前价格校验）
    stop_loss_price, take_profit_price = calculate_stop_loss_take_profit_prices(
        side, limit_price, current_price
    )
    
    # 6. 取消现有算法订单
    cancel_existing_algo_orders()
    
    # 7. 先创建普通限价单开仓
    logger.info("📝 创建普通限价单开仓...")
    order_result = create_regular_limit_order(
        side=side,
        amount=position_size,
        limit_price=limit_price
    )
    
    if not order_result:
        logger.error("❌ 限价单开仓失败")
        return False
    
    # 等待订单成交
    order_id = order_result.get('id')
    if order_id and wait_for_order_fill(order_id, timeout=120):
        logger.info("✅ 限价单成交成功")
    else:
        logger.warning("⚠️ 限价单未成交或等待超时")
        # 取消未成交订单
        if order_id:
            try:
                exchange.cancel_order(order_id, config.symbol)
                logger.info("✅ 已取消未成交订单")
            except Exception as e:
                logger.error(f"取消订单失败: {str(e)}")
        return False
    
    # 8. 开仓成功后设置止损止盈
    logger.info("🛡️ 设置止损和止盈订单...")
    
    # 获取实际入场价
    position = get_current_position()
    if not position:
        logger.error("❌ 开仓后未检测到持仓")
        return False
    
    actual_entry_price = position['entry_price']
    logger.info(f"📊 实际入场价: {actual_entry_price:.2f}")
    
    # 基于实际入场价重新计算止损止盈
    actual_stop_loss, actual_take_profit = calculate_stop_loss_take_profit_prices(
        side, actual_entry_price, current_price
    )
    
    # 设置止损订单
    stop_loss_result = create_stop_loss_order(side, position_size, actual_stop_loss)
    
    # 设置止盈订单
    take_profit_result = create_take_profit_order(side, position_size, actual_take_profit)
    
    success_count = 0
    if stop_loss_result and stop_loss_result.get('code') == '0':
        success_count += 1
        logger.info("✅ 止损订单设置成功")
    else:
        logger.error("❌ 止损订单设置失败")
    
    if take_profit_result and take_profit_result.get('code') == '0':
        success_count += 1
        logger.info("✅ 止盈订单设置成功")
    else:
        logger.error("❌ 止盈订单设置失败")
    
    if success_count < 2:
        logger.warning("⚠️ 部分订单设置失败，但继续监控...")
    
    logger.info("✅ 开始监控止损止盈...")
    
    # 9. 监控持仓和订单状态
    test_success = monitor_position_and_orders(timeout=300)  # 监控5分钟
    
    if test_success:
        logger.info("🎉 限价单止损止盈测试完全成功!")
        return True
    else:
        logger.warning("⚠️ 限价单止损止盈测试可能未完全成功")
        return False

def main():
    """主函数"""
    try:
        logger.info("=" * 60)
        logger.info("🔧 永续合约限价单止损止盈API测试程序")
        logger.info("=" * 60)
        
        # 确认测试参数
        logger.info("📋 测试配置:")
        logger.info(f"   交易对: {config.symbol}")
        logger.info(f"   杠杆: {config.leverage}x")
        logger.info(f"   保证金模式: {config.margin_mode}")
        logger.info(f"   测试金额: {config.base_usdt_amount} USDT")
        logger.info(f"   止损比例: {config.stop_loss_percent*100}%")
        logger.info(f"   止盈比例: {config.take_profit_percent*100}%")
        logger.info(f"   价格偏移: {config.price_offset_percent*100}%")
        logger.info(f"   测试模式: {'是' if config.test_mode else '否'}")
        
        # 用户确认
        if not config.test_mode:
            logger.warning("⚠️ 注意: 这不是测试模式，将执行真实交易!")
            confirm = input("确认继续? (yes/no): ")
            if confirm.lower() != 'yes':
                logger.info("测试取消")
                return
        
        # 运行测试
        success = run_limit_order_sl_tp_test()
        
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