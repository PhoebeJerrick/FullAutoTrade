#!/usr/bin/env python3

# ds_short_sl_tp_test.py - BTC空单止盈止损测试程序（基于原有稳定框架）
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

# 复用原有的日志系统和配置
from ds_debug import TestLogger, TestConfig, get_account_config, exchange, config

# 复用原有的所有功能函数
from ds_debug import (
    log_order_params, log_api_response, get_correct_inst_id, setup_exchange,
    get_current_price, get_lot_size_info, adjust_position_size, calculate_position_size,
    calculate_stop_loss_take_profit_prices, create_order_with_sl_tp, create_order_without_sl_tp,
    close_position, wait_for_order_fill, get_current_position, check_sl_tp_orders,
    cancel_all_sl_tp_orders, cancel_existing_orders, wait_for_position, verify_position_closed,
    cleanup_after_test
)

# 创建专用logger
logger = TestLogger(log_dir="../Output/short_sl_tp_test", file_name="Short_SL_TP_Test_{timestamp}.log")

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
    
    # 再次确认仓位大小符合要求
    market_info = get_lot_size_info()
    min_amount = market_info['min_amount']
    if position_size < min_amount:
        logger.warning(f"⚠️ 仓位大小 {position_size} 小于最小交易量 {min_amount}，使用最小值")
        position_size = min_amount
    
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
    
    # 使用原有的稳定函数开空单
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
    
    # 使用原有的平仓函数，但使用限价方式
    # 首先获取当前价格
    current_price = get_current_price()
    # 使用比当前价格稍低的价格来确保快速成交
    limit_price = current_price * 0.999
    
    # 创建限价平仓单
    inst_id = get_correct_inst_id()
    params = {
        'instId': inst_id,
        'tdMode': config.margin_mode,
        'side': 'buy',  # 平空单
        'ordType': 'limit',
        'sz': str(short_position['size']),
        'px': str(limit_price),
    }
    
    log_order_params("限价平仓", params, "run_short_sl_tp_test")
    logger.info(f"🔄 执行空单限价平仓: {short_position['size']} 张 @ {limit_price:.2f}")
    
    close_response = exchange.private_post_trade_order(params)
    log_api_response(close_response, "限价平仓")
    
    if not close_response or close_response.get('code') != '0':
        logger.error("❌ 限价平仓订单创建失败")
        # 如果限价平仓失败，尝试市价平仓
        logger.info("🔄 尝试市价平仓...")
        close_result = close_position('short', short_position['size'], cancel_sl_tp=True)
        if not close_result:
            logger.error("❌ 市价平仓也失败")
            return False
    else:
        close_order_id = close_response['data'][0]['ordId']
        logger.info(f"✅ 限价平仓订单创建成功: {close_order_id}")
        
        # 等待平仓成交
        if not wait_for_order_fill(close_order_id, 30):
            logger.error("❌ 限价平仓订单未在30秒内成交，尝试市价平仓")
            # 取消限价单并市价平仓
            exchange.cancel_order(close_order_id, config.symbol)
            close_position('short', short_position['size'], cancel_sl_tp=True)
    
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