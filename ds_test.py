#!/usr/bin/env python3
"""
ds_perfect2.py - 止损API测试专用程序
专门用于测试永续合约开仓和止损设置功能
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

# 交易配置
class TestConfig:
    def __init__(self):
        self.symbol = 'BTC/USDT:USDT'
        self.leverage = 3  # 低杠杆测试
        self.test_mode = False
        self.margin_mode = 'isolated'
        self.base_usdt_amount = 10  # 小金额测试
        self.min_amount = 0.01  # 最小交易量

# 账号配置
def get_account_config(account_name="default"):
    """根据账号名称获取对应的配置"""
    if account_name == "okxMain":
        return {
            'api_key': os.getenv('OKX_API_KEY_1') or os.getenv('OKX_API_KEY'),
            'secret': os.getenv('OKX_SECRET_1') or os.getenv('OKX_SECRET'),
            'password': os.getenv('OKX_PASSWORD_1') or os.getenv('OKX_PASSWORD')
        }
    else:  # default
        return {
            'api_key': os.getenv('OKX_API_KEY'),
            'secret': os.getenv('OKX_SECRET'),
            'password': os.getenv('OKX_PASSWORD')
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

def create_market_order(side: str, amount: float):
    """创建市价订单"""
    try:
        order_params = {
            'symbol': config.symbol,
            'side': side,
            'amount': amount,
            'type': 'market',
            'params': {
                'tdMode': config.margin_mode
            }
        }
        
        log_order_params("市价开仓", order_params, "create_market_order")
        
        logger.info(f"🎯 执行市价{side}开仓: {amount} 张合约")
        
        if config.test_mode:
            logger.info("🔶 测试模式 - 模拟开仓成功")
            return {'id': 'test_order_123', 'status': 'closed'}
        else:
            order = exchange.create_order(
                config.symbol,
                'market',
                side,
                amount,
                None,
                {'tdMode': config.margin_mode}
            )
            
            log_api_response(order, "create_market_order")
            return order
            
    except Exception as e:
        logger.error(f"市价开仓失败: {str(e)}")
        return None

def cancel_existing_algo_orders():
    """取消现有的算法订单"""
    try:
        logger.info("🔄 取消现有算法订单...")
        
        params = {
            'instType': 'SWAP',
            'algoOrdType': 'conditional'
        }
        
        log_order_params("查询算法订单", params, "cancel_existing_algo_orders")
        
        response = exchange.privateGetTradeOrdersAlgoPending(params)
        
        log_api_response(response, "查询算法订单")
        
        if response['code'] == '0' and response['data']:
            for order in response['data']:
                cancel_params = {
                    'instId': order.get('instId', ''),
                    'algoId': order['algoId'],
                    'algoOrdType': 'conditional'
                }
                
                log_order_params("取消算法订单", cancel_params, "cancel_existing_algo_orders")
                
                cancel_response = exchange.privatePostTradeCancelAlgoOrder(cancel_params)
                
                log_api_response(cancel_response, "取消算法订单")
                
                if cancel_response['code'] == '0':
                    logger.info(f"✅ 取消现有条件单: {order['algoId']}")
                else:
                    logger.warning(f"⚠️ 取消条件单失败: {cancel_response}")
        else:
            logger.info("✅ 没有找到待取消的条件单")
                    
    except Exception as e:
        logger.error(f"取消算法订单失败: {str(e)}")

def create_stop_loss_order(side: str, amount: float, trigger_price: float, entry_price: float):
    """创建止损订单"""
    try:
        # 确定止损方向（与开仓方向相反）
        stop_side = 'buy' if side == 'sell' else 'sell'
        
        # 计算止损距离
        stop_distance = abs(trigger_price - entry_price)
        stop_percentage = (stop_distance / entry_price) * 100
        
        inst_id = get_correct_inst_id()
        
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'algoOrdType': 'conditional',
            'side': stop_side.upper(),
            'sz': str(amount),
            'tpTriggerPx': str(round(trigger_price, 1)),
            'tpOrdPx': '-1',
            'posSide': 'net',
            'ordType': 'market'
        }
        
        log_order_params("止损订单", params, "create_stop_loss_order")
        logger.info(f"🛡️ 设置止损: {stop_side.upper()} {amount}张 @ {trigger_price:.1f}")
        logger.info(f"📏 止损距离: {stop_distance:.1f} ({stop_percentage:.3f}%)")
        
        if config.test_mode:
            logger.info("🔶 测试模式 - 模拟止损设置成功")
            return {'code': '0', 'data': [{'algoId': 'test_stop_123'}]}
        else:
            response = exchange.privatePostTradeOrderAlgo(params)
            
            log_api_response(response, "create_stop_loss_order")
            
            if response['code'] == '0':
                algo_id = response['data'][0]['algoId']
                logger.info(f"✅ 止损订单创建成功: {algo_id}")
                return response
            else:
                logger.error(f"❌ 止损订单创建失败: {response}")
                return response
                
    except Exception as e:
        logger.error(f"创建止损订单异常: {str(e)}")
        return None

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

def monitor_position_and_orders(timeout=60):
    """监控持仓和订单状态"""
    logger.info("🔍 开始监控持仓和订单状态...")
    
    start_time = time.time()
    position_closed = False
    stop_triggered = False
    
    while time.time() - start_time < timeout:
        try:
            # 检查持仓
            position = get_current_position()
            if position:
                logger.info(f"📊 当前持仓: {position['side']} {position['size']}张, 入场价: {position['entry_price']:.1f}, 浮动盈亏: {position['unrealized_pnl']:.4f}")
            else:
                if not position_closed:
                    logger.info("✅ 持仓已平仓 - 止损可能已触发!")
                    position_closed = True
                    stop_triggered = True
            
            # 检查条件单状态
            params = {'instType': 'SWAP', 'algoOrdType': 'conditional'}
            response = exchange.privateGetTradeOrdersAlgoPending(params)
            
            if response['code'] == '0':
                pending_orders = response.get('data', [])
                if pending_orders:
                    logger.info(f"📋 有待处理条件单: {len(pending_orders)}个")
                    for order in pending_orders:
                        logger.info(f"   - {order['algoId']}: {order['side']} {order['sz']} @ {order.get('tpTriggerPx', 'N/A')}")
                else:
                    if not stop_triggered and position_closed:
                        logger.info("✅ 条件单已全部处理完成")
                        stop_triggered = True
            
            # 如果持仓已平且条件单已处理，结束监控
            if position_closed and stop_triggered:
                logger.info("🎉 测试完成: 止损成功触发并平仓!")
                return True
                
            time.sleep(5)  # 每5秒检查一次
            
        except Exception as e:
            logger.error(f"监控过程中出错: {str(e)}")
            time.sleep(5)
    
    logger.warning("⏰ 监控超时，测试可能未完成")
    return False

def run_stop_loss_test():
    """运行止损测试"""
    logger.info("🚀 开始止损API测试")
    logger.info("=" * 50)
    
    # 1. 设置交易所
    if not setup_exchange():
        logger.error("❌ 交易所设置失败，测试中止")
        return False
    
    # 2. 获取当前价格并计算止损价格
    current_price = get_current_price()
    if current_price == 0:
        logger.error("❌ 无法获取当前价格，测试中止")
        return False
    
    # 设置开仓方向为卖出（空头），这样止损可以设置在略高于当前价格的位置
    # 这样如果价格稍有上涨就会触发止损
    side = 'sell'  # 开空仓
    stop_loss_price = current_price * 1.001  # 当前价格上方0.1%
    
    logger.info(f"🎯 测试参数:")
    logger.info(f"   开仓方向: {side}")
    logger.info(f"   当前价格: {current_price:.2f}")
    logger.info(f"   止损价格: {stop_loss_price:.2f}")
    logger.info(f"   止损距离: {abs(stop_loss_price - current_price):.2f}")
    
    # 3. 计算仓位大小
    position_size = calculate_position_size()
    
    # 4. 取消现有条件单
    cancel_existing_algo_orders()
    
    # 5. 执行市价开仓
    logger.info("📝 执行市价开仓...")
    order_result = create_market_order(side, position_size)
    
    if not order_result:
        logger.error("❌ 开仓失败，测试中止")
        return False
    
    # 等待订单执行
    time.sleep(3)
    
    # 6. 检查开仓结果
    position = get_current_position()
    if not position:
        logger.error("❌ 开仓后未检测到持仓，测试中止")
        return False
    
    logger.info(f"✅ 开仓成功:")
    logger.info(f"   方向: {position['side']}")
    logger.info(f"   数量: {position['size']} 张")
    logger.info(f"   入场价: {position['entry_price']:.2f}")
    
    # 7. 设置止损订单
    logger.info("🛡️ 设置止损订单...")
    stop_loss_result = create_stop_loss_order(
        side=side,
        amount=position_size,
        trigger_price=stop_loss_price,
        entry_price=position['entry_price']
    )
    
    if not stop_loss_result or stop_loss_result.get('code') != '0':
        logger.error("❌ 止损订单设置失败")
        
        # 尝试平仓
        logger.info("🔄 尝试平仓...")
        close_side = 'buy' if side == 'sell' else 'sell'
        close_order = create_market_order(close_side, position_size)
        
        if close_order:
            logger.info("✅ 手动平仓成功")
        else:
            logger.error("❌ 手动平仓失败")
            
        return False
    
    logger.info("✅ 止损订单设置成功，开始监控...")
    
    # 8. 监控持仓和订单状态
    test_success = monitor_position_and_orders(timeout=120)  # 监控2分钟
    
    if test_success:
        logger.info("🎉 止损测试完全成功!")
        return True
    else:
        logger.warning("⚠️ 止损测试可能未完全成功")
        return False

def main():
    """主函数"""
    try:
        logger.info("=" * 60)
        logger.info("🔧 永续合约止损API测试程序")
        logger.info("=" * 60)
        
        # 确认测试参数
        logger.info("📋 测试配置:")
        logger.info(f"   交易对: {config.symbol}")
        logger.info(f"   杠杆: {config.leverage}x")
        logger.info(f"   保证金模式: {config.margin_mode}")
        logger.info(f"   测试金额: {config.base_usdt_amount} USDT")
        logger.info(f"   测试模式: {'是' if config.test_mode else '否'}")
        
        # 用户确认
        if not config.test_mode:
            logger.warning("⚠️ 注意: 这不是测试模式，将执行真实交易!")
            confirm = input("确认继续? (yes/no): ")
            if confirm.lower() != 'yes':
                logger.info("测试取消")
                return
        
        # 运行测试
        success = run_stop_loss_test()
        
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