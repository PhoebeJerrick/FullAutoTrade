#!/usr/bin/env python3

# ds_short_sl_tp_test.py - BTC空单止盈止损测试程序
# 流程：
# 1. 开BTC空单并附带止盈止损
# 2. 确认止盈止损正确设置
# 3. 等待5秒
# 4. 限价平仓
# 5. 确认仓位已平
# 6. 检查止盈止损是否还在，如果还在则撤销

import os
import time
import sys
from datetime import datetime
from typing import Dict, Any, Optional
import ccxt
from dotenv import load_dotenv

# 加载环境变量
env_path = '../ExApiConfig/ExApiConfig.env'
load_dotenv(dotenv_path=env_path)

# 复用原有的日志系统
class TestLogger:
    def __init__(self, log_dir="../Output/short_sl_tp_test", file_name="Short_SL_TP_Test_{timestamp}.log"):
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
        self.leverage = 3  # 使用较低杠杆以降低风险
        self.test_mode = False  # 设置为True进行模拟测试
        self.margin_mode = 'isolated'
        self.base_usdt_amount = 5  # 使用5USDT保证金
        self.min_contract_size = None
        self.stop_loss_percent = 0.01  # 1%止损
        self.take_profit_percent = 0.01  # 1%止盈
        self.wait_time_seconds = 5  # 等待5秒
        self.contract_size = 0.01

# 账号配置
def get_account_config():
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
        current_price = get_current_price()
        if current_price == 0:
            return 0.01  # 默认最小仓位
            
        # 计算需要的BTC数量
        required_btc = (config.base_usdt_amount * config.leverage) / current_price
        
        # 转换为合约张数
        contract_size = required_btc / config.contract_size
        
        # 确保合理的仓位大小
        contract_size = max(0.01, min(contract_size, 0.1))  # 限制在0.01-0.1张之间
        
        logger.info(f"📏 仓位计算: {contract_size:.4f} 张")
        return contract_size
        
    except Exception as e:
        logger.error(f"计算仓位大小失败: {str(e)}")
        return 0.01

def calculate_stop_loss_take_profit_prices(side: str, entry_price: float):
    """计算止损和止盈价格"""
    if side == 'short':  # 空头
        stop_loss_price = entry_price * (1 + config.stop_loss_percent)
        take_profit_price = entry_price * (1 - config.take_profit_percent)
    
    logger.info(f"🎯 价格计算 - 入场: {entry_price:.2f}, 止损: {stop_loss_price:.2f}, 止盈: {take_profit_price:.2f}")
    return stop_loss_price, take_profit_price

def create_short_with_sl_tp(amount: float):
    """
    创建空单并同时设置止损止盈
    """
    try:
        inst_id = get_correct_inst_id()
        current_price = get_current_price()
        
        # 计算止损止盈价格
        stop_loss_price, take_profit_price = calculate_stop_loss_take_profit_prices('short', current_price)
        
        # 基础参数
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': 'sell',  # 空单
            'ordType': 'market',
            'sz': str(amount),
        }
        
        # 添加止损止盈参数
        params['attachAlgoOrds'] = [
            {
                'tpTriggerPx': str(take_profit_price),
                'tpOrdPx': '-1',  # 市价止盈
                'slTriggerPx': str(stop_loss_price),
                'slOrdPx': '-1',  # 市价止损
                'algoOrdType': 'conditional',
                'sz': str(amount),
                'side': 'buy'  # 止损止盈方向与开仓方向相反
            }
        ]
        
        log_order_params("空单带止损止盈", params, "create_short_with_sl_tp")
        logger.info(f"🎯 执行空单开仓: {amount} 张")
        logger.info(f"🛡️ 止损价格: {stop_loss_price:.2f}")
        logger.info(f"🎯 止盈价格: {take_profit_price:.2f}")
        
        # 创建订单
        response = exchange.private_post_trade_order(params)
        
        log_api_response(response, "create_short_with_sl_tp")
        
        if response and response.get('code') == '0':
            order_id = response['data'][0]['ordId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ 空单创建成功: {order_id}")
            return {
                'order_id': order_id,
                'stop_loss_price': stop_loss_price,
                'take_profit_price': take_profit_price,
                'amount': amount
            }
        else:
            logger.error(f"❌ 空单创建失败: {response}")
            return None
            
    except Exception as e:
        logger.error(f"空单开仓失败: {str(e)}")
        import traceback
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return None

def wait_for_order_fill(order_id: str, timeout: int = 30) -> bool:
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
                
            time.sleep(2)
            
        except Exception as e:
            logger.error(f"检查订单状态失败: {str(e)}")
            time.sleep(2)
    
    logger.warning(f"⏰ 订单等待超时: {order_id}")
    return False

def get_current_position():
    """获取当前持仓"""
    try:
        positions = exchange.fetch_positions()
        
        if not positions:
            logger.info("📊 没有找到任何持仓")
            return None
        
        target_symbol = config.symbol
        logger.info(f"📊 查找持仓: {target_symbol}")
        
        for pos in positions:
            symbol = pos.get('symbol', '')
            contracts = float(pos.get('contracts', 0))
            
            if symbol == target_symbol and contracts > 0:
                position_info = {
                    'side': pos.get('side', 'unknown'),
                    'size': contracts,
                    'entry_price': float(pos.get('entryPrice', 0)),
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
        inst_id = get_correct_inst_id()
        
        params = {
            'instType': 'SWAP',
            'instId': inst_id,
            'ordType': 'conditional',
        }
        
        logger.info(f"📋 查询 {inst_id} 的止损止盈条件单...")
        response = exchange.private_get_trade_orders_algo_pending(params)
        
        if response and response.get('code') == '0':
            orders = response.get('data', [])
            
            if orders:
                logger.info(f"✅ 发现止损止盈条件单: {len(orders)}个")
                for order in orders:
                    algo_id = order.get('algoId', 'Unknown')
                    has_tp = order.get('tpTriggerPx') not in [None, '']
                    has_sl = order.get('slTriggerPx') not in [None, '']
                    
                    if has_tp and has_sl:
                        order_type = "OCO"
                    elif has_sl:
                        order_type = "止损"
                    elif has_tp:
                        order_type = "止盈"
                    else:
                        order_type = "其他条件单"
                    
                    logger.info(f"   ID: {algo_id}, 类型: {order_type}")
                    if has_sl:
                        logger.info(f"     止损触发: {order.get('slTriggerPx')}")
                    if has_tp:
                        logger.info(f"     止盈触发: {order.get('tpTriggerPx')}")
                
                return True
            else:
                logger.info(f"📋 未发现 {inst_id} 的止损止盈条件单")
                return False
        else:
            logger.warning(f"⚠️ 查询止损止盈订单失败")
            return False
            
    except Exception as e:
        logger.error(f"检查止损止盈订单失败: {str(e)}")
        return False

def cancel_all_sl_tp_orders():
    """撤销所有止损止盈订单"""
    try:
        inst_id = get_correct_inst_id()
        
        logger.info(f"🔄 撤销 {inst_id} 的所有止损止盈订单...")
        
        params = {
            'instType': 'SWAP',
            'instId': inst_id,
            'ordType': 'conditional',
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
                    cancel_params = [
                        {
                            'algoId': algo_id,
                            'instId': inst_id,
                        }
                    ]
                    
                    cancel_response = exchange.private_post_trade_cancel_algos(cancel_params)
                    
                    if cancel_response and cancel_response.get('code') == '0':
                        logger.info(f"✅ 已撤销条件单: {algo_id}")
                        cancel_count += 1
                    else:
                        logger.error(f"❌ 撤销条件单失败: {algo_id}")
            
            logger.info(f"📊 总计撤销 {cancel_count}/{len(orders)} 个条件单")
            return cancel_count > 0
        else:
            logger.error(f"❌ 获取待撤销订单失败")
            return False
            
    except Exception as e:
        logger.error(f"撤销止损止盈订单失败: {str(e)}")
        return False

def close_short_position_limit(amount: float):
    """
    限价平空单
    """
    try:
        inst_id = get_correct_inst_id()
        current_price = get_current_price()
        
        # 平空单方向为买入，使用限价单
        # 使用比当前价格稍低的价格以确保快速成交
        limit_price = current_price * 0.999
        
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': 'buy',  # 平空单
            'ordType': 'limit',
            'sz': str(amount),
            'px': str(limit_price),
        }
        
        log_order_params("限价平仓", params, "close_short_position_limit")
        logger.info(f"🔄 执行空单限价平仓: {amount} 张 @ {limit_price:.2f}")
        
        response = exchange.private_post_trade_order(params)
        
        log_api_response(response, "close_short_position_limit")
        
        if response and response.get('code') == '0':
            order_id = response['data'][0]['ordId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ 平仓订单创建成功: {order_id}")
            
            # 等待平仓成交
            if wait_for_order_fill(order_id, 30):
                logger.info(f"✅ 平仓订单已成交")
                return True
            else:
                logger.error(f"❌ 平仓订单未在30秒内成交")
                return False
        else:
            logger.error(f"❌ 平仓订单创建失败: {response}")
            return False
            
    except Exception as e:
        logger.error(f"限价平仓失败: {str(e)}")
        return False

def verify_position_closed(timeout: int = 10) -> bool:
    """验证仓位是否已平"""
    logger.info("🔍 验证仓位是否已平...")
    
    start_time = time.time()
    while time.time() - start_time < timeout:
        position = get_current_position()
        if not position:
            logger.info("✅ 确认仓位已平")
            return True
        logger.info(f"⏳ 仍有持仓: {position}, 等待中...")
        time.sleep(2)
    
    logger.error("❌ 仓位未在指定时间内平掉")
    return False

def run_short_sl_tp_test():
    """
    运行空单止盈止损测试流程
    """
    logger.info("🚀 开始空单止盈止损测试流程")
    logger.info("=" * 50)
    
    # 1. 设置交易所
    if not setup_exchange():
        logger.error("❌ 交易所设置失败，测试中止")
        return False
    
    # 2. 获取当前价格和计算仓位
    current_price = get_current_price()
    if current_price == 0:
        logger.error("❌ 无法获取当前价格，测试中止")
        return False
    
    position_size = calculate_position_size()
    if position_size <= 0:
        logger.error("❌ 仓位计算失败，测试中止")
        return False
    
    logger.info(f"🎯 测试参数:")
    logger.info(f"   交易对: {config.symbol}")
    logger.info(f"   保证金: {config.base_usdt_amount} USDT")
    logger.info(f"   杠杆: {config.leverage}x")
    logger.info(f"   仓位大小: {position_size:.4f} 张")
    logger.info(f"   止损: {config.stop_loss_percent*100}%")
    logger.info(f"   止盈: {config.take_profit_percent*100}%")
    logger.info(f"   等待时间: {config.wait_time_seconds}秒")
    
    # 阶段1: 开空单并设置止盈止损
    logger.info("")
    logger.info("🔹 阶段1: 开空单并设置止盈止损")
    logger.info("-" * 40)
    
    short_order_result = create_short_with_sl_tp(position_size)
    if not short_order_result:
        logger.error("❌ 空单开仓失败")
        return False
    
    # 等待订单成交
    if not wait_for_order_fill(short_order_result['order_id'], 30):
        logger.error("❌ 空单未在30秒内成交")
        return False
    
    # 确认持仓建立
    time.sleep(2)
    short_position = get_current_position()
    if not short_position or short_position['side'] != 'short':
        logger.error("❌ 空单持仓未建立")
        return False
    
    logger.info(f"✅ 空单持仓建立: {short_position['size']}张")
    
    # 阶段2: 确认止盈止损设置正确
    logger.info("")
    logger.info("🔹 阶段2: 确认止盈止损设置")
    logger.info("-" * 40)
    
    logger.info("📋 检查止盈止损订单...")
    has_sl_tp = check_sl_tp_orders()
    if not has_sl_tp:
        logger.error("❌ 未发现止盈止损订单")
        return False
    
    logger.info("✅ 止盈止损订单设置正确")
    
    # 阶段3: 等待5秒
    logger.info("")
    logger.info("🔹 阶段3: 等待5秒")
    logger.info("-" * 40)
    
    logger.info(f"⏳ 等待 {config.wait_time_seconds} 秒...")
    for i in range(config.wait_time_seconds, 0, -1):
        logger.info(f"   {i}秒后平仓...")
        time.sleep(1)
    
    # 阶段4: 限价平仓
    logger.info("")
    logger.info("🔹 阶段4: 限价平仓")
    logger.info("-" * 40)
    
    close_success = close_short_position_limit(short_position['size'])
    if not close_success:
        logger.error("❌ 限价平仓失败")
        return False
    
    # 阶段5: 确认仓位已平
    logger.info("")
    logger.info("🔹 阶段5: 确认仓位已平")
    logger.info("-" * 40)
    
    if not verify_position_closed():
        logger.error("❌ 仓位未完全平掉")
        return False
    
    # 阶段6: 检查并清理止盈止损订单
    logger.info("")
    logger.info("🔹 阶段6: 检查并清理止盈止损订单")
    logger.info("-" * 40)
    
    logger.info("📋 检查平仓后止盈止损订单状态...")
    has_remaining_orders = check_sl_tp_orders()
    
    if has_remaining_orders:
        logger.warning("⚠️ 发现平仓后仍有止盈止损订单存在")
        logger.info("🔄 执行清理...")
        
        if cancel_all_sl_tp_orders():
            logger.info("✅ 止盈止损订单清理成功")
        else:
            logger.error("❌ 止盈止损订单清理失败")
            return False
    else:
        logger.info("✅ 止盈止损订单已自动取消")
    
    # 最终确认
    logger.info("")
    logger.info("🔹 最终状态确认")
    logger.info("-" * 40)
    
    # 最终检查无持仓
    final_position = get_current_position()
    if final_position:
        logger.error(f"❌ 最终检查发现仍有持仓: {final_position}")
        return False
    
    # 最终检查无止损止盈订单
    final_sl_tp = check_sl_tp_orders()
    if final_sl_tp:
        logger.error("❌ 最终检查发现仍有止盈止损订单")
        return False
    
    logger.info("✅ 所有检查通过!")
    
    logger.info("")
    logger.info("🎉 空单止盈止损测试流程完成!")
    logger.info("=" * 50)
    return True

def cleanup_after_test():
    """测试结束后的清理工作"""
    try:
        logger.info("🧹 测试结束，执行清理...")
        
        # 检查并平掉所有持仓
        position = get_current_position()
        if position:
            logger.warning(f"⚠️ 测试结束发现未平持仓: {position}")
            # 这里可以添加紧急平仓逻辑
        
        # 撤销所有止损止盈订单
        cancel_all_sl_tp_orders()
        
        logger.info("✅ 清理完成")
        return True
        
    except Exception as e:
        logger.error(f"清理失败: {str(e)}")
        return False

def main():
    """主函数"""
    try:
        logger.info("=" * 50)
        logger.info("🔧 BTC空单止盈止损测试程序")
        logger.info("=" * 50)
        
        # 确认测试参数
        logger.info("📋 测试配置:")
        logger.info(f"   交易对: {config.symbol}")
        logger.info(f"   杠杆: {config.leverage}x")
        logger.info(f"   保证金: {config.base_usdt_amount} USDT")
        logger.info(f"   止损止盈: {config.stop_loss_percent*100}%")
        logger.info(f"   测试模式: {'模拟盘' if config.test_mode else '实盘'}")
        
        # 用户确认
        if not config.test_mode:
            logger.warning("⚠️ 注意: 这是实盘交易，将使用真实资金!")
            confirm = input("确认继续? (yes/no): ")
            if confirm.lower() != 'yes':
                logger.info("测试取消")
                return
        
        # 运行测试
        success = run_short_sl_tp_test()
        
        # 执行清理
        logger.info("")
        logger.info("🧹 执行测试后清理...")
        cleanup_after_test()
        
        if success:
            logger.info("🎊 测试成功完成!")
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