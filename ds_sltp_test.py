#!/usr/bin/env python3

# ds_sltp_test.py - BTC空单止盈止损测试程序（基于OKX客服建议优化）

import os
import time
import sys
import traceback
import uuid
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

def generate_cl_ord_id(side: str) -> str:
    """
    生成符合OKX规范的clOrdId：
    - 仅包含字母和数字
    - 长度1-32位
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

def create_limit_close_order(side: str, amount: float) -> Optional[str]:
    """创建限价平仓订单"""
    try:
        inst_id = get_correct_inst_id()
        current_price = get_current_price()
        
        if side == 'short':
            limit_price = current_price * 1.001
            close_side = 'buy'
        else:
            limit_price = current_price * 0.999
            close_side = 'sell'
        
        cl_ord_id = generate_cl_ord_id(side)
        
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': close_side,
            'ordType': 'limit',
            'sz': str(amount),
            'px': str(limit_price),
            'clOrdId': cl_ord_id
        }
        
        log_order_params("限价平仓", params, "create_limit_close_order")
        logger.info(f"🔄 执行{side}仓位限价平仓: {amount} 张 @ {limit_price:.2f} (当前价: {current_price:.2f})")
        
        response = exchange.private_post_trade_order(params)
        log_api_response(response, "限价平仓")
        
        if response and response.get('code') == '0':
            order_id = response['data'][0]['ordId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ 限价平仓订单创建成功: {order_id} (自定义ID: {cl_ord_id})")
            return order_id
        else:
            logger.error(f"❌ 限价平仓订单创建失败: {response}")
            return None
            
    except Exception as e:
        logger.error(f"创建限价平仓订单失败: {str(e)}")
        return None

def get_order_comprehensive_info(main_ord_id: str) -> Dict[str, any]:
    """
    获取订单综合信息（修复版）
    重点修复：正确识别和处理附带止盈止损单
    """
    result = {
        "main_order_state": None,
        "attach_algo_ids": [],
        "algo_orders_details": [],
        "has_valid_sl_tp": False,
        "success": False
    }
    
    try:
        inst_id = get_correct_inst_id()
        
        # 1. 查询主订单详情（核心信息源）
        logger.info(f"🔍 查询主订单详情: {main_ord_id}")
        main_order_params = {
            "instId": inst_id,
            "ordId": main_ord_id
        }
        
        main_order_resp = exchange.private_get_trade_order(main_order_params)
        
        # 打印完整的API响应信息
        logger.info("📋 主订单API完整响应:")
        logger.info(f"   响应码: {main_order_resp.get('code')}")
        logger.info(f"   响应消息: {main_order_resp.get('msg')}")
        logger.info(f"   数据条数: {len(main_order_resp.get('data', []))}")
        
        if main_order_resp.get('data'):
            data = main_order_resp['data'][0]
            logger.info("📋 主订单数据详情:")
            for key, value in data.items():
                logger.info(f"   {key}: {value}")
        
        if not main_order_resp or main_order_resp.get("code") != "0" or not main_order_resp.get("data"):
            logger.error("❌ 主订单查询失败")
            return result
        
        main_order_data = main_order_resp["data"][0]
        result["main_order_state"] = main_order_data.get("state")
        logger.info(f"   主订单状态: {result['main_order_state']}")
        
        # 从主订单中提取attach_algo_ids（附带止盈止损的唯一可靠来源）
        attach_algo_ords = main_order_data.get("attachAlgoOrds", [])
        valid_attach_ids = [ord.get("attachAlgoId") for ord in attach_algo_ords if ord.get("attachAlgoId")]
        result["attach_algo_ids"] = valid_attach_ids
        
        # 详细打印附带止盈止损信息
        if valid_attach_ids:
            logger.info(f"📋 主订单附带止盈止损详细信息:")
            for idx, algo_ord in enumerate(attach_algo_ords):
                logger.info(f"   止盈止损单 #{idx+1}:")
                logger.info(f"     attachAlgoId: {algo_ord.get('attachAlgoId')}")
                logger.info(f"     algoId: {algo_ord.get('algoId')}")
                logger.info(f"     algoClOrdId: {algo_ord.get('algoClOrdId')}")
                logger.info(f"     algoOrdType: {algo_ord.get('algoOrdType')}")
                logger.info(f"     止损触发价: {algo_ord.get('slTriggerPx', '未设置')}")
                logger.info(f"     止损委托价: {algo_ord.get('slOrdPx', '未设置')}")
                logger.info(f"     止盈触发价: {algo_ord.get('tpTriggerPx', '未设置')}")
                logger.info(f"     止盈委托价: {algo_ord.get('tpOrdPx', '未设置')}")
        else:
            logger.info("ℹ️ 未发现附带止盈止损单")

        # 关键修复：只要有attach_algo_ids就认为有有效的止损止盈设置
        if valid_attach_ids:
            # 检查触发价格是否有效（大于0）
            has_valid_trigger_prices = False
            for ord_info in attach_algo_ords:
                sl_trigger_px = ord_info.get("slTriggerPx")
                tp_trigger_px = ord_info.get("tpTriggerPx")
                
                # 检查是否有有效的触发价格（大于0）
                if (sl_trigger_px and float(sl_trigger_px) > 0) or (tp_trigger_px and float(tp_trigger_px) > 0):
                    has_valid_trigger_prices = True
                    break
            
            result["has_valid_sl_tp"] = has_valid_trigger_prices
            
            if has_valid_trigger_prices:
                logger.info(f"✅ 发现有效的附带止盈止损单: {valid_attach_ids}")
            else:
                logger.info(f"ℹ️ 发现附带止盈止损单但触发价格无效: {valid_attach_ids}")
        else:
            logger.info("ℹ️ 未发现有效的止损止盈设置")
            result["has_valid_sl_tp"] = False
        
        # 2. 只有当主订单已成交时，才查询已委托的止盈止损单（作为补充信息）
        if result["main_order_state"] == "filled":
            logger.info("🔍 查询已委托的止盈止损单（补充信息）")
            pending_params = {
                "instType": "SWAP",
                "instId": inst_id,
                # 移除ordType参数，因为conditional/oco不在这个接口中
            }
            
            pending_resp = exchange.private_get_trade_orders_pending(pending_params)
            
            # 打印已委托订单查询的完整响应
            if pending_resp:
                logger.info("📋 已委托订单API完整响应:")
                logger.info(f"   响应码: {pending_resp.get('code')}")
                logger.info(f"   响应消息: {pending_resp.get('msg')}")
                logger.info(f"   数据条数: {len(pending_resp.get('data', []))}")
            
            if pending_resp and pending_resp.get("code") == "0":
                # 筛选与当前主订单关联的已委托订单
                related_algos = []
                for order in pending_resp.get("data", []):
                    if order.get("attachOrdId") == main_ord_id:
                        related_algos.append({
                            "algoId": order.get("algoId"),
                            "ordType": order.get("ordType"),
                            "slTriggerPx": order.get("slTriggerPx", ""),
                            "tpTriggerPx": order.get("tpTriggerPx", "")
                        })
                
                result["algo_orders_details"] = related_algos
                if related_algos:
                    logger.info(f"   已委托止盈止损单: {len(related_algos)}个")
                    for idx, algo in enumerate(related_algos):
                        logger.info(f"     止盈止损单 #{idx+1}:")
                        logger.info(f"       algoId: {algo.get('algoId')}")
                        logger.info(f"       ordType: {algo.get('ordType')}")
                        logger.info(f"       止损触发价: {algo.get('slTriggerPx')}")
                        logger.info(f"       止盈触发价: {algo.get('tpTriggerPx')}")
                else:
                    logger.info("   未发现已委托的止盈止损单（可能在algo订单中）")
        
        result["success"] = True
        return result
        
    except Exception as e:
        logger.error(f"订单综合信息查询异常: {str(e)}")
        return result

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
        response = exchange.private_post_trade_amend_order(params)
        
        if response and response.get("code") == "0":
            logger.info(f"✅ 成功撤销未委托止盈止损: {attach_algo_id}")
            return True
        else:
            logger.error(f"❌ 修改失败: {response}")
            return False
    except Exception as e:
        logger.error(f"修改出错: {str(e)}")
        return False

def amend_traded_sl_tp(algo_id: str, algo_cl_ord_id: str, inst_id: str) -> bool:
    """适用于主订单完全成交，止盈止损已委托的场景"""
    try:
        # 确保 algo_cl_ord_id 是字符串，不是列表
        if isinstance(algo_cl_ord_id, list):
            if algo_cl_ord_id:
                algo_cl_ord_id = algo_cl_ord_id[0]  # 取第一个元素
            else:
                logger.error("❌ algo_cl_ord_id 列表为空")
                return False
        
        # 关键修复：直接使用我们自定义的algoClOrdId
        params = {
            "instId": inst_id,
            "algoClOrdId": algo_cl_ord_id,  # 使用我们自定义的algoClOrdId
            "slTriggerPx": "0",
            "tpTriggerPx": "0"
        }
        
        logger.info(f"🔄 [已成交阶段] 修改已委托止盈止损: algoClOrdId={algo_cl_ord_id}")
        response = exchange.private_post_trade_amend_algos(params)
        
        if response and response.get("code") == "0":
            logger.info(f"✅ 成功撤销已委托止盈止损: {algo_cl_ord_id}")
            return True
        else:
            logger.error(f"❌ 修改失败: {response}")
            return False
    except Exception as e:
        logger.error(f"修改出错: {str(e)}")
        return False

def cancel_attached_sl_tp_by_algo_ids(main_ord_id: str, attach_algo_ids: List[str], algo_cl_ord_ids: List[str], main_order_state: str) -> bool:
    """
    专门处理附带止盈止损单的撤销
    根据主订单状态选择正确的撤销方式
    """
    if not attach_algo_ids and not algo_cl_ord_ids:
        logger.info("✅ 没有需要撤销的附带止盈止损单")
        return True
        
    inst_id = get_correct_inst_id()
    success = True
    
    logger.info(f"🔧 开始撤销附带止盈止损单, 主订单状态: {main_order_state}")
    
    # 根据主订单状态选择撤销方式
    if main_order_state in ["live", "partially_filled"]:
        # 主订单未完全成交，使用amend-order接口和attach_algo_ids
        logger.info("🔄 使用amend-order接口撤销未成交止盈止损")
        for attach_algo_id in attach_algo_ids:
            if not amend_untraded_sl_tp(main_ord_id, attach_algo_id, inst_id):
                logger.error(f"❌ 附带止盈止损单撤销失败: {attach_algo_id}")
                success = False
            else:
                logger.info(f"✅ 附带止盈止损单撤销成功: {attach_algo_id}")
            time.sleep(1)
    elif main_order_state == "filled":
        # 主订单已完全成交，使用amend-algos接口和algo_cl_ord_ids
        logger.info("🔄 使用amend-algos接口撤销已委托止盈止损")
        for algo_cl_ord_id in algo_cl_ord_ids:
            if not amend_traded_sl_tp(None, algo_cl_ord_id, inst_id):
                logger.error(f"❌ 已委托止盈止损单撤销失败: {algo_cl_ord_id}")
                success = False
            else:
                logger.info(f"✅ 已委托止盈止损单撤销成功: {algo_cl_ord_id}")
            time.sleep(1)
    else:
        logger.warning(f"⚠️ 主订单状态为 {main_order_state}，无法确定撤销方式")
        success = False
    
    return success

def cancel_all_sl_tp_versatile(main_ord_id: str) -> bool:
    """全能撤销函数（修复版）- 正确处理附带止盈止损单"""
    if not main_ord_id:
        logger.error("❌ 必须提供主订单ID")
        return False
        
    # 获取订单综合信息（一次性查询）
    order_info = get_order_comprehensive_info(main_ord_id)
    if not order_info["success"]:
        logger.error("❌ 无法获取订单信息，撤销中止")
        return False
        
    main_state = order_info["main_order_state"]
    logger.info(f"📊 主订单{main_ord_id}当前状态: {main_state}")
    
    # 关键修复：只要有attach_algo_ids就执行撤销操作
    attach_algo_ids = order_info["attach_algo_ids"]
    has_attached_sl_tp = len(attach_algo_ids) > 0
    
    if not has_attached_sl_tp:
        logger.info("✅ 没有发现需要撤销的止盈止损单")
        return True
    
    # 在撤销前检查触发价格状态
    if attach_algo_ids:
        logger.info(f"🔧 检查附带止盈止损单的触发价格状态...")
        for attach_algo_id in attach_algo_ids:
            # 这里可以添加更详细的触发价格检查日志
            logger.info(f"   准备撤销附带止盈止损单: {attach_algo_id}")

    # 关键修复：使用保存的attach_algo_ids进行撤销
    if saved_attach_algo_ids:
        logger.info(f"🔧 使用保存的attach_algo_ids进行撤销: {saved_attach_algo_ids}")
        success = cancel_attached_sl_tp_by_algo_ids(short_order_id, saved_attach_algo_ids, main_state)  # 传入主订单状态
    else:
        logger.info("🔧 通过查询获取attach_algo_ids进行撤销")
        success = cancel_all_sl_tp_versatile(short_order_id)

    time.sleep(2)
    
    # 验证撤销结果
    if success:
        logger.info("✅ 止盈止损单撤销操作完成")
        
        # 再次查询确认
        verify_info = get_order_comprehensive_info(main_ord_id)
        if verify_info["success"] and len(verify_info["attach_algo_ids"]) == 0:
            logger.info("✅ 确认所有止盈止损单已成功撤销")
        else:
            logger.warning("⚠️ 止盈止损单可能未完全撤销，建议手动确认")
            
        return True
    else:
        logger.error("❌ 止盈止损单撤销失败")
        return False

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
    """
    try:
        inst_id = get_correct_inst_id()
        amount = amount or get_safe_position_size()
        cl_ord_id = generate_cl_ord_id(side)
        
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': side,
            'ordType': ord_type,
            'sz': str(amount),
            'clOrdId': cl_ord_id
        }
        
        if ord_type == 'limit' and price is not None:
            params['px'] = str(price)
            logger.info(f"💰 限价单价格: {price:.2f}")
                
        algo_ords = []
        opposite_side = 'buy' if side == 'sell' else 'sell'
        algo = {}

        if stop_loss_price is not None:
            algo['slTriggerPx'] = str(stop_loss_price)
            algo['slOrdPx'] = '-1'
            logger.info(f"🛡️ 止损: {stop_loss_price:.2f}")

        if take_profit_price is not None:
            algo['tpTriggerPx'] = str(take_profit_price)
            algo['tpOrdPx'] = '-1'
            logger.info(f"🎯 止盈: {take_profit_price:.2f}")

        if algo:
            algo['sz'] = str(amount)
            algo['side'] = opposite_side
            algo['algoOrdType'] = 'conditional'
            algo['algoClOrdId'] = generate_cl_ord_id(side)
            algo_ords.append(algo)  
        
        if algo_ords:
            params['attachAlgoOrds'] = algo_ords
        
        action_name = f"{'做多' if side == 'buy' else '做空'}{'市价' if ord_type == 'market' else '限价'}单"
        log_order_params(action_name, params, "create_universal_order")
        logger.info(f"🎯 执行{action_name}: {amount} 张")
        
        response = exchange.private_post_trade_order(params)
        log_api_response(response, "create_universal_order")
        
        result = {
            'order_id': None, 
            'cl_ord_id': cl_ord_id,
            'response': response, 
            'algo_ids': [], 
            'algo_cl_ord_ids': [],
            'attach_algo_ids': [],  # 新增：保存attach_algo_ids
            'sl_trigger_px': stop_loss_price,  # 保存止损触发价格
            'tp_trigger_px': take_profit_price,  # 保存止盈触发价格
            'success': False
        }
        
        if response and response.get('code') == '0':
            result['success'] = True
            result['order_id'] = response['data'][0]['ordId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ {action_name}创建成功: {result['order_id']}")
            
            if response.get('data'):
                for data in response['data']:
                    if 'attachAlgoOrds' in data:
                        for algo_ord in data['attachAlgoOrds']:
                            if 'algoId' in algo_ord:
                                result['algo_ids'].append(algo_ord['algoId'])
                            if 'algoClOrdId' in algo_ord:
                                result['algo_cl_ord_ids'].append(algo_ord['algoClOrdId'])
                            if 'attachAlgoId' in algo_ord:  # 新增：保存attachAlgoId
                                result['attach_algo_ids'].append(algo_ord['attachAlgoId'])
        
        return result
            
    except Exception as e:
        logger.error(f"创建全能订单失败: {str(e)}")
        return {
            'order_id': None, 
            'cl_ord_id': None,
            'response': None, 
            'algo_ids': [], 
            'algo_cl_ord_ids': [],
            'attach_algo_ids': [],
            'success': False
        }

def create_short_with_sl_tp_fixed(amount: float, stop_loss_price: float, take_profit_price: float):
    """创建空单并设置止损止盈"""
    return create_universal_order(
        side='sell',
        ord_type='market',
        amount=amount,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        verify_sl_tp=True
    )

def set_sl_tp_separately(side: str, amount: float, stop_loss_price: float, take_profit_price: float) -> Dict[str, List[str]]:
    """分开设置止损和止盈订单"""
    result = {
        'algo_ids': [],
        'algo_cl_ord_ids': []
    }
    
    try:
        inst_id = get_correct_inst_id()
        logger.info("🔄 分开设置止损止盈订单...")
        
        # 设置止损订单
        sl_cl_ord_id = generate_cl_ord_id(side)
        sl_params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': 'buy' if side == 'short' else 'sell',
            'ordType': 'conditional',
            'sz': str(amount),
            'slTriggerPx': str(stop_loss_price),
            'slOrdPx': '-1',
            'algoClOrdId': sl_cl_ord_id
        }
        
        sl_response = exchange.private_post_trade_order_algo(sl_params)
        if sl_response and sl_response.get('code') == '0':
            sl_algo_id = sl_response['data'][0]['algoId'] if sl_response.get('data') else 'Unknown'
            logger.info(f"✅ 止损订单设置成功: {sl_algo_id}")
            result['algo_ids'].append(sl_algo_id)
            result['algo_cl_ord_ids'].append(sl_cl_ord_id)
        
        # 设置止盈订单
        tp_cl_ord_id = generate_cl_ord_id(side)
        tp_params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': 'buy' if side == 'short' else 'sell',
            'ordType': 'conditional',
            'sz': str(amount),
            'tpTriggerPx': str(take_profit_price),
            'tpOrdPx': '-1',
            'algoClOrdId': tp_cl_ord_id
        }
        
        tp_response = exchange.private_post_trade_order_algo(tp_params)
        if tp_response and tp_response.get('code') == '0':
            tp_algo_id = tp_response['data'][0]['algoId'] if tp_response.get('data') else 'Unknown'
            logger.info(f"✅ 止盈订单设置成功: {tp_algo_id}")
            result['algo_ids'].append(tp_algo_id)
            result['algo_cl_ord_ids'].append(tp_cl_ord_id)
            
        return result
            
    except Exception as e:
        logger.error(f"分开设置止损止盈失败: {str(e)}")
        return result

def check_sl_tp_status(main_ord_id: str) -> bool:
    """使用优化查询检查止损止盈状态"""
    order_info = get_order_comprehensive_info(main_ord_id)
    
    # 关键修复：只要有attach_algo_ids就认为有有效的止损止盈设置
    has_valid_sl_tp = order_info["has_valid_sl_tp"]
    
    if has_valid_sl_tp:
        logger.info("✅ 发现有效的止损止盈设置")
        return True
    else:
        logger.warning("⚠️ 未发现有效的止损止盈设置")
        return False

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
    
    short_order_result = create_short_with_sl_tp_fixed(
        amount=position_size,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price
    )
    
    if not short_order_result['success']:
        logger.error("❌ 空单开仓失败")
        return False
    
    short_order_id = short_order_result['order_id']
    # 确保 saved_attach_algo_ids 被正确定义
    saved_attach_algo_ids = short_order_result.get('attach_algo_ids', [])
    logger.info(f"💾 保存的附带止盈止损ID: {saved_attach_algo_ids}")
    
    # 等待空单成交
    if not wait_for_order_fill(short_order_id, 30):
        logger.error("❌ 空单未在30秒内成交")
        return False
    
    # 等待空单持仓出现
    short_position = wait_for_position('short', 30)
    if not short_position:
        logger.error("❌ 空单持仓未找到")
        return False
    
    logger.info(f"✅ 空单持仓建立: {short_position['size']}张")
    
    # 阶段2: 使用优化查询确认止盈止损设置
    logger.info("")
    logger.info("🔹 阶段2: 确认止盈止损设置")
    logger.info("-" * 40)
    
    time.sleep(3)
    has_sl_tp = check_sl_tp_status(short_order_id)
    
    if not has_sl_tp:
        logger.warning("⚠️ 未发现止损止盈信息，尝试分开设置...")
        recalculated_sl, recalculated_tp = calculate_stop_loss_take_profit_prices('short', short_position['entry_price'])
        set_sl_tp_separately('short', short_position['size'], recalculated_sl, recalculated_tp)
        time.sleep(2)
        has_sl_tp = check_sl_tp_status(short_order_id)
        
        if not has_sl_tp:
            logger.error("❌ 止损止盈设置失败")
            return False

    # 阶段3: 取消现有止盈止损单
    logger.info("")
    logger.info("🔹 阶段3: 取消现有止盈止损单")
    logger.info("-" * 40)

    logger.info("⏳ 等待5秒后取消止盈止损单...")
    time.sleep(5)

    # 获取当前订单状态信息
    current_order_info = get_order_comprehensive_info(short_order_id)
    current_main_state = current_order_info["main_order_state"]
    current_attach_algo_ids = current_order_info["attach_algo_ids"]

    # 使用当前查询到的信息进行撤销
    if current_attach_algo_ids:
        logger.info(f"🔧 使用查询到的attach_algo_ids进行撤销: {current_attach_algo_ids}")
        success = cancel_attached_sl_tp_by_algo_ids(
            short_order_id, 
            current_attach_algo_ids, 
            short_order_result['algo_cl_ord_ids'],  # 传递我们自定义的algoClOrdId
            current_main_state
        )
    else:
        logger.info("🔧 未发现需要撤销的止盈止损单")
        success = True

    if not success:
        logger.error("❌ 止盈止损单取消失败")
        return False

    # 确认止盈止损单已取消
    time.sleep(2)
    if not check_sl_tp_status(short_order_id):
        logger.info("✅ 确认所有止盈止损单已取消")
    else:
        logger.warning("⚠️ 仍有止盈止损单存在，尝试再次取消...")
        if cancel_all_sl_tp_versatile(short_order_id) and not check_sl_tp_status(short_order_id):
            logger.info("✅ 再次取消后确认已无止损止盈单")
        else:
            logger.error("❌ 无法完全取消止盈止损单")
            return False
    
    # 阶段4: 重新设置止盈止损单
    logger.info("")
    logger.info("🔹 阶段4: 重新设置止盈止损单")
    logger.info("-" * 40)
    
    new_sl, new_tp = calculate_stop_loss_take_profit_prices('short', short_position['entry_price'])
    logger.info(f"📊 重新计算止损: {new_sl:.2f}, 止盈: {new_tp:.2f}")
    
    set_sl_tp_separately('short', short_position['size'], new_sl, new_tp)
    time.sleep(2)
    
    if check_sl_tp_status(short_order_id):
        logger.info("✅ 重新设置的止盈止损单已确认")
    else:
        logger.warning("⚠️ 重新设置的止盈止损单未查询到")

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
    
    close_order_id = create_limit_close_order('short', short_position['size'])
    
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
        cleanup_after_test()
    except Exception as e:
        logger.error(f"💥 测试程序异常: {str(e)}")
        cleanup_after_test()
        traceback.print_exc()

if __name__ == "__main__":
    main()