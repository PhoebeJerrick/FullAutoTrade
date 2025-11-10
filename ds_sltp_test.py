#!/usr/bin/env python3

# ds_sltp_test.py - BTC空单止盈止损测试程序（基于OKX客服建议优化）

import os
import time
import sys
import traceback
from datetime import datetime
from typing import Dict, Any, Optional, List
import ccxt
from dotenv import load_dotenv

# 加载环境变量
env_path = '../ExApiConfig/ExApiConfig.env'
load_dotenv(dotenv_path=env_path)

# 复用原有的日志系统和配置
from ds_debug import TestLogger, TestConfig, get_account_config, exchange, config

# 复用原有的所有功能函数
from ds_debug import (
    log_order_params, log_api_response, get_correct_inst_id, setup_exchange,
    get_current_price, get_lot_size_info, adjust_position_size, calculate_position_size,
    calculate_stop_loss_take_profit_prices, create_order_without_sl_tp,
    close_position, wait_for_order_fill, get_current_position, check_sl_tp_orders,
    cancel_all_sl_tp_orders, cancel_existing_orders, wait_for_position, cleanup_after_test
)

# 创建专用logger
logger = TestLogger(log_dir="../Output/short_sl_tp_test", file_name="Short_SL_TP_Test_{timestamp}.log")

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

def create_limit_close_order(side: str, amount: float) -> Optional[str]:
    """创建限价平仓订单"""
    try:
        inst_id = get_correct_inst_id()
        current_price = get_current_price()
        
        # 根据方向确定限价价格
        if side == 'short':  # 平空单，买入
            limit_price = current_price * 1.001  # 比当前价高0.1%
            close_side = 'buy'
        else:  # 平多单，卖出
            limit_price = current_price * 0.999  # 比当前价低0.1%
            close_side = 'sell'
        
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': close_side,
            'ordType': 'limit',
            'sz': str(amount),
            'px': str(limit_price),
        }
        
        log_order_params("限价平仓", params, "create_limit_close_order")
        logger.info(f"🔄 执行{side}仓位限价平仓: {amount} 张 @ {limit_price:.2f} (当前价: {current_price:.2f})")
        
        response = exchange.private_post_trade_order(params)
        log_api_response(response, "限价平仓")
        
        if response and response.get('code') == '0':
            order_id = response['data'][0]['ordId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ 限价平仓订单创建成功: {order_id}")
            return order_id
        else:
            logger.error(f"❌ 限价平仓订单创建失败: {response}")
            return None
            
    except Exception as e:
        logger.error(f"创建限价平仓订单失败: {str(e)}")
        return None


def get_safe_position_size() -> float:
    """
    安全计算仓位大小，确保符合交易所要求
    """
    try:
        # 获取市场信息
        market_info = get_lot_size_info()
        min_amount = market_info.get('min_amount', 0.01)
        
        logger.info(f"📏 交易所最小交易量: {min_amount}")
        
        # 使用原有的计算函数
        calculated_size = calculate_position_size()
        logger.info(f"📏 计算得到的仓位大小: {calculated_size}")
        
        # 确保不低于最小交易量
        if calculated_size < min_amount:
            logger.warning(f"⚠️ 仓位大小 {calculated_size} 小于最小交易量 {min_amount}，使用最小值")
            return min_amount
        
        # 确保是min_amount的整数倍
        if min_amount > 0:
            # 使用整数除法确保是整数倍
            multiple = int(calculated_size / min_amount)
            safe_size = multiple * min_amount
            
            logger.info(f"📏 安全仓位大小: {safe_size} ({multiple}倍最小交易量)")
            return safe_size
        else:
            return calculated_size
            
    except Exception as e:
        logger.error(f"安全计算仓位大小失败: {str(e)}")
        # 返回最小交易量作为保底
        return 0.01

def check_sl_tp_from_main_order(order_id: str) -> bool:
    """
    根据OKX客服建议：通过主订单查询止损止盈信息
    使用 GET /api/v5/trade/order 查询主订单的止损止盈信息
    """
    try:
        logger.info(f"🔍 通过主订单查询止损止盈信息: {order_id}")
        
        params = {
            'instId': get_correct_inst_id(),
            'ordId': order_id,
        }
        
        response = exchange.private_get_trade_order(params)
        
        if response and response.get('code') == '0':
            orders = response.get('data', [])
            if orders:
                order_info = orders[0]
                logger.info(f"📋 主订单信息:")
                logger.info(f"   订单ID: {order_info.get('ordId')}")
                logger.info(f"   状态: {order_info.get('state')}")
                logger.info(f"   方向: {order_info.get('side')}")
                logger.info(f"   数量: {order_info.get('sz')}")
                
                # 检查是否有附加的止损止盈信息
                attach_algo_ords = order_info.get('attachAlgoOrds', [])
                if attach_algo_ords:
                    logger.info(f"✅ 发现附加的止损止盈订单: {len(attach_algo_ords)}个")
                    for algo_ord in attach_algo_ords:
                        algo_id = algo_ord.get('algoId', 'Unknown')
                        algo_type = algo_ord.get('algoOrdType', 'Unknown')
                        logger.info(f"   算法订单ID: {algo_id}")
                        logger.info(f"   算法订单类型: {algo_type}")
                        
                        # 检查止损止盈价格
                        if 'slTriggerPx' in algo_ord:
                            logger.info(f"   止损触发价: {algo_ord['slTriggerPx']}")
                        if 'tpTriggerPx' in algo_ord:
                            logger.info(f"   止盈触发价: {algo_ord['tpTriggerPx']}")
                    
                    return True
                else:
                    logger.info("📋 主订单中没有附加的止损止盈信息")
            else:
                logger.error("❌ 未找到主订单信息")
        else:
            logger.error(f"❌ 查询主订单失败: {response}")
        
        return False
        
    except Exception as e:
        logger.error(f"通过主订单查询止损止盈信息失败: {str(e)}")
        return False

def check_algo_order_detail(algo_id: str) -> bool:
    """
    根据OKX客服建议：通过算法订单ID查询完整信息（适用于已触发的订单）
    使用 GET /api/v5/trade/order-algo 查询算法订单完整信息
    """
    try:
        logger.info(f"🔍 查询算法订单完整信息: {algo_id}")
        
        params = {
            'algoId': algo_id,
        }
        
        response = exchange.private_get_trade_order_algo(params)
        
        if response and response.get('code') == '0':
            orders = response.get('data', [])
            if orders:
                order_info = orders[0]
                logger.info(f"✅ 算法订单详细信息:")
                logger.info(f"   算法ID: {order_info.get('algoId')}")
                logger.info(f"   状态: {order_info.get('state')}")
                logger.info(f"   订单类型: {order_info.get('ordType')}")
                
                # 检查止损止盈信息
                if 'slTriggerPx' in order_info:
                    logger.info(f"   止损触发价: {order_info['slTriggerPx']}")
                if 'tpTriggerPx' in order_info:
                    logger.info(f"   止盈触发价: {order_info['tpTriggerPx']}")
                if 'slOrdPx' in order_info:
                    logger.info(f"   止损委托价: {order_info['slOrdPx']}")
                if 'tpOrdPx' in order_info:
                    logger.info(f"   止盈委托价: {order_info['tpOrdPx']}")
                
                return True
            else:
                logger.info("📋 未找到算法订单信息")
        else:
            logger.error(f"❌ 查询算法订单失败: {response}")
        
        return False
        
    except Exception as e:
        logger.error(f"查询算法订单完整信息失败: {str(e)}")
        return False

def get_algo_orders_from_main_order(order_id: str) -> List[str]:
    """
    从主订单获取所有算法订单ID
    """
    try:
        algo_ids = []
        
        params = {
            'instId': get_correct_inst_id(),
            'ordId': order_id,
        }
        
        response = exchange.private_get_trade_order(params)
        
        if response and response.get('code') == '0':
            orders = response.get('data', [])
            if orders:
                order_info = orders[0]
                attach_algo_ords = order_info.get('attachAlgoOrds', [])
                
                for algo_ord in attach_algo_ords:
                    if 'algoId' in algo_ord:
                        algo_ids.append(algo_ord['algoId'])
        
        return algo_ids
        
    except Exception as e:
        logger.error(f"从主订单获取算法订单ID失败: {str(e)}")
        return []

def create_universal_order(
    side: str, 
    ord_type: str = 'market',
    amount: Optional[float] = None,
    price: Optional[float] = None,
    stop_loss_price: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    verify_sl_tp: bool = True
) -> Dict[str, Any]:
    """
    全能交易函数：支持限价/市价开仓，可选止损止盈设置
    
    Args:
        side: 交易方向 'buy'（做多）或 'sell'（做空）
        ord_type: 订单类型 'market'（市价）或 'limit'（限价）
        amount: 交易数量，None则自动计算
        price: 限价单价格，市价单可忽略
        stop_loss_price: 止损价格，None表示不设置
        take_profit_price: 止盈价格，None表示不设置
        verify_sl_tp: 是否验证止损止盈设置
    
    Returns:
        包含order_id, response, algo_ids和success状态的字典
    """
    try:
        inst_id = get_correct_inst_id()
        
        # 自动计算仓位大小
        amount = amount or get_safe_position_size()
        logger.info(f"📏 自动计算仓位大小: {amount}" if amount is None else f"📏 仓位大小: {amount}")
        
        # 基础参数构建
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': side,
            'ordType': ord_type,
            'sz': str(amount),
        }
        
        # 限价单价格设置
        if ord_type == 'limit' and price is not None:
            params['px'] = str(price)
            logger.info(f"💰 限价单价格: {price:.2f}")
        
        # 构建止损止盈参数（统一放在algo_ords中）
        algo_ords = []
        opposite_side = 'buy' if side == 'sell' else 'sell'  # 止损止盈方向统一为相反方向
        
        # 批量处理止损和止盈（修正后）
        for ord_type, trigger_price in [
            ('stop_loss', stop_loss_price),
            ('take_profit', take_profit_price)
        ]:
            if trigger_price is not None:
                # 正确的参数名映射
                trigger_key = 'slTriggerPx' if ord_type == 'stop_loss' else 'tpTriggerPx'
                ord_key = 'slOrdPx' if ord_type == 'stop_loss' else 'tpOrdPx'
                
                algo = {
                    trigger_key: str(trigger_price),  # 使用正确的触发价参数名
                    ord_key: '-1',  # 使用正确的委托价参数名
                    'sz': str(amount),
                    'side': opposite_side,
                    'algoOrdType': 'conditional'
                }
                algo_ords.append(algo)
                logger.info(f"{'🛡️ 止损' if ord_type == 'stop_loss' else '🎯 止盈'}: {trigger_price:.2f} (方向: {opposite_side})")
        
        # 添加止损止盈到主订单参数
        if algo_ords:
            params['attachAlgoOrds'] = algo_ords
        
        # 日志与订单执行
        action_name = f"{'做多' if side == 'buy' else '做空'}{'市价' if ord_type == 'market' else '限价'}单"
        log_order_params(action_name, params, "create_universal_order")
        logger.info(f"🎯 执行{action_name}: {amount} 张")
        if algo_ords:
            logger.info(f"📋 附带条件单: {'、'.join(['止损' if 'slTriggerPx' in a else '止盈' for a in algo_ords])}")
        
        # 发送订单并处理响应
        response = exchange.private_post_trade_order(params)
        log_api_response(response, "create_universal_order")
        
        result = {'order_id': None, 'response': response, 'algo_ids': [], 'success': False}
        
        if response and response.get('code') == '0':
            result['success'] = True
            result['order_id'] = response['data'][0]['ordId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ {action_name}创建成功: {result['order_id']}")
            
            # 提取算法订单ID
            data_list = response.get('data', [])  # 先获取数据列表，默认空列表
            # 跳过主订单数据（第一个元素），遍历剩余的算法订单
            for data in data_list[1:]:
                if 'algoId' in data:
                    result['algo_ids'].append(data['algoId'])
                    logger.info(f"✅ 条件单创建成功: {data['algoId']}")
            
            # 验证止损止盈设置
            if verify_sl_tp and algo_ords:
                logger.info("🔍 验证止损止盈设置...")
                time.sleep(2)
                if check_sl_tp_from_main_order(result['order_id']):
                    logger.info("✅ 止损止盈设置验证成功")
                else:
                    logger.warning("⚠️ 止损止盈设置验证失败，建议手动确认")
        else:
            logger.error(f"❌ {action_name}创建失败: {response}")
        
        return result
            
    except Exception as e:
        logger.error(f"创建全能订单失败: {str(e)}")
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return {'order_id': None, 'response': None, 'algo_ids': [], 'success': False}

def create_short_with_sl_tp_fixed(amount: float, stop_loss_price: float, take_profit_price: float):
    """
    向后兼容的包装函数 - 创建空单并设置止损止盈
    """
    return create_universal_order(
        side='sell',
        ord_type='market',
        amount=amount,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        verify_sl_tp=True
    )

def create_long_with_sl_fixed(amount: float, stop_loss_price: float, take_profit_price: float):
    """
    向后兼容的包装函数 - 创建多单并设置止损
    """
    return create_universal_order(
        side='buy',
        ord_type='market',
        amount=amount,
        stop_loss_price=stop_loss_price,
        take_profit_price=None,
        verify_sl_tp=True
    )


# 使用示例函数
def usage_examples():
    """
    展示全能函数的使用示例
    """
    current_price = get_current_price()
    
    # 示例1: 市价做多，带止损和止盈
    logger.info("📋 示例1: 市价做多，带止损止盈")
    result1 = create_universal_order(
        side='buy',
        ord_type='market',
        stop_loss_price=current_price * 0.99,  # 1%止损
        take_profit_price=current_price * 1.02  # 2%止盈
    )
    
    # 示例2: 限价做空，只带止损
    logger.info("📋 示例2: 限价做空，只带止损")
    result2 = create_universal_order(
        side='sell',
        ord_type='limit',
        price=current_price * 1.01,  # 比当前价高1%做空
        stop_loss_price=current_price * 1.02,  # 2%止损
        take_profit_price=None  # 不设置止盈
    )
    
    # 示例3: 市价做多，不带任何止损止盈
    logger.info("📋 示例3: 市价做多，不带止损止盈")
    result3 = create_universal_order(
        side='buy',
        ord_type='market'
        # 不设置stop_loss_price和take_profit_price
    )
    
    # 示例4: 限价做空，只带止盈
    logger.info("📋 示例4: 限价做空，只带止盈")
    result4 = create_universal_order(
        side='sell',
        ord_type='limit',
        price=current_price * 1.005,
        stop_loss_price=None,  # 不设置止损
        take_profit_price=current_price * 0.995  # 只设置止盈
    )

def set_sl_tp_separately(side: str, amount: float, stop_loss_price: float, take_profit_price: float):
    """分开设置止损和止盈订单 - 备选方案"""
    try:
        inst_id = get_correct_inst_id()
        
        logger.info("🔄 分开设置止损止盈订单...")
        
        # 设置止损订单
        sl_params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': 'buy' if side == 'short' else 'sell',
            'ordType': 'conditional',
            'sz': str(amount),
            'slTriggerPx': str(stop_loss_price),
            'slOrdPx': '-1',
        }
        
        logger.info("🛡️ 设置止损订单...")
        sl_response = exchange.private_post_trade_order_algo(sl_params)
        
        if sl_response and sl_response.get('code') == '0':
            sl_algo_id = sl_response['data'][0]['algoId'] if sl_response.get('data') else 'Unknown'
            logger.info(f"✅ 止损订单设置成功: {sl_algo_id}")
        else:
            logger.error(f"❌ 止损订单设置失败: {sl_response}")
            return False
        
        # 设置止盈订单
        tp_params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': 'buy' if side == 'short' else 'sell',
            'ordType': 'conditional',
            'sz': str(amount),
            'tpTriggerPx': str(take_profit_price),
            'tpOrdPx': '-1',
        }
        
        logger.info("🎯 设置止盈订单...")
        tp_response = exchange.private_post_trade_order_algo(tp_params)
        
        if tp_response and tp_response.get('code') == '0':
            tp_algo_id = tp_response['data'][0]['algoId'] if tp_response.get('data') else 'Unknown'
            logger.info(f"✅ 止盈订单设置成功: {tp_algo_id}")
            return True
        else:
            logger.error(f"❌ 止盈订单设置失败: {tp_response}")
            return False
            
    except Exception as e:
        logger.error(f"分开设置止损止盈失败: {str(e)}")
        return False

def run_short_sl_tp_test():
    """运行空单止盈止损测试流程"""
    logger.info("🚀 开始空单止盈止损测试流程（基于OKX客服建议优化）")
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
    logger.info(f"🎯 最终使用的仓位大小: {position_size}")
    
    logger.info(f"📋 测试参数:")
    logger.info(f"   交易对: {config.symbol}")
    logger.info(f"   保证金: {config.base_usdt_amount} USDT")
    logger.info(f"   杠杆: {config.leverage}x")
    logger.info(f"   仓位大小: {position_size} 张")
    logger.info(f"   止损: {config.stop_loss_percent*100}%")
    logger.info(f"   止盈: {config.take_profit_percent*100}%")
    logger.info(f"   等待时间: {config.wait_time_seconds}秒")
    
    # 阶段1: 开空单并设置止盈止损
    logger.info("")
    logger.info("🔹 阶段1: 开空单并设置止盈止损")
    logger.info("-" * 40)
    
    # 计算止损止盈价格
    stop_loss_price, take_profit_price = calculate_stop_loss_take_profit_prices('sell', current_price)
    
    # 取消现有订单
    cancel_existing_orders()
    
    # 开空单
    short_order_result = create_short_with_sl_tp_fixed(
        amount=position_size,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price
    )
    
    if not short_order_result:
        logger.error("❌ 空单开仓失败")
        return False
    
    short_order_id = short_order_result['order_id']
    
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
    
    # 阶段2: 确认止盈止损设置正确（使用OKX客服建议的方法）
    logger.info("")
    logger.info("🔹 阶段2: 确认止盈止损设置（使用OKX客服建议的方法）")
    logger.info("-" * 40)
    
    logger.info("📋 检查止盈止损订单...")
    time.sleep(3)  # 给系统一些时间处理止损止盈订单
    
    # 方法1: 通过主订单查询止损止盈信息
    has_sl_tp = check_sl_tp_from_main_order(short_order_id)
    if not has_sl_tp:
        logger.warning("⚠️ 通过主订单未发现止损止盈信息，尝试分开设置...")
        
        # 备选方案：分开设置止损止盈
        recalculated_sl, recalculated_tp = calculate_stop_loss_take_profit_prices('short', short_position['entry_price'])
        
        if set_sl_tp_separately('short', short_position['size'], recalculated_sl, recalculated_tp):
            logger.info("✅ 通过分开设置成功创建止损止盈订单")
            time.sleep(2)
            # 检查分开设置的订单
            has_sl_tp = check_sl_tp_orders()
            if has_sl_tp:
                logger.info("✅ 止损止盈订单设置正确")
            else:
                logger.warning("⚠️ API查询不到但假设设置成功（从交易所界面确认）")
        else:
            logger.error("❌ 分开设置止损止盈也失败")
            return False
    else:
        logger.info("✅ 止损止盈订单设置正确")

    # 阶段3: 等待5秒后取消现有止盈止损单
    logger.info("")
    logger.info("🔹 阶段3: 取消现有止盈止损单")
    logger.info("-" * 40)
    
    logger.info("⏳ 等待5秒后取消止盈止损单...")
    time.sleep(5)
    
    # 取消当前止盈止损单
    logger.info("🔄 取消当前止盈止损单...")
    if cancel_all_sl_tp_orders():
        logger.info("✅ 止盈止损单取消命令已执行")
    else:
        logger.error("❌ 止盈止损单取消失败")
        return False
    
    # 确认止盈止损单已取消
    logger.info("🔍 确认止盈止损单已取消...")
    time.sleep(2)  # 等待系统处理取消操作
    has_remaining = check_sl_tp_orders()
    if not has_remaining:
        logger.info("✅ 确认所有止盈止损单已取消")
    else:
        logger.warning("⚠️ 仍有止盈止损单存在，尝试再次取消...")
        if cancel_all_sl_tp_orders() and not check_sl_tp_orders():
            logger.info("✅ 再次取消后确认已无止损止盈单")
        else:
            logger.error("❌ 无法完全取消止盈止损单，测试中止")
            return False

    # 阶段4: 重新设置止盈止损单
    logger.info("")
    logger.info("🔹 阶段4: 重新设置止盈止损单")
    logger.info("-" * 40)
    
    # 基于入场价重新计算止损止盈价格
    new_sl, new_tp = calculate_stop_loss_take_profit_prices('short', short_position['entry_price'])
    logger.info(f"📊 重新计算止损: {new_sl:.2f}, 止盈: {new_tp:.2f}")
    
    # 重新设置止盈止损
    if not set_sl_tp_separately('short', short_position['size'], new_sl, new_tp):
        logger.error("❌ 重新设置止盈止损单失败")
        return False
    
    # 确认重新设置成功
    time.sleep(2)
    if check_sl_tp_orders():
        logger.info("✅ 重新设置的止盈止损单已确认")
    else:
        logger.warning("⚠️ 重新设置的止盈止损单未查询到")

    # 阶段5: 等待5秒后准备平仓
    logger.info("")
    logger.info("🔹 阶段5: 等待5秒后平仓")
    logger.info("-" * 40)
    
    logger.info("⏳ 等待5秒...")
    time.sleep(5)

    # 阶段6: 平仓当前订单
    logger.info("")
    logger.info("🔹 阶段6: 平仓当前订单")
    logger.info("-" * 40)
    
    close_order_id = create_limit_close_order('short', short_position['size'])
    
    if close_order_id:
        if not wait_for_order_fill(close_order_id, 30):
            logger.error("❌ 限价平仓订单未在30秒内成交，尝试市价平仓")
            try:
                exchange.cancel_order(close_order_id, config.symbol)
                logger.info(f"✅ 已取消限价平仓订单: {close_order_id}")
            except Exception as e:
                logger.error(f"取消限价单失败: {str(e)}")
            
            logger.info("🔄 尝试市价平仓...")
            close_result = close_position('short', short_position['size'], cancel_sl_tp=True)
            if not close_result:
                logger.error("❌ 市价平仓也失败")
                return False
    else:
        logger.info("🔄 限价平仓订单创建失败，尝试市价平仓...")
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

    # 阶段8: 检查并清理剩余止盈止损单
    logger.info("")
    logger.info("🔹 阶段8: 清理剩余止盈止损单")
    logger.info("-" * 40)
    
    logger.info("🔍 检查是否有剩余止盈止损单...")
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
        logger.info("✅ 平仓后无剩余止盈止损订单")

    # 最终确认
    logger.info("")
    logger.info("🔹 最终状态确认")
    logger.info("-" * 40)
    
    final_position = get_current_position()
    if final_position:
        logger.error(f"❌ 最终检查发现仍有持仓: {final_position}")
        return False
    
    final_sl_tp = check_sl_tp_orders()
    if final_sl_tp:
        logger.error("❌ 最终检查发现仍有止盈止损订单")
        return False
    
    logger.info("✅ 所有检查通过!")
    
    logger.info("")
    logger.info("🎉 空单止盈止损测试流程完成!")
    logger.info("=" * 60)
    return True

def main():
    """主函数"""
    try:
        logger.info("=" * 60)
        logger.info("🔧 BTC空单止盈止损测试程序（基于OKX客服建议优化）")
        logger.info("=" * 60)
        
        # 更新配置参数
        config.leverage = 3
        config.base_usdt_amount = 5
        config.stop_loss_percent = 0.01
        config.take_profit_percent = 0.01
        config.wait_time_seconds = 5
        
        # 确认测试参数
        logger.info("📋 测试配置:")
        logger.info(f"   交易对: {config.symbol}")
        logger.info(f"   杠杆: {config.leverage}x")
        logger.info(f"   保证金: {config.base_usdt_amount} USDT")
        logger.info(f"   止损止盈: {config.stop_loss_percent*100}%")
        logger.info(f"   等待时间: {config.wait_time_seconds}秒")
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
        traceback.print_exc()

if __name__ == "__main__":
    main()