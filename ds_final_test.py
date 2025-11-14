#!/usr/bin/env python3

# ds_sltp_test.py - BTC空单止盈止损测试程序（独立完整版）

import os
import time
import sys
import traceback
import uuid
import json
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple, Union
import ccxt
from dotenv import load_dotenv


# 在文件顶部定义全局变量
saved_attach_algo_ids = []

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

# 创建专用logger
logger = TestLogger(log_dir="../Output/short_sl_tp_test", file_name="Short_SL_TP_Test_{timestamp}.log")


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

def adjust_position_size(amount: float) -> float:
    """
    调整仓位大小以符合交易所的最小交易量和精度要求
    修复浮点数转整数时的类型错误
    """
    try:
        # 获取市场信息（最小交易量、精度等）
        market_info = get_lot_size_info()
        min_amount = market_info.get('min_amount', 0.01)  # 最小交易量（如0.01）
        precision = market_info.get('precision', 0.01)    # 精度（如0.01表示两位小数）
        
        # 处理极端情况：输入数量为0或负数
        if amount <= 0:
            logger.warning(f"输入数量无效: {amount}，使用最小交易量 {min_amount}")
            return min_amount
        
        # 计算精度对应的小数位数（如0.01 → 2位小数）
        # 避免浮点数直接处理，转为字符串解析
        precision_str = str(precision)
        if '.' in precision_str:
            decimal_places = len(precision_str.split('.')[1])
        else:
            decimal_places = 0  # 整数精度（如1.0 → 0位小数）
        
        # 1. 先将数量四舍五入到指定精度（避免小数位数过多）
        rounded_amount = round(amount, decimal_places)
        
        # 2. 确保数量不小于最小交易量
        if rounded_amount < min_amount:
            logger.warning(f"数量 {rounded_amount} 小于最小交易量 {min_amount}，自动调整为 {min_amount}")
            return min_amount
        
        # 3. 确保数量是最小交易量的整数倍（核心修复：用整数运算避免浮点数误差）
        # 转换为最小单位的整数（如0.01 → 1个单位，0.05 → 5个单位）
        multiplier = 10 **decimal_places  # 10^小数位数（如2 → 100）
        min_amount_units = int(round(min_amount * multiplier))  # 最小交易量的单位数（如0.01*100=1）
        amount_units = int(round(rounded_amount * multiplier))   # 当前数量的单位数（如0.05*100=5）
        
        # 计算最大的、小于等于当前单位数的最小单位倍数
        max_valid_units = (amount_units // min_amount_units) * min_amount_units
        
        # 转换回原始单位
        adjusted_amount = max_valid_units / multiplier
        
        logger.info(f"📏 仓位调整完成: {amount} → {adjusted_amount} (精度: {decimal_places}位小数)")
        return adjusted_amount
        
    except Exception as e:
        logger.error(f"调整仓位大小失败: {str(e)}")
        # 失败时返回最小交易量作为保底
        return market_info.get('min_amount', 0.01)


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
    else:  # 空头 (side == 'sell' or 'short')
        stop_loss_price = entry_price * (1 + config.stop_loss_percent)
        take_profit_price = entry_price * (1 - config.take_profit_percent)
    
    logger.info(f"🎯 价格计算 - 入场: {entry_price:.2f}, 止损: {stop_loss_price:.2f}, 止盈: {take_profit_price:.2f}")
    return stop_loss_price, take_profit_price

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

# ---------------------------------------------------------------------------
# Code 专属于 ds_sltp_test.py 的函数
# ---------------------------------------------------------------------------

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


def algo_order_pending_get_comprehensive_info(inst_id: str) -> Dict[str, Any]:
    """
    获取指定交易对的所有策略委托单（未完成的算法订单）综合信息
    基于 OKX 的 private_get_trade_orders_algo_pending 接口实现
    
    :param inst_id: 交易对 ID（如 'BTC-USDT-SWAP'）
    :return: 包含策略委托单信息的字典，结构如下：
        {
            "success": bool,          # 接口调用是否成功
            "error": str,             # 错误信息（成功时为空）
            "total_count": int,       # 策略委托单总数
            "algo_orders": List[Dict] # 策略委托单详细列表
        }
    """
    result = {
        "success": False,
        "error": "",
        "total_count": 0,
        "algo_orders": []
    }
    
    try:
        # 构造查询参数：指定交易对、策略订单类型（conditional=条件单, oco=OCO单）
        params = {
            "instType": "SWAP",       # 产品类型：永续合约（根据实际场景调整）
            "instId": inst_id,        # 指定交易对
            "ordType": "conditional,oco"  # 策略订单类型，可根据需要扩展
        }
        
        logger.info(f"🔍 查询策略委托单（未完成）请求参数: {json.dumps(params, indent=2)}")
        
        # 调用 OKX 未完成算法订单查询接口
        response = exchange.private_get_trade_orders_algo_pending(params)
        
        # 打印完整响应日志
        logger.info(f"📥 策略委托单查询响应: {json.dumps(response, indent=2)}")
        
        # 检查接口返回状态
        if not response:
            result["error"] = "接口无返回数据"
            return result
        
        if response.get("code") != "0":
            result["error"] = f"接口返回错误: {response.get('msg', '未知错误')}"
            return result
        
        # 提取策略委托单数据
        algo_orders = response.get("data", [])
        result["algo_orders"] = algo_orders
        result["total_count"] = len(algo_orders)
        result["success"] = True
        
        # 日志输出统计信息
        logger.info(f"✅ 成功获取 {inst_id} 的策略委托单，共 {result['total_count']} 条")
        
        return result
        
    except Exception as e:
        error_msg = f"查询策略委托单异常: {str(e)}"
        logger.error(error_msg)
        logger.error(f"异常堆栈: {traceback.format_exc()}")
        result["error"] = error_msg
    
    logger.info("=" * 80)
    return result

#未完成的委托订单解析
def algo_pending_orders_parse(
    algo_result: Dict[str, Any],
    target_inst_id: Optional[str] = None
) -> None:
    """
    解析策略委托单（未完成）的返回信息并格式化打印，重点提取止盈止损触发价格
    
    :param algo_result: algo_order_pending_get_comprehensive_info 函数的返回结果
    :param target_inst_id: 可选，指定交易对（如 'BTC-USDT-SWAP'），仅打印该交易对的信息；
                           不指定则打印所有交易对的信息
    """
    # 检查原始结果是否有效
    if not algo_result.get("success"):
        logger.error(f"❌ 策略委托单数据无效：{algo_result.get('error', '未知错误')}")
        return

    # 提取核心数据
    total_count = algo_result.get("total_count", 0)
    algo_orders = algo_result.get("algo_orders", [])
    target_inst_id = target_inst_id or algo_result.get("inst_id")  # 优先使用传入的交易对，其次用结果中的交易对

    logger.info("=" * 80)
    logger.info(f"📊 策略委托单解析结果（交易对：{target_inst_id or '全部'}）")
    logger.info(f"📝 总数量：{total_count} 条")
    logger.info("-" * 80)

    if total_count == 0:
        logger.info("ℹ️ 没有找到未完成的策略委托单")
        logger.info("=" * 80)
        return

    # 筛选目标交易对的订单（如果指定）
    filtered_orders = []
    for order in algo_orders:
        order_inst_id = order.get("instId")
        if not target_inst_id or order_inst_id == target_inst_id:
            filtered_orders.append(order)

    logger.info(f"🔍 筛选后有效订单数量：{len(filtered_orders)} 条")
    logger.info("-" * 80)

    # 逐个解析并打印订单信息
    for idx, order in enumerate(filtered_orders, 1):
        # 提取核心字段（兼容OKX接口返回格式）
        order_info = {
            "序号": idx,
            "交易对": order.get("instId", "未知"),
            "策略订单ID": order.get("algoId", "未知"),
            "自定义策略ID": order.get("algoClOrdId", "未设置"),
            "订单类型": order.get("ordType", "未知"),  # conditional=条件单, oco=OCO单等
            "方向": "多" if order.get("side") == "buy" else "空" if order.get("side") == "sell" else "未知",
            "数量": order.get("sz", "未知"),
            "状态": order.get("state", "未知"),
            "止损触发价": order.get("slTriggerPx", "未设置"),
            "止盈触发价": order.get("tpTriggerPx", "未设置"),
            "关联主订单ID": order.get("attachOrdId", "无关联")
        }

        # 格式化打印（突出显示止盈止损信息）
        logger.info(f"📌 订单 #{order_info['序号']}")
        logger.info(f"   交易对：{order_info['交易对']} | 类型：{order_info['订单类型']} | 方向：{order_info['方向']}")
        logger.info(f"   策略ID：{order_info['策略订单ID']} | 自定义ID：{order_info['自定义策略ID']}")
        logger.info(f"   数量：{order_info['数量']} | 状态：{order_info['状态']}")
        logger.info(f"   🛡️ 止损触发价：{order_info['止损触发价']}")  # 重点标注止损
        logger.info(f"   🎯 止盈触发价：{order_info['止盈触发价']}")  # 重点标注止盈
        logger.info(f"   关联主订单：{order_info['关联主订单ID']}")
        logger.info("-" * 60)

def get_pending_algo_order_count(
    algo_result: Dict[str, Any],
    target_inst_id: Optional[str] = None
) -> int:
    """
    获取指定交易对的未完成策略委托单数量
    
    :param algo_result: algo_order_pending_get_comprehensive_info 函数的返回结果
    :param target_inst_id: 可选，指定交易对（如 'BTC-USDT-SWAP'），不指定则返回总数量
    :return: 未完成策略委托单数量（整数）
    """
    # 验证输入有效性
    if not algo_result.get("success"):
        raise ValueError(f"无效的策略委托单数据：{algo_result.get('error', '未知错误')}")

    # 优先使用结果中的total_count（如果存在）
    total_count = algo_result.get("total_count", 0)
    if not target_inst_id:
        return total_count

    # 若指定交易对，需筛选统计 
    pending_algos = algo_result.get("algo_orders", [])
    main_order_inst_id = algo_result.get("main_order_data", {}).get("instId")
    count = 0

    for algo in pending_algos:
        # 交易对匹配逻辑（兼容订单中未显式指定instId的情况）
        algo_inst_id = algo.get("instId") or main_order_inst_id
        if algo_inst_id == target_inst_id:
            count += 1

    return count

def close_position_universal(
    side: str,
    amount: Optional[float] = None,
    ord_type: str = 'market',
    price: Optional[float] = None
) -> Dict[str, Any]:
    """
    全能平仓函数，支持市价平仓和限价平仓（使用ccxt标准化接口，兼容多交易所）
    """
    try:
        # 1. 确定平仓方向（与原持仓方向相反）
        close_side = 'sell' if side in ('buy', 'long') else 'buy'
        action_name = f"{'多头' if side in ('buy', 'long') else '空头'}{'市价' if ord_type == 'market' else '限价'}平仓"
        
        # 2. 获取必要参数
        inst_id = get_correct_inst_id()
        current_price = get_current_price()
        
        if current_price == 0:
            error_msg = "无法获取当前价格，无法执行平仓操作"
            logger.error(f"❌ {error_msg}")
            return {'success': False, 'error': error_msg, 'order_id': None, 'cl_ord_id': None, 'response': None}
        
        # 3. 处理平仓数量（默认平掉全部持仓）
        if amount is None:
            position = get_current_position()
            if not position:
                error_msg = "没有持仓需要平仓"
                logger.info(f"ℹ️ {error_msg}")
                return {'success': True, 'error': error_msg, 'order_id': None, 'cl_ord_id': None, 'response': None}
            
            amount = float(position.get('sz', 0))
            if amount <= 0:
                error_msg = "持仓数量无效，无法平仓"
                logger.error(f"❌ {error_msg}")
                return {'success': False, 'error': error_msg, 'order_id': None, 'cl_ord_id': None, 'response': None}
        
        # 4. 调整数量为符合交易所要求的安全值
        amount = adjust_position_size(amount)
        if amount <= 0:
            error_msg = f"调整后平仓数量无效: {amount}"
            logger.error(f"❌ {error_msg}")
            return {'success': False, 'error': error_msg, 'order_id': None, 'cl_ord_id': None, 'response': None}
        
        # 5. 生成自定义订单ID（ccxt标准参数为clientOrderId）
        cl_ord_id = generate_cl_ord_id(close_side)
        
        # 6. 构建ccxt标准化订单参数
        # ccxt标准参数：symbol, type, side, amount, price, params
        order_params = {
            'symbol': inst_id,
            'type': ord_type,
            'side': close_side,
            'amount': amount,
            'clientOrderId': cl_ord_id,  # 自定义订单ID，部分交易所支持
        }
        
        # 添加价格参数（限价单）
        if ord_type == 'limit':
            if price is None:
                # 自动设置合理的默认限价
                if close_side == 'buy':  # 平空单（买入）时，限价略高于当前价
                    price = current_price * 1.001
                else:  # 平多单（卖出）时，限价略低于当前价
                    price = current_price * 0.999
                logger.warning(f"⚠️ 未指定限价，自动设置为: {price:.2f}")
            
            order_params['price'] = price
        
        # 添加交易所特定参数（如保证金模式）
        # 注意：不同交易所的保证金模式参数可能不同，这里以OKX为例，其他交易所可能需要调整
        order_params['params'] = {
            'tdMode': config.margin_mode  # 保证金模式，部分交易所可能不需要
        }
        
        # 7. 打印订单信息
        logger.info(f"📤 {action_name}参数:")
        logger.info(json.dumps(order_params, indent=2, ensure_ascii=False))
        logger.info(f"🎯 执行{action_name}: {amount} 张 {'@ ' + str(price) if ord_type == 'limit' else ''}")
        
        # 8. 执行平仓订单（使用ccxt标准化接口）
        response = exchange.create_order(
            symbol=order_params['symbol'],
            type=order_params['type'],
            side=order_params['side'],
            amount=order_params['amount'],
            price=order_params.get('price'),
            params=order_params['params']
        )
        
        # 9. 处理API响应（ccxt标准化响应格式）
        logger.info(f"📥 {action_name}响应:")
        logger.info(json.dumps(response, indent=2, ensure_ascii=False))
        
        # 检查ccxt响应是否成功（不同交易所可能有差异）
        if not response or ('status' in response and response['status'] not in ['open', 'closed']):
            error_msg = f"订单状态异常: {response.get('info', {}).get('msg', '未知错误')}"
            logger.error(f"❌ {action_name}失败: {error_msg}")
            return {
                'success': False,
                'error': error_msg,
                'order_id': response.get('id') if response else None,
                'cl_ord_id': cl_ord_id,
                'response': response
            }
        
        # 10. 提取订单ID（ccxt标准字段为id）
        order_id = response.get('id')
        logger.info(f"✅ {action_name}成功: {order_id} (自定义ID: {cl_ord_id})")
        
        return {
            'success': True,
            'order_id': order_id,
            'cl_ord_id': cl_ord_id,
            'response': response,
            'error': None
        }
        
    except Exception as e:
        error_msg = f"{action_name}异常: {str(e)}"
        logger.error(error_msg)
        logger.error(f"异常堆栈: {traceback.format_exc()}")
        return {
            'success': False,
            'error': error_msg,
            'order_id': None,
            'cl_ord_id': None,
            'response': None
        }


def amend_untraded_sl_tp(main_ord_id: str, attach_algo_id: str, inst_id: str) -> bool:
    """适用于主订单未完全成交，止盈止损未委托的场景"""
    try:
        params = {
            "instId": inst_id,
            "ordId": main_ord_id,
            "attachAlgoOrds": [
                {
                    "attachAlgoId": attach_algo_id,
                    "newTpTriggerPx": "0",
                    "newSlTriggerPx": "0"
                }
            ]
        }
        
        logger.info(f"🔄 [未成交阶段] 修改附带止盈止损: attachAlgoId={attach_algo_id}")
        logger.info(f"   请求参数: {json.dumps(params, indent=2, ensure_ascii=False)}")
        response = exchange.private_post_trade_amend_order(params)
        logger.info(f"   响应: {json.dumps(response, indent=2, ensure_ascii=False)}")
        
        if response and response.get("code") == "0":
            logger.info(f"✅ 成功撤销未委托止盈止损: {attach_algo_id}")
            return True
        else:
            logger.error(f"❌ 修改失败: {response}")
            return False
    except Exception as e:
        logger.error(f"修改出错: {str(e)}")
        return False


def amend_traded_sl_tp(
    algo_id: Optional[str] = None,
    algo_cl_ord_id: Optional[str] = None,
    inst_id: Optional[str] = None,
    new_sl_price: Optional[Union[float, int]] = None,  # 支持0（删除）或具体价格
    new_tp_price: Optional[Union[float, int]] = None   # 支持0（删除）或具体价格
) -> Dict[str, Any]:
    """
    支持通过0删除止盈/止损的修改函数（符合OKX API规范）
    若new_sl_price=0 → 删除止损；new_tp_price=0 → 删除止盈
    """
    result = {
        "success": False,
        "algo_id": algo_id,
        "algo_cl_ord_id": algo_cl_ord_id,
        "response": None,
        "error": None,
        "code": None
    }
    
    # 参数校验：至少提供一个ID和一个操作（修改或删除）
    if not algo_id and not algo_cl_ord_id:
        result["error"] = "必须提供algo_id或algo_cl_ord_id"
        logger.error(result["error"])
        return result
    
    # 允许0作为有效操作（删除），但需确保至少有一个价格参数非None
    if new_sl_price is None and new_tp_price is None:
        result["error"] = "必须提供new_sl_price（含0）或new_tp_price（含0）"
        logger.warning(result["error"])
        return result
    
    # 补全交易对ID
    inst_id = inst_id or get_correct_inst_id()
    if not inst_id:
        result["error"] = "无法获取交易对ID（inst_id）"
        logger.error(result["error"])
        return result
    
    try:
        # 构建基础参数（定位订单）
        amend_params = {
            "instId": inst_id,
            **({"algoId": algo_id} if algo_id else {"algoClOrdId": algo_cl_ord_id})
        }
        
        # 处理止损价：0 → 删除；其他值 → 修改为对应价格（转为字符串）
        if new_sl_price is not None:
            if new_sl_price == 0:
                logger.info("📌 检测到new_sl_price=0，执行删除止损操作")
                amend_params["slTriggerPx"] = "0"  # 符合OKX API要求
            else:
                amend_params["slTriggerPx"] = str(new_sl_price)
        
        # 处理止盈价：0 → 删除；其他值 → 修改为对应价格（转为字符串）
        if new_tp_price is not None:
            if new_tp_price == 0:
                logger.info("📌 检测到new_tp_price=0，执行删除止盈操作")
                amend_params["tpTriggerPx"] = "0"  # 符合OKX API要求
            else:
                amend_params["tpTriggerPx"] = str(new_tp_price)
        
        # 打印操作信息
        logger.info(f"📝 策略订单修改参数：{json.dumps(amend_params, indent=2)}")
        
        # 调用OKX修改接口
        response = exchange.private_post_trade_amend_algos(amend_params)
        result["response"] = response
        logger.info(f"📥 API响应：{json.dumps(response, indent=2)}")
        
        # 处理响应结果
        if not response or response.get("code") != "0":
            result["code"] = response.get("code") if response else None
            result["error"] = f"操作失败：{response.get('msg', '未知错误')}" if response else "无响应"
            logger.error(result["error"])
            return result
        
        # 提取修改后的订单ID
        amended_data = response.get("data", [{}])[0]
        result["algo_id"] = amended_data.get("algoId") or algo_id
        result["algo_cl_ord_id"] = amended_data.get("algoClOrdId") or algo_cl_ord_id
        result["success"] = True
        logger.info(f"✅ 操作成功：algo_id={result['algo_id']}")
        
        return result
    
    except Exception as e:
        result["error"] = f"异常：{str(e)}"
        logger.error(result["error"], exc_info=True)
        return result

def cancel_algo_order_by_attach_id(algo_cl_ord_id: str, inst_id: str) -> bool:
    """通过algoClOrdId撤销已激活的止盈止损单"""
    try:
        params = [{
            "instId": inst_id,
            "algoClOrdId": algo_cl_ord_id
        }]
        
        logger.info(f"🔄 通过algoClOrdId撤销止盈止损单: {algo_cl_ord_id}")
        logger.info(f"   请求参数: {json.dumps(params, indent=2, ensure_ascii=False)}")
        
        response = exchange.private_post_trade_cancel_algos(params)
        logger.info(f"   响应: {json.dumps(response, indent=2, ensure_ascii=False)}")
        
        if response and response.get("code") == "0":
            logger.info(f"✅ 成功撤销止盈止损单: {algo_cl_ord_id}")
            return True
        else:
            logger.error(f"❌ 撤销失败: {response}")
            return False
            
    except Exception as e:
        logger.error(f"通过algoClOrdId撤销止盈止损单失败: {str(e)}")
        return False

def cancel_attached_sl_tp_by_algo_ids(main_ord_id: str, attach_algo_ids: List[str], algo_cl_ord_ids: List[str], attach_algo_cl_ord_ids: List[str], main_order_state: str, has_activated_sl_tp: bool = False) -> bool:
    """
    专门处理附带止盈止损单的撤销
    根据主订单状态和止盈止损单激活状态选择正确的撤销方式
    """
    if not attach_algo_ids and not algo_cl_ord_ids and not attach_algo_cl_ord_ids:
        logger.info("✅ 没有需要撤销的附带止盈止损单")
        return True
        
    inst_id = get_correct_inst_id()
    success = True
    
    logger.info(f"🔧 开始撤销附带止盈止损单, 主订单状态: {main_order_state}, 止盈止损激活状态: {has_activated_sl_tp}")
    
    # 关键修复：优先使用我们自定义的止盈止损ID
    if attach_algo_cl_ord_ids:
        logger.info("🔄 优先使用自定义止盈止损ID进行撤销")
        for algo_cl_ord_id in attach_algo_cl_ord_ids:
            tp_sl_amend_result = amend_traded_sl_tp(
                algo_cl_ord_id=algo_cl_ord_id,
                inst_id= inst_id,
                new_sl_price=0,
                new_tp_price=0  # 同时修改两者
            )
            if tp_sl_amend_result["success"]:
                print(f"止损止盈同时修改成功，自定义ID：{tp_sl_amend_result['algo_cl_ord_id']}")
                success = True
            else:
                logger.error(f"❌ 使用自定义ID撤销止盈止损单失败: {algo_cl_ord_id}")
                success = False

            time.sleep(1)
    
    return success

def get_safe_position_size() -> float:
    """安全计算仓位大小，确保符合交易所要求"""
    try:
        market_info = get_lot_size_info()
        min_amount = market_info.get('min_amount', 0.01)
        
        calculated_size = calculate_position_size()
        logger.info(f"📏 计算仓位大小: {calculated_size}, 最小交易量: {min_amount}")
        
        if calculated_size < min_amount:
            logger.warning(f"⚠️ 使用最小值: {min_amount}")
            return min_amount
        
        if min_amount > 0:
            multiple = int(calculated_size / min_amount)
            safe_size = multiple * min_amount
            logger.info(f"📏 安全仓位大小: {safe_size}")
            return safe_size
        else:
            return calculated_size
            
    except Exception as e:
        logger.error(f"安全计算仓位大小失败: {str(e)}")
        return 0.01

def create_order_with_sl_tp(
    side: str, 
    amount: float, 
    order_type: str = 'market', 
    limit_price: Optional[float] = None, 
    stop_loss_price: Optional[float] = None, 
    take_profit_price: Optional[float] = None
) -> Dict[str, Any]:
    """
    创建订单并同时设置止损止盈 - 使用OKX新的attachAlgoOrds API
    支持市价单和限价单
    
    Args:
        side: 交易方向 'buy' 或 'sell'
        amount: 订单数量
        order_type: 订单类型 'market' 或 'limit'
        limit_price: 限价单价格（仅限价单需要）
        stop_loss_price: 止损价格
        take_profit_price: 止盈价格
        
    Returns:
        标准化响应结果:
            {
                'success': bool,
                'clOrdId': str,  # 主订单自定义ID
                'algo_cl_ord_id': 止损止盈订单的自定义ID
                'error': Optional[str],  # 错误信息（失败时存在）
            }
    """
    try:
        inst_id = get_correct_inst_id()  # 假设已实现获取标的ID的函数
        order_type_name = "市价单" if order_type == 'market' else "限价单"
        
        # 1. 生成主订单的自定义ID（clOrdId）
        main_cl_ord_id = generate_cl_ord_id(f"{side}")
        
        # 基础参数
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,  # 假设config已定义
            'side': side,
            'ordType': order_type,
            'sz': str(amount),
            'clOrdId': main_cl_ord_id,  # 主订单自定义ID
        }
        
        # 限价单补充价格参数
        if order_type == 'limit':
            if limit_price is None:
                error_msg = "❌ 限价单必须提供limit_price参数"
                logger.error(error_msg)
                return None
            params['px'] = str(limit_price)
        
        # 2. 处理止损止盈算法订单（生成attachClOrderId并构建参数）
        opposite_side = 'buy' if side == 'sell' else 'sell'  # 止损止盈方向与主订单相反
        sl_tp_cl_ord_id = generate_cl_ord_id(f"{side}")  # 止损止盈单自定义ID
        
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
                    'side': opposite_side,  # 止损止盈方向与开仓方向相反
                    'attachAlgoClOrdId': sl_tp_cl_ord_id  # 止损止盈方向与开仓方向相反
                }
            ]
        
        # 日志记录
        log_order_params(f"{order_type_name}带止损止盈", params, "create_order_with_sl_tp")
        if order_type == 'market':
            logger.info(f"🎯 执行市价{side}开仓: {amount} 张 (主订单ID: {main_cl_ord_id})")
        else:
            logger.info(f"🎯 执行限价{side}开仓: {amount} 张 @ {limit_price:.2f} (主订单ID: {main_cl_ord_id})")
        if stop_loss_price is not None:
            logger.info(f"🛡️ 止损价格: {stop_loss_price:.2f} (止损ID: {sl_tp_cl_ord_id})")
        if take_profit_price is not None:
            logger.info(f"🎯 止盈价格: {take_profit_price:.2f} (止盈ID: {sl_tp_cl_ord_id})")
        
        # 打印详细请求（仅限价单）
        if order_type == 'limit':
            logger.info("🚀 原始请求数据:")
            logger.info(f"   接口: POST /api/v5/trade/order")
            logger.info(f"   完整参数: {json.dumps(params, indent=2, ensure_ascii=False)}")
        
        # 调用OKX API
        response = exchange.private_post_trade_order(params)  # 假设exchange已初始化
        
        # 打印详细响应（仅限价单）
        if order_type == 'limit':
            logger.info("📥 原始响应数据:")
            logger.info(f"   完整响应: {json.dumps(response, indent=2, ensure_ascii=False)}")
        
        log_api_response(response, "create_order_with_sl_tp")  # 假设已实现日志函数
        
        # 3. 处理响应并返回标准化结果
        if response and response.get('code') == '0':
            order_id = response['data'][0]['ordId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ {order_type_name}创建成功: {order_id}")
            return {
                'success': True,
                'clOrdId': main_cl_ord_id,
                'attachclOrdId': sl_tp_cl_ord_id,
                'error': None,
            }
        else:
            error_msg = f"{order_type_name}创建失败: {response.get('msg', 'Unknown error')}" if response else f"{order_type_name}创建失败: 无响应"
            logger.error(error_msg)
            return {
                'success': False,
                'clOrdId': main_cl_ord_id,
                'attachclOrdId': sl_tp_cl_ord_id,
                'error': error_msg
            }
            
    except Exception as e:
        error_msg = f"{order_type_name}开仓失败: {str(e)}"
        logger.error(error_msg)
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return {
            'success': False,
            'clOrdId': main_cl_ord_id if 'main_cl_ord_id' in locals() else None,  # 确保即使生成ID失败也有返回
            'attachclOrdId': sl_tp_cl_ord_id,
            'error': error_msg
        }

def sl_tp_algo_order_set(side: str, amount: float, stop_loss_price: Optional[float] = None, take_profit_price: Optional[float] = None) -> Dict[str, Optional[str]]:
    """
    优化版：合并参数生成逻辑，通过动态添加字段处理OCO/单独止损/止盈订单
    返回单个ID而非列表（因每次调用最多生成一个订单）
    """
    # 初始化返回结果为单个值（None表示未生成订单）
    result = {'algo_id': None, 'algo_cl_ord_id': None}
    
    if not (stop_loss_price or take_profit_price):
        logger.warning("⚠️ 未设置止损或止盈价格，无需创建订单")
        return result

    try:
        inst_id = get_correct_inst_id()
        opposite_side = 'buy' if side in ('sell', 'short') else 'sell'
        
        # 公共参数（三种订单类型的共有字段）
        base_params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': opposite_side,
            'sz': str(amount),
        }

        # 1. 同时存在止损止盈：生成OCO订单
        if stop_loss_price and take_profit_price:
            oco_params = {
                **base_params,
                'ordType': 'oco',
                'slTriggerPx': str(stop_loss_price),
                'slOrdPx': '-1',
                'tpTriggerPx': str(take_profit_price),
                'tpOrdPx': '-1',
                'algoClOrdId': generate_cl_ord_id(f"{side}_sl_tp")  # OCO单专用ID
            }
            logger.info(f"📝 OCO订单参数: {json.dumps(oco_params, indent=2)}")
            response = exchange.private_post_trade_order_algo(oco_params)
            log_api_response(response, "OCO订单")
            
            if response and response.get('code') == '0':
                algo_id = response['data'][0]['algoId']
                result['algo_id'] = algo_id  # 赋值单个ID
                result['algo_cl_ord_id'] = oco_params['algoClOrdId']
                logger.info(f"✅ OCO订单创建成功 (algoId: {algo_id})")

        # 2. 仅止损：生成止损单
        elif stop_loss_price:
            sl_params = {
                **base_params,
                'ordType': 'conditional',
                'slTriggerPx': str(stop_loss_price),
                'slOrdPx': '-1',
                'algoClOrdId': generate_cl_ord_id(f"{side}_sl")  # 止损单专用ID
            }
            logger.info(f"📝 止损订单参数: {json.dumps(sl_params, indent=2)}")
            response = exchange.private_post_trade_order_algo(sl_params)
            log_api_response(response, "止损订单")
            
            if response and response.get('code') == '0':
                algo_id = response['data'][0]['algoId']
                result['algo_id'] = algo_id  # 赋值单个ID
                result['algo_cl_ord_id'] = sl_params['algoClOrdId']
                logger.info(f"✅ 止损订单创建成功 (algoId: {algo_id})")

        # 3. 仅止盈：生成止盈单
        elif take_profit_price:
            tp_params = {
                **base_params,
                'ordType': 'conditional',
                'tpTriggerPx': str(take_profit_price),
                'tpOrdPx': '-1',
                'algoClOrdId': generate_cl_ord_id(f"{side}_tp")  # 止盈单专用ID
            }
            logger.info(f"📝 止盈订单参数: {json.dumps(tp_params, indent=2)}")
            response = exchange.private_post_trade_order_algo(tp_params)
            log_api_response(response, "止盈订单")
            
            if response and response.get('code') == '0':
                algo_id = response['data'][0]['algoId']
                result['algo_id'] = algo_id  # 赋值单个ID
                result['algo_cl_ord_id'] = tp_params['algoClOrdId']
                logger.info(f"✅ 止盈订单创建成功 (algoId: {algo_id})")

        return result

    except Exception as e:
        logger.error(f"设置止损止盈失败: {str(e)}", exc_info=True)
        return result


def Is_sl_tp_canceled_with_instId(inst_id: str) -> bool:
    """使用优化查询检查止损止盈状态"""
    order_info = algo_order_pending_get_comprehensive_info(inst_id)
    if get_pending_algo_order_count(order_info,inst_id) > 0:
        return False
    
    return True

def get_algo_order_info_by_clId(
    algo_cl_ord_id: Optional[str] = None,
    algo_id: Optional[str] = None,
    inst_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    基于 GET /api/v5/trade/order-algo 获取策略委托单详细信息
    支持通过algoClOrdId（自定义ID）或algoId（系统ID）查询
    
    参数:
        algo_cl_ord_id: 策略委托单自定义ID（优先使用）
        algo_id: 策略委托单系统ID
        inst_id: 交易对ID（如不提供则自动获取）
    
    返回:
        包含查询结果的字典，结构如下:
        {
            "success": bool,          # 查询是否成功
            "data": Dict[str, Any],   # 订单详情（成功时）
            "error": str,             # 错误信息（失败时）
            "code": str               # 错误码（失败时）
        }
    """
    result = {
        "success": False,
        "data": None,
        "error": "",
        "code": ""
    }
    
    # 参数校验：必须提供至少一个ID
    if not algo_cl_ord_id and not algo_id:
        result["error"] = "必须提供algo_cl_ord_id或algo_id"
        logger.error(result["error"])
        return result
    
    try:
        # 补全交易对ID
        inst_id = inst_id or get_correct_inst_id()
        if not inst_id:
            result["error"] = "无法获取交易对ID"
            logger.error(result["error"])
            return result
        
        # 构建查询参数
        params = {"instId": inst_id}
        if algo_cl_ord_id:
            params["algoClOrdId"] = algo_cl_ord_id
            logger.info(f"🔍 查询策略委托单（自定义ID）: {algo_cl_ord_id}")
        else:
            params["algoId"] = algo_id
            logger.info(f"🔍 查询策略委托单（系统ID）: {algo_id}")
        
        # 调用OKX API
        logger.info(f"请求参数: {json.dumps(params, indent=2)}")
        response = exchange.private_get_trade_order_algo(params)
        logger.info(f"API响应: {json.dumps(response, indent=2)}")
        
        # 处理响应
        if response.get("code") != "0":
            result["error"] = response.get("msg", "未知错误")
            result["code"] = response.get("code", "")
            logger.error(f"查询失败 [{result['code']}]: {result['error']}")
            return result
        
        # 提取订单数据（返回列表中第一个元素为目标订单）
        algo_orders = response.get("data", [])
        if not algo_orders:
            result["error"] = "未找到对应的策略委托单"
            logger.warning(result["error"])
            return result
        
        result["data"] = algo_orders[0]
        result["success"] = True
        logger.info(f"✅ 成功获取策略委托单信息: {algo_orders[0].get('algoId')}")
        return result
    
    except Exception as e:
        error_msg = f"查询策略委托单异常: {str(e)}"
        result["error"] = error_msg
        logger.error(error_msg, exc_info=True)
        return result

def confirm_algo_order_by_clId(
    side: str,
    amount: float,
    stop_loss_price: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    algo_cl_ord_id: Optional[str] = None,
    timeout: int = 30,
    interval: int = 2
) -> Dict[str, Any]:
    """
    基于set_sl_tp_separately的参数和返回的单个algoClOrdId，确认止损/止盈委托单是否正确下发
    
    参数:
        side: 开仓方向（与set_sl_tp_separately一致，如'short'/'long'）
        amount: 委托数量（与set_sl_tp_separately一致）
        stop_loss_price: 止损价格（与set_sl_tp_separately一致，可选）
        take_profit_price: 止盈价格（与set_sl_tp_separately一致，可选）
        algo_cl_ord_id: set_sl_tp_separately返回的单个自定义策略ID
        timeout: 确认超时时间（秒）
        interval: 检查间隔（秒）
    
    返回:
        确认结果字典:
        {
            "success": bool,                # 确认是否成功
            "order": Dict,                  # 订单详情（成功时）
            "error": str,                   # 错误信息（失败时）
            "reason": List[str]             # 不匹配原因（存在不匹配时）
        }
    """
    result = {
        "success": False,
        "order": None,
        "error": "",
        "reason": []
    }
    
    # 参数校验
    if not algo_cl_ord_id:
        result["error"] = "未提供algo_cl_ord_id"
        logger.error(result["error"])
        return result
    
    # 计算预期的平仓方向（与开仓方向相反）
    close_side = "buy" if side == "short" else "sell"
    expected_sz = str(amount)  # 数量需转为字符串（与API参数一致）
    inst_id = get_correct_inst_id()
    if not inst_id:
        result["error"] = "无法获取交易对ID"
        logger.error(result["error"])
        return result
    
    # 超时循环检查单个订单
    start_time = time.time()
    found = False
    
    while time.time() - start_time < timeout and not found:
        # 获取单个订单信息
        order_info = get_algo_order_info_by_clId(
            algo_cl_ord_id=algo_cl_ord_id,
            inst_id=inst_id
        )
        
        if not order_info["success"]:
            # 订单暂未找到，继续等待
            time_left = int(timeout - (time.time() - start_time))
            logger.info(f"⏳ 等待订单 {algo_cl_ord_id} 确认（剩余{time_left}秒）")
            time.sleep(interval)
            continue
        
        # 订单已找到，开始校验
        found = True
        order_data = order_info["data"]
        mismatches = []  # 记录不匹配项
        
        # 1. 基础参数校验
        if order_data.get("side") != close_side:
            mismatches.append(
                f"方向不符（预期: {close_side}, 实际: {order_data.get('side')}）"
            )
        if order_data.get("sz") != expected_sz:
            mismatches.append(
                f"数量不符（预期: {expected_sz}, 实际: {order_data.get('sz')}）"
            )
        orderType = order_data.get("ordType")
        if orderType not in ["conditional", "oco"]:
            mismatches.append(
                f"订单类型不符（预期: conditional or oco, 实际: {orderType}）"
            )
            logger.log_warning(f"⚠️ 发现非算法订单类型: {orderType}")
        if order_data.get("state") not in ("live", "effective"):
            mismatches.append(
                f"订单状态无效（当前: {order_data.get('state')}）"
            )
        
        # 2. 区分止损/止盈单，校验触发价
        sl_trigger_px = order_data.get("slTriggerPx")
        tp_trigger_px = order_data.get("tpTriggerPx")
        expected_sl = str(stop_loss_price) if stop_loss_price else None
        expected_tp = str(take_profit_price) if take_profit_price else None
        
        # (修复逻辑：OCO订单会同时包含sl和tp字段)
        is_oco = order_data.get("ordType") == "oco"
        
        if is_oco:
             if sl_trigger_px != expected_sl:
                 mismatches.append(f"OCO止损触发价不符（预期: {expected_sl}, 实际: {sl_trigger_px}）")
             if tp_trigger_px != expected_tp:
                 mismatches.append(f"OCO止盈触发价不符（预期: {expected_tp}, 实际: {tp_trigger_px}）")
        elif sl_trigger_px:
            # 校验止损单
            if expected_sl is None:
                mismatches.append("非预期的止损单（未设置止损价格）")
            elif sl_trigger_px != expected_sl:
                mismatches.append(
                    f"止损触发价不符（预期: {expected_sl}, 实际: {sl_trigger_px}）"
                )
        elif tp_trigger_px:
            # 校验止盈单
            if expected_tp is None:
                mismatches.append("非预期的止盈单（未设置止盈价格）")
            elif tp_trigger_px != expected_tp:
                mismatches.append(
                    f"止盈触发价不符（预期: {expected_tp}, 实际: {tp_trigger_px}）"
                )
        else:
            mismatches.append("未找到止损或止盈触发价")
        
        # 3. 处理校验结果
        if not mismatches:
            result["success"] = True
            result["order"] = {
                "algo_cl_ord_id": algo_cl_ord_id,
                "algo_id": order_data.get("algoId"),
                "details": order_data
            }
            logger.info(f"✅ 订单 {algo_cl_ord_id} 匹配成功")
        else:
            result["reason"] = mismatches
            logger.warning(f"❌ 订单 {algo_cl_ord_id} 参数不匹配: {mismatches}")
    
    # 处理超时未找到的情况
    if not found:
        result["error"] = f"超时未找到订单 {algo_cl_ord_id}"
        logger.error(result["error"])
    
    return result


def run_short_sl_tp_test():
    """运行空单止盈止损测试流程（修复版）"""
    logger.info("🚀 开始空单止盈止损测试流程")
    logger.info("=" * 60)
    
    # 1. 设置交易所
    if not setup_exchange():
        logger.error("❌ 交易所设置失败，测试中止")
        return False
    
    # 2. 获取当前价格
    current_price = get_current_price()
    if current_price == 0:
        logger.error("❌ 无法获取当前价格，测试中止")
        return False
    
    # 3. 计算仓位大小
    position_size = get_safe_position_size()
    
    logger.info(f"📋 测试参数:")
    logger.info(f"   交易对: {config.symbol}")
    logger.info(f"   仓位大小: {position_size} 张")
    logger.info(f"   当前价格: {current_price:.2f}")
    
    # 阶段1: 开空单并设置止盈止损
    logger.info("")
    logger.info("🔹 阶段1: 开空单并设置止盈止损")
    logger.info("-" * 40)

    stop_loss_price, take_profit_price = calculate_stop_loss_take_profit_prices('sell', current_price)
    cancel_existing_orders()

    # 创建订单（简化版）
    short_order_result = create_order_with_sl_tp(
        side='sell',
        order_type='market',
        amount=position_size,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price
    )

    if not short_order_result['success']:
        logger.error("❌ 空单开仓失败")
        return False

    logger.info("⏳ 等待5秒后获取止盈止损信息...")
    time.sleep(5)

    # 保存用于后续查找的信息
    cl_order_id = short_order_result['clOrdId']
    saved_attach_algo_cl_ord_id = short_order_result['attachclOrdId']

    logger.info(f"💾 保存的订单信息:")
    logger.info(f"   clOrdId: {cl_order_id}")
    logger.info(f"   attachclOrdId: {saved_attach_algo_cl_ord_id}")

    # 等待空单持仓出现
    short_position = wait_for_position('short', 30)
    if not short_position:
        logger.error("❌ 空单持仓未找到")
        return False
    
    logger.info(f"✅ 空单持仓建立: {short_position['size']}张")

    # 阶段3: 取消现有止盈止损单
    logger.info("")
    logger.info("🔹 阶段3: 取消现有止盈止损单")
    logger.info("-" * 40)

    logger.info("⏳ 等待5秒后取消止盈止损单...")
    time.sleep(5)

    success = False

    if saved_attach_algo_cl_ord_id:
        algo_cl_ord_id = saved_attach_algo_cl_ord_id
        logger.info(f"🔧 进行止盈止损撤销操作")
        # 其次尝试使用我们自定义的ID
        if cancel_algo_order_by_attach_id(algo_cl_ord_id, get_correct_inst_id()):
            success = True
    else:
        logger.info("🔧 未发现需要撤销的止盈止损单")
        success = True

    if not success:
        logger.error("❌ 止盈止损单取消失败")
        return False

    # 确认止盈止损单已取消
    time.sleep(5)
    
    inst_id = get_correct_inst_id()
    if Is_sl_tp_canceled_with_instId(inst_id):
        logger.info("✅ 确认所有止盈止损单已取消")
    else:
        logger.warning("⚠️ 仍有止盈止损单存在，取消失败...")
        return False
    
    # 阶段4: 重新设置止盈止损单
    logger.info("")
    logger.info("🔹 阶段4: 等待7s重新设置止盈止损单")
    logger.info("-" * 40)
    time.sleep(7)
    
    new_sl, new_tp = calculate_stop_loss_take_profit_prices('short', short_position['entry_price'])
    logger.info(f"📊 重新计算止损: {new_sl:.2f}, 止盈: {new_tp:.2f}")
    
    sl_tp_set_result = sl_tp_algo_order_set(
        side="short",
        amount=short_position['size'],
        stop_loss_price=new_sl,
        take_profit_price=new_tp
    )

    time.sleep(2)
    if sl_tp_set_result['algo_id']:
        print(f"sltp订单创建成功，algo_id: {sl_tp_set_result['algo_id']}")
        
    if sl_tp_set_result["algo_cl_ord_id"] :
        sltp_confirm = confirm_algo_order_by_clId(
        side="short",
        amount=short_position['size'],
        take_profit_price=new_tp,
        stop_loss_price=new_sl,
        algo_cl_ord_id=sl_tp_set_result["algo_cl_ord_id"],  # 取止盈单ID
        timeout=60
    )

    if sltp_confirm["success"]:
        logger.info("所有止损止盈单均确认正确下发")
    else:
        if not sltp_confirm["success"]:
            logger.error(f"止损止盈单验证失败: {sltp_confirm['error'] or sltp_confirm['reason']}")

    # 阶段5: 等待后平仓
    logger.info("")
    logger.info("🔹 阶段5: 等待后平仓")
    logger.info("-" * 40)
    
    logger.info("⏳ 等待5秒...")
    time.sleep(5)

    # 阶段6: 平仓
    logger.info("")
    logger.info("🔹 阶段6: 平仓")
    logger.info("-" * 40)

    result = close_position_universal(side='sell', ord_type = 'market', amount = short_position['size'])
    if result['success']:
        print(f"市价平{short_position['size']}张空单成功，订单ID: {result['order_id']},clid:{result['cl_ord_id']}")
    close_order_id = result['order_id']
    
    if close_order_id:
        if not wait_for_order_fill(close_order_id, 30):
            logger.error("❌ 限价平仓未成交，尝试市价平仓")
            try:
                exchange.cancel_order(close_order_id, config.symbol)
            except Exception as e:
                logger.error(f"取消限价单失败: {str(e)}")
            
            close_result = close_position('short', short_position['size'], cancel_sl_tp=True)
            if not close_result:
                logger.error("❌ 市价平仓失败")
                return False
    else:
        close_result = close_position('short', short_position['size'], cancel_sl_tp=True)
        if not close_result:
            logger.error("❌ 市价平仓失败")
            return False

    # 阶段7: 确认仓位已平
    logger.info("")
    logger.info("🔹 阶段7: 确认仓位已平")
    logger.info("-" * 40)
    
    if not verify_position_closed():
        logger.error("❌ 仓位未完全平掉")
        return False

    # 阶段8: 清理剩余止盈止损单
    logger.info("")
    logger.info("🔹 阶段8: 清理剩余止盈止损单")
    logger.info("-" * 40)
    
    if check_sl_tp_orders():
        logger.warning("⚠️ 发现平仓后仍有止盈止损订单")
        if cancel_all_sl_tp_orders():
            logger.info("✅ 止盈止损订单清理成功")
        else:
            logger.error("❌ 止盈止损订单清理失败")
            return False
    else:
        logger.info("✅ 平仓后无剩余止盈止损订单")

    # 最终确认
    logger.info("")
    logger.info("🔹 最终状态确认")
    logger.info("-" * 40)
    
    final_position = get_current_position()
    if final_position:
        logger.error(f"❌ 最终检查发现仍有持仓: {final_position}")
        return False
    
    logger.info("✅ 所有检查通过!")
    logger.info("🎉 空单止盈止损测试流程完成!")
    return True

def main():
    """主函数"""
    try:
        logger.info("=" * 60)
        logger.info("🔧 BTC空单止盈止损测试程序")
        logger.info("=" * 60)
        
        # 更新配置参数
        config.leverage = 3
        config.base_usdt_amount = 5
        config.stop_loss_percent = 0.01
        config.take_profit_percent = 0.01
        config.wait_time_seconds = 5
        
        logger.info("📋 测试配置:")
        logger.info(f"   交易对: {config.symbol}")
        logger.info(f"   杠杆: {config.leverage}x")
        logger.info(f"   保证金: {config.base_usdt_amount} USDT")
        logger.info(f"   止损止盈: {config.stop_loss_percent*100}%")
        
        # 运行测试
        success = run_short_sl_tp_test()
        
        logger.info("🧹 执行测试后清理...")
        cleanup_after_test()
        
        if success:
            logger.info("🎊 测试成功完成!")
        else:
            logger.error("💥 测试失败!")
            
    except KeyboardInterrupt:
        logger.info("🛑 用户中断测试")
        # cleanup_after_test()
    except Exception as e:
        logger.error(f"💥 测试程序异常: {str(e)}")
        cleanup_after_test()
        traceback.print_exc()

if __name__ == "__main__":
    main()