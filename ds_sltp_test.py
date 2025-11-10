#!/usr/bin/env python3

# ds_sltp_test.py - BTC空单止盈止损测试程序（基于原有稳定框架）

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
    """创建限价平仓订单 - 改进版本"""
    try:
        inst_id = get_correct_inst_id()
        current_price = get_current_price()
        
        # 根据方向确定限价价格 - 使用更合理的价格
        if side == 'short':  # 平空单，买入
            # 对于空单平仓，使用比当前价格稍高的价格，确保快速成交
            limit_price = current_price * 1.001  # 比当前价高0.1%
            close_side = 'buy'
        else:  # 平多单，卖出
            # 对于多单平仓，使用比当前价格稍低的价格
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

def enforce_lot_size_requirement(position_size: float) -> float:
    """
    强制确保仓位大小符合交易所的lot size要求
    """
    try:
        # 获取市场信息
        market_info = get_lot_size_info()
        min_amount = market_info.get('min_amount', 0.001)
        
        logger.info(f"📏 交易所最小交易量: {min_amount}")
        logger.info(f"📏 原始仓位大小: {position_size}")
        
        # 确保仓位大小是最小交易量的整数倍
        if min_amount > 0:
            # 计算最接近的整数倍
            multiple = round(position_size / min_amount)
            enforced_size = multiple * min_amount
            
            # 确保不低于最小交易量
            if enforced_size < min_amount:
                enforced_size = min_amount
            
            logger.info(f"📏 调整后仓位大小: {enforced_size} ({multiple}倍最小交易量)")
            
            return enforced_size
        else:
            return position_size
            
    except Exception as e:
        logger.error(f"强制调整仓位大小失败: {str(e)}")
        return position_size

def create_short_with_sl_tp_fixed(amount: float, stop_loss_price: float, take_profit_price: float):
    """
    修复版的创建空单并设置止损止盈函数
    """
    try:
        inst_id = get_correct_inst_id()
        
        # 基础参数 - 空单开仓
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': 'sell',  # 空单
            'ordType': 'market',
            'sz': str(amount),
        }
        
        # 修复：正确设置止损止盈参数
        # 对于空单，止损是价格上涨到某个价位，止盈是价格下跌到某个价位
        # 平仓方向与开仓方向相反：空单平仓是买入
        params['attachAlgoOrds'] = [
            {
                'tpTriggerPx': str(take_profit_price),  # 止盈触发价格
                'tpOrdPx': '-1',  # 市价止盈
                'slTriggerPx': str(stop_loss_price),    # 止损触发价格  
                'slOrdPx': '-1',  # 市价止损
                'sz': str(amount),
                'side': 'buy',  # 空单的止损止盈方向是买入平仓
                'algoOrdType': 'conditional'
            }
        ]
        
        log_order_params("空单带止损止盈(修复版)", params, "create_short_with_sl_tp_fixed")
        logger.info(f"🎯 执行空单开仓: {amount} 张")
        logger.info(f"🛡️ 止损价格: {stop_loss_price:.2f}")
        logger.info(f"🎯 止盈价格: {take_profit_price:.2f}")
        
        # 创建订单
        response = exchange.private_post_trade_order(params)
        
        log_api_response(response, "create_short_with_sl_tp_fixed")
        
        if response and response.get('code') == '0':
            order_id = response['data'][0]['ordId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ 空单创建成功: {order_id}")
            
            # 检查是否有止损止盈订单信息
            if 'attachAlgoOrds' in params and response.get('data'):
                for algo_ord in response['data']:
                    if 'algoId' in algo_ord:
                        logger.info(f"✅ 止损止盈订单创建成功: {algo_ord['algoId']}")
            
            return response
        else:
            logger.error(f"❌ 空单创建失败: {response}")
            return None
            
    except Exception as e:
        logger.error(f"空单开仓失败: {str(e)}")
        import traceback
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return None

def set_sl_tp_separately(side: str, amount: float, stop_loss_price: float, take_profit_price: float):
    """
    分开设置止损和止盈订单 - 备选方案
    """
    try:
        inst_id = get_correct_inst_id()
        
        logger.info("🔄 分开设置止损止盈订单...")
        
        # 设置止损订单
        sl_params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': 'buy' if side == 'short' else 'sell',  # 平仓方向
            'ordType': 'conditional',
            'sz': str(amount),
            'slTriggerPx': str(stop_loss_price),
            'slOrdPx': '-1',  # 市价止损
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
            'side': 'buy' if side == 'short' else 'sell',  # 平仓方向
            'ordType': 'conditional',
            'sz': str(amount),
            'tpTriggerPx': str(take_profit_price),
            'tpOrdPx': '-1',  # 市价止盈
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
    """
    运行空单止盈止损测试流程 - 基于原有稳定框架
    """
    logger.info("🚀 开始空单止盈止损测试流程（基于稳定框架）")
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
    
    # 3. 计算仓位大小（使用原有的稳定函数）
    position_size = calculate_position_size()
    logger.info(f"📏 计算得到的仓位大小: {position_size}")
    
    # 4. 强制确保仓位大小符合lot size要求
    position_size = enforce_lot_size_requirement(position_size)
    
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
    
    # 使用修复版的函数开空单
    short_order_result = create_short_with_sl_tp_fixed(
        amount=position_size,
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
    
    # 阶段2: 确认止盈止损设置正确
    logger.info("")
    logger.info("🔹 阶段2: 确认止盈止损设置")
    logger.info("-" * 40)
    
    logger.info("📋 检查止盈止损订单...")
    time.sleep(3)  # 给系统一些时间处理止损止盈订单
    
    has_sl_tp = check_sl_tp_orders()
    if not has_sl_tp:
        logger.warning("⚠️ 未发现止损止盈订单，尝试分开设置...")
        
        # 备选方案：分开设置止损止盈
        recalculated_sl, recalculated_tp = calculate_stop_loss_take_profit_prices('short', short_position['entry_price'])
        
        if set_sl_tp_separately('short', short_position['size'], recalculated_sl, recalculated_tp):
            logger.info("✅ 通过分开设置成功创建止损止盈订单")
            time.sleep(2)  # 等待订单处理
            has_sl_tp = check_sl_tp_orders()
            if has_sl_tp:
                logger.info("✅ 止损止盈订单设置正确")
            else:
                logger.error("❌ 即使分开设置也未能创建止损止盈订单")
                return False
        else:
            logger.error("❌ 分开设置止损止盈也失败")
            return False
    else:
        logger.info("✅ 止损止盈订单设置正确")
    
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
    
    # 使用改进的限价平仓
    close_order_id = create_limit_close_order('short', short_position['size'])
    
    if close_order_id:
        # 等待限价平仓成交
        if not wait_for_order_fill(close_order_id, 30):
            logger.error("❌ 限价平仓订单未在30秒内成交，尝试市价平仓")
            # 取消限价单
            try:
                exchange.cancel_order(close_order_id, config.symbol)
                logger.info(f"✅ 已取消限价平仓订单: {close_order_id}")
            except Exception as e:
                logger.error(f"取消限价单失败: {str(e)}")
            
            # 使用市价平仓
            logger.info("🔄 尝试市价平仓...")
            close_result = close_position('short', short_position['size'], cancel_sl_tp=True)
            if not close_result:
                logger.error("❌ 市价平仓也失败")
                return False
    else:
        # 限价单创建失败，直接使用市价平仓
        logger.info("🔄 限价平仓订单创建失败，尝试市价平仓...")
        close_result = close_position('short', short_position['size'], cancel_sl_tp=True)
        if not close_result:
            logger.error("❌ 市价平仓失败")
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
    logger.info("=" * 60)
    return True

def main():
    """主函数"""
    try:
        logger.info("=" * 60)
        logger.info("🔧 BTC空单止盈止损测试程序（基于稳定框架）")
        logger.info("=" * 60)
        
        # 更新配置参数
        config.leverage = 3  # 使用较低杠杆
        config.base_usdt_amount = 5  # 使用5USDT保证金
        config.stop_loss_percent = 0.01  # 1%止损
        config.take_profit_percent = 0.01  # 1%止盈
        config.wait_time_seconds = 5  # 等待5秒
        
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
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()