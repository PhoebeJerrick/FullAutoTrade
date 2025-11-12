#!/usr/bin/env python3

# ds_sltp_test.py - BTC空单止盈止损测试程序（基于OKX客服建议优化）

import os
import time
import sys
import traceback
import uuid
import json
from datetime import datetime
from typing import Dict, Any, Optional, List
import ccxt
from dotenv import load_dotenv

# 在文件顶部定义全局变量
saved_attach_algo_ids = []

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

def get_order_comprehensive_info(main_ord_id: str) -> Dict[str, Any]:
    """
    获取订单综合信息（修复版）
    重点修复：正确识别和处理附带止盈止损单
    """
    result = {
        "main_order_state": None,
        "main_order_data": None,  # 新增：保存完整的主订单数据
        "attach_algo_ids": [],
        "algo_orders_details": [],
        "has_valid_sl_tp": False,
        "success": False
    }
    
    try:
        inst_id = get_correct_inst_id()
        
        # 1. 查询主订单详情（核心信息源）
        logger.info(f"🔍 get_order_comprehensive_info: {main_ord_id}")
        logger.info(f"🔍private_get_trade_order 查询主订单请求详情: {main_ord_id}")
        main_order_params = {
            "instId": inst_id,
            "ordId": main_ord_id,
        }
        logger.info(json.dumps(main_order_params, indent=2, ensure_ascii=False))

        main_order_resp = exchange.private_get_trade_order(main_order_params)
        
        # 打印完整的API响应信息
        logger.info("📋 主订单API完整响应:")
        logger.info(f"   响应码: {main_order_resp.get('code')}")
        logger.info(f"   响应消息: {main_order_resp.get('msg')}")
        logger.info(f"   数据条数: {len(main_order_resp.get('data', []))}")
        
        if main_order_resp.get('data'):
            data = main_order_resp['data'][0]
            logger.info("📋 主订单数据详情:")
            logger.info(json.dumps(data, indent=2, ensure_ascii=False))
            result["main_order_data"] = data  # 保存完整数据
            
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
        
        # # 详细打印附带止盈止损信息
        # if valid_attach_ids:
        #     logger.info(f"📋 主订单附带止盈止损详细信息:")
        #     for idx, algo_ord in enumerate(attach_algo_ords):
        #         logger.info(f"   止盈止损单 #{idx+1}:")
        #         logger.info(json.dumps(algo_ord, indent=2, ensure_ascii=False))
        # else:
        #     logger.info("ℹ️ 未发现附带止盈止损单")

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
        
        # 2. 查询已委托的分离止盈止损单
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
                if pending_resp.get('data'):
                    logger.info("   数据详情:")
                    logger.info(json.dumps(pending_resp['data'], indent=2, ensure_ascii=False))
            
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
                        logger.info(json.dumps(algo, indent=2, ensure_ascii=False))
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
        logger.info(f"   请求参数: {json.dumps(params, indent=2, ensure_ascii=False)}")
        response = exchange.private_post_trade_amend_algos(params)
        logger.info(f"   响应: {json.dumps(response, indent=2, ensure_ascii=False)}")
        
        if response and response.get("code") == "0":
            logger.info(f"✅ 成功撤销已委托止盈止损: {algo_cl_ord_id}")
            return True
        else:
            logger.error(f"❌ 修改失败: {response}")
            return False
    except Exception as e:
        logger.error(f"修改出错: {str(e)}")
        return False

def check_sl_tp_activation_status(main_ord_id: str) -> Dict[str, Any]:
    """
    检查止盈止损单的激活状态
    返回：{
        "has_attached_sl_tp": bool,  # 是否有附带止盈止损
        "has_activated_sl_tp": bool,  # 是否已激活
        "algo_ids": List[str],        # 算法订单ID
        "algo_cl_ord_ids": List[str]  # 算法订单自定义ID
    }
    """
    result = {
        "has_attached_sl_tp": False,
        "has_activated_sl_tp": False,
        "algo_ids": [],
        "algo_cl_ord_ids": []
    }
    
    try:
        inst_id = get_correct_inst_id()
        
        # 1. 查询主订单，检查是否有附带止盈止损
        main_order_info = get_order_comprehensive_info(main_ord_id)
        if not main_order_info["success"]:
            return result
            
        result["has_attached_sl_tp"] = len(main_order_info["attach_algo_ids"]) > 0
        
        # 2. 查询算法订单，检查是否已激活
        algo_params = {
            "instType": "SWAP",
            "instId": inst_id,
            "ordType": "conditional,oco"  # 条件单类型
        }
        
        logger.info(f"🔍 查询算法订单状态请求:")
        logger.info(json.dumps(algo_params, indent=2, ensure_ascii=False))
        algo_resp = exchange.private_get_trade_orders_algo_pending(algo_params)

        # 打印完整响应
        logger.info("📥 止盈止损订单查询响应:")
        if algo_resp:
            logger.info(f"   响应码: {algo_resp.get('code')}")
            logger.info(f"   响应消息: {algo_resp.get('msg')}")
            logger.info(f"   数据条数: {len(algo_resp.get('data', []))}")
            
            if algo_resp.get('data'):
                for idx, order in enumerate(algo_resp['data']):
                    logger.info(f"   订单 #{idx+1}:")
                    logger.info(json.dumps(order, indent=2, ensure_ascii=False))

        if algo_resp and algo_resp.get("code") == "0":
            algo_orders = algo_resp.get("data", [])
            # 查找与主订单关联的算法订单
            for order in algo_orders:
                if order.get("attachOrdId") == main_ord_id:
                    result["has_activated_sl_tp"] = True
                    if order.get("algoId"):
                        result["algo_ids"].append(order["algoId"])
                    if order.get("algoClOrdId"):
                        result["algo_cl_ord_ids"].append(order["algoClOrdId"])
            
            if result["has_activated_sl_tp"]:
                logger.info(f"✅ 发现已激活的止盈止损单: {result['algo_ids']}")
            else:
                logger.info("ℹ️ 未发现已激活的止盈止损单")
        
        return result
        
    except Exception as e:
        logger.error(f"检查止盈止损激活状态失败: {str(e)}")
        return result


def cancel_activated_sl_tp_by_algo_id(algo_id: str, inst_id: str) -> bool:
    """通过algoId撤销已激活的止盈止损单"""
    try:
        params = {
            "instId": inst_id,
            "algoId": algo_id
        }
        
        logger.info(f"🔄 通过algoId撤销止盈止损单: {algo_id}")
        logger.info(f"   请求参数: {json.dumps(params, indent=2, ensure_ascii=False)}")
        
        response = exchange.private_post_trade_cancel_algos(params)
        logger.info(f"   响应: {json.dumps(response, indent=2, ensure_ascii=False)}")
        
        if response and response.get("code") == "0":
            logger.info(f"✅ 成功撤销止盈止损单: {algo_id}")
            return True
        else:
            logger.error(f"❌ 撤销失败: {response}")
            return False
            
    except Exception as e:
        logger.error(f"通过algoId撤销止盈止损单失败: {str(e)}")
        return False

def cancel_activated_sl_tp_by_algo_cl_ord_id(algo_cl_ord_id: str, inst_id: str) -> bool:
    """通过algoClOrdId撤销已激活的止盈止损单"""
    try:
        params = {
            "instId": inst_id,
            "algoClOrdId": algo_cl_ord_id
        }
        
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


def cancel_attached_sl_tp_smart(main_ord_id: str, attach_algo_ids: List[str], attach_algo_cl_ord_ids: List[str]) -> bool:
    """
    智能撤销止盈止损单
    根据止盈止损单的实际状态选择正确的撤销方式
    """
    if not attach_algo_ids and not attach_algo_cl_ord_ids:
        logger.info("✅ 没有需要撤销的止盈止损单")
        return True
        
    inst_id = get_correct_inst_id()

    # 1. 首先检查止盈止损单的激活状态
    sl_tp_status = check_sl_tp_activation_status(main_ord_id)
    
    logger.info(f"🔧 止盈止损单状态: 附带={sl_tp_status['has_attached_sl_tp']}, 激活={sl_tp_status['has_activated_sl_tp']}")
    
    # 2. 根据状态选择撤销方式
    if sl_tp_status["has_activated_sl_tp"]:
        # 止盈止损单已激活，使用算法订单接口
        logger.info("🔄 止盈止损单已激活，使用算法订单接口撤销")
        
        # 优先使用查询到的算法订单ID
        if sl_tp_status["algo_ids"]:
            for algo_id in sl_tp_status["algo_ids"]:
                if cancel_activated_sl_tp_by_algo_id(algo_id, inst_id):
                    return True
        # 其次尝试使用我们自定义的ID
        elif attach_algo_cl_ord_ids:
            for algo_cl_ord_id in attach_algo_cl_ord_ids:
                if cancel_activated_sl_tp_by_algo_cl_ord_id(algo_cl_ord_id, inst_id):
                    return True
        
        logger.error("❌ 无法撤销已激活的止盈止损单")
        return False
        
    else:
        # 止盈止损单未激活，使用主订单修改接口
        logger.info("🔄 止盈止损单未激活，使用主订单修改接口撤销")
        
        if attach_algo_ids:
            for attach_algo_id in attach_algo_ids:
                if amend_untraded_sl_tp(main_ord_id, attach_algo_id, inst_id):
                    return True
        
        logger.error("❌ 无法撤销未激活的止盈止损单")
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
            if not amend_traded_sl_tp(None, algo_cl_ord_id, inst_id):
                logger.error(f"❌ 使用自定义ID撤销止盈止损单失败: {algo_cl_ord_id}")
                success = False
            else:
                logger.info(f"✅ 使用自定义ID撤销止盈止损单成功: {algo_cl_ord_id}")
                return True
            time.sleep(1)
    
    # 如果自定义ID撤销失败，尝试其他方式
    if not has_activated_sl_tp:
        # 止盈止损单未激活，使用amend-order接口
        logger.info("🔄 止盈止损单未激活，使用amend-order接口撤销")
        for attach_algo_id in attach_algo_ids:
            if not amend_untraded_sl_tp(main_ord_id, attach_algo_id, inst_id):
                logger.error(f"❌ 未激活止盈止损单撤销失败: {attach_algo_id}")
                success = False
            else:
                logger.info(f"✅ 未激活止盈止损单撤销成功: {attach_algo_id}")
            time.sleep(1)
    elif main_order_state in ["live", "partially_filled"]:
        # 主订单未完全成交，止盈止损单未激活
        logger.info("🔄 使用amend-order接口撤销未成交止盈止损")
        for attach_algo_id in attach_algo_ids:
            if not amend_untraded_sl_tp(main_ord_id, attach_algo_id, inst_id):
                logger.error(f"❌ 附带止盈止损单撤销失败: {attach_algo_id}")
                success = False
            else:
                logger.info(f"✅ 附带止盈止损单撤销成功: {attach_algo_id}")
            time.sleep(1)
    elif main_order_state == "filled" and has_activated_sl_tp:
        # 主订单已完全成交，止盈止损单已激活
        logger.info("🔄 使用amend-algos接口撤销已委托止盈止损")
        for algo_cl_ord_id in algo_cl_ord_ids:
            if not amend_traded_sl_tp(None, algo_cl_ord_id, inst_id):
                logger.error(f"❌ 已委托止盈止损单撤销失败: {algo_cl_ord_id}")
                success = False
            else:
                logger.info(f"✅ 已委托止盈止损单撤销成功: {algo_cl_ord_id}")
            time.sleep(1)
    else:
        logger.warning(f"⚠️ 无法确定撤销方式: 主订单状态={main_order_state}, 止盈止损激活状态={has_activated_sl_tp}")
        success = False
    
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


def find_sl_tp_order_by_attach_algo_cl_ord_id(attach_algo_cl_ord_id: str) -> Optional[Dict]:
    """
    通过attachAlgoClOrdId查找止盈止损订单
    """
    try:
        inst_id = get_correct_inst_id()
        
        # 查询待处理的算法订单
        params = {
            "instType": "SWAP",
            "instId": inst_id,
            "algoClOrdId": attach_algo_cl_ord_id  # 使用我们设置的attachAlgoClOrdId
        }
        
        logger.info(f"🔍 通过attachAlgoClOrdId查找止盈止损订单: {attach_algo_cl_ord_id}")
        logger.info(f"   请求参数: {json.dumps(params, indent=2, ensure_ascii=False)}")
        response = exchange.private_get_trade_orders_algo_pending(params)
        
        # 打印完整响应
        logger.info("📥 止盈止损订单查询响应:")
        if response:
            logger.info(f"   响应码: {response.get('code')}")
            logger.info(f"   响应消息: {response.get('msg')}")
            logger.info(f"   数据条数: {len(response.get('data', []))}")
            
            if response.get('data'):
                for idx, order in enumerate(response['data']):
                    logger.info(f"   订单 #{idx+1}:")
                    logger.info(json.dumps(order, indent=2, ensure_ascii=False))
        
        if response and response.get('code') == '0' and response.get('data'):
            return response['data'][0]  # 返回第一个匹配的订单
        
        return None
        
    except Exception as e:
        logger.error(f"通过attachAlgoClOrdId查找止盈止损订单失败: {str(e)}")
        return None

def cancel_sl_tp_by_attach_algo_cl_ord_id(attach_algo_cl_ord_id: str) -> bool:
    """
    通过attachAlgoClOrdId取消止盈止损订单
    """
    try:
        # 先查找订单
        sl_tp_order = find_sl_tp_order_by_attach_algo_cl_ord_id(attach_algo_cl_ord_id)
        
        if not sl_tp_order:
            logger.warning(f"⚠️ 未找到对应的止盈止损订单: {attach_algo_cl_ord_id}")
            return False
        
        algo_id = sl_tp_order.get('algoId')
        inst_id = sl_tp_order.get('instId')
        
        if not algo_id:
            logger.error(f"❌ 止盈止损订单没有algoId: {sl_tp_order}")
            return False
        
        # 取消订单
        cancel_params = {
            "instId": inst_id,
            "algoId": algo_id
        }
        
        logger.info(f"🔄 取消止盈止损订单: algoId={algo_id}")
        logger.info(f"   请求参数: {json.dumps(cancel_params, indent=2, ensure_ascii=False)}")
        response = exchange.private_post_trade_cancel_algos(cancel_params)
        logger.info(f"   响应: {json.dumps(response, indent=2, ensure_ascii=False)}")
        
        if response and response.get('code') == '0':
            logger.info(f"✅ 成功取消止盈止损订单: {algo_id}")
            return True
        else:
            logger.error(f"❌ 取消止盈止损订单失败: {response}")
            return False
            
    except Exception as e:
        logger.error(f"通过attachAlgoClOrdId取消止盈止损订单失败: {str(e)}")
        return False

def process_order_result(order_result: Dict[str, Any]) -> Dict[str, Any]:
    """
    处理订单结果，提取和补充止盈止损信息
    """
    if not order_result.get('success'):
        return order_result
    
    # 基础结果结构
    processed_result = {
        'success': True,
        'order_id': order_result['order_id'],
        'cl_ord_id': order_result['cl_ord_id'],
        'custom_sl_tp_id': order_result.get('custom_sl_tp_id'),
        'stop_loss_price': order_result.get('stop_loss_price'),
        'take_profit_price': order_result.get('take_profit_price'),
        'attach_algo_ids': [],
        'attach_algo_cl_ord_ids': [],
        'algo_ids': [],
        'algo_cl_ord_ids': []
    }
    
    # 如果需要获取详细的止盈止损信息，主动查询一次
    if order_result.get('custom_sl_tp_id'):
        logger.info("🔍 查询订单详情获取止盈止损信息...")
        time.sleep(2)  # 等待订单处理
        
        order_detail = get_order_comprehensive_info(order_result['order_id'])
        if order_detail["success"] and order_detail["attach_algo_ids"]:
            processed_result['attach_algo_ids'] = order_detail["attach_algo_ids"]
            
            # 从详细数据中提取其他信息
            if order_detail.get("main_order_data", {}).get("attachAlgoOrds"):
                for algo_ord in order_detail["main_order_data"]["attachAlgoOrds"]:
                    if algo_ord.get("attachAlgoClOrdId"):
                        processed_result['attach_algo_cl_ord_ids'].append(algo_ord["attachAlgoClOrdId"])
                    if algo_ord.get("algoId"):
                        processed_result['algo_ids'].append(algo_ord["algoId"])
                    if algo_ord.get("algoClOrdId"):
                        processed_result['algo_cl_ord_ids'].append(algo_ord["algoClOrdId"])
        
        logger.info(f"📋 处理后的订单详情:")
        logger.info(json.dumps({
            "主订单ID": processed_result['order_id'],
            "自定义订单ID": processed_result['cl_ord_id'],
            "止盈止损自定义ID": processed_result['custom_sl_tp_id'],
            "附带止盈止损ID": processed_result['attach_algo_ids'],
            "止盈止损算法ID": processed_result['algo_ids']
        }, indent=2, ensure_ascii=False))
    
    return processed_result


def create_universal_order(
    side: str, 
    ord_type: str = 'market',
    amount: Optional[float] = None,
    price: Optional[float] = None,
    stop_loss_price: Optional[float] = None,
    take_profit_price: Optional[float] = None
) -> Dict[str, Any]:
    """
    简化版全能交易函数：只负责创建订单，不处理复杂的响应解析
    """
    try:
        inst_id = get_correct_inst_id()
        amount = amount or get_safe_position_size()
        cl_ord_id = generate_cl_ord_id(side)
        
        # 基础订单参数
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
                
        algo_ords = []
        opposite_side = 'buy' if side == 'sell' else 'sell'
        
        # 设置止盈止损参数
        sl_tp_cl_ord_id = None
        if stop_loss_price is not None or take_profit_price is not None:
            algo = {}
            
            # 为每个止盈止损单生成唯一的attachAlgoClOrdId
            sl_tp_cl_ord_id = generate_cl_ord_id(f"{side}_sl_tp")
            
            if stop_loss_price is not None:
                algo['slTriggerPx'] = str(stop_loss_price)
                algo['slOrdPx'] = '-1'
            
            if take_profit_price is not None:
                algo['tpTriggerPx'] = str(take_profit_price)
                algo['tpOrdPx'] = '-1'
            
            # 关键：设置attachAlgoClOrdId，用于后续查找
            algo['attachAlgoClOrdId'] = sl_tp_cl_ord_id
            algo['sz'] = str(amount)
            algo['side'] = opposite_side
            algo['algoOrdType'] = 'conditional'
            
            algo_ords.append(algo)
        
        if algo_ords:
            params['attachAlgoOrds'] = algo_ords
        
        action_name = f"{'做多' if side == 'buy' else '做空'}{'市价' if ord_type == 'market' else '限价'}单"
        
        # 打印完整的请求信息
        logger.info("📤 完整请求参数:")
        logger.info(json.dumps(params, indent=2, ensure_ascii=False))
        
        logger.info(f"🎯 执行{action_name}: {amount} 张")
        
        # 执行API调用
        response = exchange.private_post_trade_order(params)
        
        # 打印完整的响应信息
        logger.info("📥 完整响应信息:")
        if response:
            logger.info(json.dumps(response, indent=2, ensure_ascii=False))
            
            if response.get('code') != '0':
                logger.error(f"❌ API调用失败: {response}")
                return {
                    'success': False,
                    'error': response.get('msg', 'Unknown error'),
                    'response': response
                }
        else:
            logger.error("❌ 无响应数据")
            return {
                'success': False,
                'error': 'No response data',
                'response': None
            }
        
        # 简化的返回结果
        order_id = response['data'][0]['ordId'] if response.get('data') else None
        logger.info(f"✅ {action_name}创建成功: {order_id}")
        
        return {
            'success': True,
            'order_id': order_id,
            'cl_ord_id': cl_ord_id,
            'response': response,
            'custom_sl_tp_id': sl_tp_cl_ord_id,  # 保存我们自定义的止盈止损ID
            'stop_loss_price': stop_loss_price,
            'take_profit_price': take_profit_price
        }
            
    except Exception as e:
        logger.error(f"创建全能订单失败: {str(e)}")
        logger.error(f"异常堆栈: {traceback.format_exc()}")
        return {
            'success': False,
            'error': str(e),
            'response': None
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
        
        logger.info(f"🛡️ 设置止损订单:")
        logger.info(json.dumps(sl_params, indent=2, ensure_ascii=False))
        sl_response = exchange.private_post_trade_order_algo(sl_params)
        logger.info(f"止损订单响应:")
        logger.info(json.dumps(sl_response, indent=2, ensure_ascii=False))
        
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
        
        logger.info(f"🎯 设置止盈订单:")
        logger.info(json.dumps(tp_params, indent=2, ensure_ascii=False))
        tp_response = exchange.private_post_trade_order_algo(tp_params)
        logger.info(f"止盈订单响应:")
        logger.info(json.dumps(tp_response, indent=2, ensure_ascii=False))
        
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

    # 创建订单（简化版）
    short_order_result = create_universal_order(
        side='sell',
        ord_type='market',
        amount=position_size,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price
    )

    if not short_order_result['success']:
        logger.error("❌ 空单开仓失败")
        return False

    logger.info("⏳ 等待5秒后获取止盈止损信息...")
    time.sleep(5)

    # 处理订单结果，获取止盈止损信息
    processed_order_result = process_order_result(short_order_result)

    # 保存用于后续查找的信息
    main_order_id = processed_order_result['order_id']
    saved_attach_algo_ids = processed_order_result['attach_algo_ids']
    saved_attach_algo_cl_ord_ids = processed_order_result['attach_algo_cl_ord_ids']
    saved_algo_cl_ord_ids = processed_order_result['algo_cl_ord_ids']

    logger.info(f"💾 保存的订单信息:")
    logger.info(f"   主订单ID: {main_order_id}")
    logger.info(f"   附带止盈止损ID: {saved_attach_algo_ids}")
    logger.info(f"   止盈止损自定义ID: {saved_attach_algo_cl_ord_ids}")
    logger.info(f"   算法订单自定义ID: {saved_algo_cl_ord_ids}")

    # 等待空单成交
    if not wait_for_order_fill(main_order_id, 30):
        logger.error("❌ 空单未在30秒内成交")
        return False

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

    # 使用智能撤销函数
    if saved_attach_algo_ids or saved_attach_algo_cl_ord_ids:
        logger.info(f"🔧 进行止盈止损撤销操作")
        logger.info(f"   保存的attach_algo_ids: {saved_attach_algo_ids}")
        logger.info(f"   保存的attach_algo_cl_ord_ids: {saved_attach_algo_cl_ord_ids}")
        
        if saved_algo_cl_ord_ids:
            for algo_ord_id in saved_algo_cl_ord_ids:
                if cancel_activated_sl_tp_by_algo_id(algo_ord_id, get_correct_inst_id()):
                    return True
                
        # 其次尝试使用我们自定义的ID
        if saved_attach_algo_cl_ord_ids:
            for algo_cl_ord_id in saved_attach_algo_cl_ord_ids:
                if cancel_activated_sl_tp_by_algo_cl_ord_id(algo_cl_ord_id, get_correct_inst_id()):
                    return True
                
        if saved_attach_algo_ids:
            for attach_algo_id in saved_attach_algo_ids:
                if amend_untraded_sl_tp(main_order_id, attach_algo_id, get_correct_inst_id()):
                    return True

    else:
        logger.info("🔧 未发现需要撤销的止盈止损单")
        success = True

    if not success:
        logger.error("❌ 止盈止损单取消失败")
        return False

    # 确认止盈止损单已取消
    time.sleep(2)
    if not check_sl_tp_status(main_order_id):
        logger.info("✅ 确认所有止盈止损单已取消")
    else:
        logger.warning("⚠️ 仍有止盈止损单存在，取消失败...")
        return False
    
    # 阶段4: 重新设置止盈止损单
    logger.info("")
    logger.info("🔹 阶段4: 重新设置止盈止损单")
    logger.info("-" * 40)
    
    new_sl, new_tp = calculate_stop_loss_take_profit_prices('short', short_position['entry_price'])
    logger.info(f"📊 重新计算止损: {new_sl:.2f}, 止盈: {new_tp:.2f}")
    
    set_sl_tp_separately('short', short_position['size'], new_sl, new_tp)
    time.sleep(2)
    
    if check_sl_tp_status(main_order_id):
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