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
    # 方向前缀（纯字母）
    prefix = "SELL" if side == "sell" else "BUY"
    # 生成UUID并移除所有非字母数字字符（UUID本身包含字母和数字）
    unique_str = str(uuid.uuid4()).replace('-', '')  # 去掉UUID中的横线
    # 组合前缀和唯一字符串，总长度控制在32位以内
    cl_ord_id = f"{prefix}{unique_str}"[:32]  # 确保不超过32位
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
        
        # 根据方向确定限价价格
        if side == 'short':  # 平空单，买入
            limit_price = current_price * 1.001  # 比当前价高0.1%
            close_side = 'buy'
        else:  # 平多单，卖出
            limit_price = current_price * 0.999  # 比当前价低0.1%
            close_side = 'sell'
        
        # 生成唯一的自定义订单ID
        cl_ord_id = generate_cl_ord_id(side)
        
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': close_side,
            'ordType': 'limit',
            'sz': str(amount),
            'px': str(limit_price),
            'clOrdId': cl_ord_id  # 添加自定义订单ID
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

"""查询订单相关原始信息（整合3个核心接口）"""
def get_raw_order_info(ord_id: str, inst_id: str) -> Optional[Dict[str, dict]]:
    """
    同时调用3个接口查询订单相关信息：
    1. GET /api/v5/trade/order：查询单个订单详情（主订单）
    2. GET /api/v5/trade/orders-pending：查询未成交订单（可能包含关联的条件单）
    3. GET /api/v5/trade/orders-history：查询历史订单（含已成交/已撤销）
    返回所有接口的原始响应数据，用于撤销失败时的全面诊断
    """
    if not ord_id or not inst_id:
        logger.warning("⚠️ 查询订单信息失败：缺少ordId（主订单ID）或instId（产品ID）")
        return None
        
    # 存储所有接口的响应结果
    all_responses = {}
    
    try:
        # --------------------------
        # 1. 查询单个主订单详情（核心接口）
        # --------------------------
        single_order_params = {
            "instId": inst_id,
            "ordId": ord_id
        }
        logger.info(f"\n🔍 [1/3] 调用GET /trade/order（单个订单详情）：ordId={ord_id}, instId={inst_id}")
        logger.info(f"📋 单个订单原始请求：{single_order_params}")
        single_order_response = exchange.private_get_trade_order(single_order_params)
        all_responses["single_order"] = single_order_response
        logger.info(f"📋 单个订单原始响应：{single_order_response}")
        
        # --------------------------
        # 2. 查询未成交订单（含条件单/止盈止损单）
        # --------------------------
        pending_orders_params = {
            "instType": "SWAP",  # 现货/合约类型，根据实际场景调整
            "instId": inst_id,   # 限定当前产品
            "ordType": "market",  # 重点查询条件单和OCO单（止盈止损常用类型）
        }
        logger.info(f"\n🔍 [2/3] 调用GET /trade/orders-pending（未成交订单）：instId={inst_id}")
        logger.info(f"📋 未成交订单原始请求：{pending_orders_params}")
        pending_orders_response = exchange.private_get_trade_orders_pending(pending_orders_params)
        all_responses["pending_orders"] = pending_orders_response
        logger.info(f"📋 未成交订单原始响应：{pending_orders_response}")
        
        # --------------------------
        # 3. 查询历史订单（含已成交/已撤销）
        # --------------------------
        history_orders_params = {
            "instType": "SWAP",
            "instId": inst_id,
            "state": "filled,canceled"  # 重点查已成交和已撤销状态
        }
        logger.info(f"\n🔍 [3/3] 调用GET /trade/orders-history（历史订单）：ordId={ord_id}, instId={inst_id}")
        logger.info(f"📋 历史订单原始请求：{history_orders_params}")
        history_orders_response = exchange.private_get_trade_orders_history(history_orders_params)
        all_responses["history_orders"] = history_orders_response
        logger.info(f"📋 历史订单原始响应：{history_orders_response}")
        
        return all_responses
        
    except Exception as e:
        logger.error(f"❌ 订单信息查询出错：{str(e)}")
        return all_responses  # 即使部分接口失败，也返回已获取的响应


"""通过修改触发价为0撤销附带的止盈止损单（严格遵循OKX文档）"""
def amend_attached_sl_tp_to_zero(attach_algo_id: str, inst_id: str, order_id: str) -> bool:
    """
    关键修正：
    1. 必传`attachAlgoId`（系统生成的附带止盈止损订单ID，文档标注必填）
    2. 使用`newTpTriggerPx`和`newSlTriggerPx`修改触发价（文档指定参数）
    3. 设置触发价为0表示删除止盈止损（文档说明）
    参考文档：POST /修改订单 中attachAlgoOrds参数说明
    """
    if not attach_algo_id or not order_id:
        logger.warning("⚠️ 缺少必填参数attachAlgoId / order_id，无法修改附带止盈止损单")
        return False
        
    try:
        # 严格按照文档构造参数：attachAlgoId为必填，用于标识要修改的附带止盈止损单
        params = {
            "instId": inst_id,
            "ordId": order_id,
            "attachAlgoOrds": [  # 数组形式，包含要修改的附带止盈止损信息
                {
                    "attachAlgoId": attach_algo_id,  # 文档标注的必填项
                    "newTpTriggerPx": "0",  # 止盈触发价设为0（删除止盈）
                    "newSlTriggerPx": "0"   # 止损触发价设为0（删除止损）
                }
            ]
        }
        
        logger.info(f"🔄 尝试修改附带止盈止损单（attachAlgoId={attach_algo_id}）的触发价为0")
        response = exchange.private_post_trade_amend_order(params)  # 使用修改订单接口
        
        if response and response.get("code") == "0":
            logger.info(f"✅ 成功撤销附带止盈止损单：attachAlgoId={attach_algo_id}")
            return True
        else:
            logger.error(f"❌ 修改失败：响应={response}，参数={params}")
            # 撤销失败时，立即查询主订单原始信息
            logger.info("📌 撤销失败，查询主订单详细信息：")
            get_raw_order_info(order_id, inst_id)  # 打印完整原始接口信息
            return False
            
    except Exception as e:
        logger.error(f"修改附带止盈止损单出错：{str(e)}，参数={params}")
        # 撤销失败时，立即查询主订单原始信息
        logger.info("📌 撤销失败，查询主订单详细信息：")
        get_raw_order_info(order_id, inst_id)  # 打印完整原始接口信息
        return False


"""场景1：主订单未完全成交时，用amend-order修改未委托的止盈止损"""
def amend_untraded_sl_tp(
    main_ord_id: str,  # 主订单ID（必填）
    attach_algo_id: str,  # 附带止盈止损单ID（必填）
    inst_id: str
) -> bool:
    """适用于主订单未完全成交（live/partially_filled），止盈止损未委托的场景"""
    try:
        params = {
            "instId": inst_id,
            "ordId": main_ord_id,  # 主订单标识
            "attachAlgoOrds": [    # 附带止盈止损修改信息
                {
                    "attachAlgoId": attach_algo_id,
                    "newTpTriggerPx": "0",  # 止盈设为0（删除）
                    "newSlTriggerPx": "0"   # 止损设为0（删除）
                }
            ]
        }
        logger.info(f"🔄 [未成交阶段] 调用amend-order修改：主订单{main_ord_id}，attachAlgoId={attach_algo_id}")
        response = exchange.private_post_trade_amend_order(params)
        
        if response and response.get("code") == "0":
            logger.info(f"✅ 成功撤销未委托止盈止损：attachAlgoId={attach_algo_id}")
            return True
        else:
            logger.error(f"❌ [未成交阶段] amend-order失败：{response}，参数={params}")
            return False
    except Exception as e:
        logger.error(f"[未成交阶段] 修改出错：{str(e)}")
        return False


"""修正：已成交阶段用amend-algos修改（适配参数名和类型）"""
def amend_traded_sl_tp(
    algo_id: str,
    ord_type: str,  # 新增：订单类型（oco/conditional）
    inst_id: str
) -> bool:
    """
    关键修正：
    1. 根据订单类型（ordType）使用正确的触发价参数名
    2. OCO单需用newSlTriggerPx/newTpTriggerPx，条件单可用slTriggerPx/tpTriggerPx
    3. 明确传递ordType参数，避免接口歧义
    """
    try:
        # 基础参数：产品ID和算法订单ID
        params = {
            "instId": inst_id,
            "algoId": algo_id,
            "ordType": ord_type  # 明确订单类型，解决参数歧义
        }
        
        # 根据订单类型设置正确的触发价参数名（核心修正）
        if ord_type == "oco":
            # OCO单必须用newSlTriggerPx和newTpTriggerPx
            params.update({
                "newSlTriggerPx": "0",  # 止损设为0（删除）
                "newTpTriggerPx": "0"   # 止盈设为0（删除）
            })
        else:
            # 条件单可用slTriggerPx和tpTriggerPx
            params.update({
                "slTriggerPx": "0",
                "tpTriggerPx": "0"
            })
        
        logger.info(f"🔄 [已成交阶段] 调用amend-algos（类型{ord_type}）：algoId={algo_id}，参数={params}")
        response = exchange.private_post_trade_amend_algos(params)
        
        if response and response.get("code") == "0":
            logger.info(f"✅ 成功撤销已委托止盈止损（{ord_type}）：algoId={algo_id}")
            return True
        else:
            logger.error(f"❌ [已成交阶段] amend-algos失败：响应={response}，参数={params}")
            return False
    except Exception as e:
        logger.error(f"[已成交阶段] 修改出错：{str(e)}，参数={params}")
        return False

def get_sl_tp_related_info(main_ord_id: str, inst_id: str) -> Dict[str, any]:
    """
    全能订单信息查询接口（增强版）：
    1. 详细记录每一步查询过程、参数和结果
    2. 针对关键节点（如主订单状态获取、ID提取）提供明确提示
    3. 错误场景附带可能原因分析，辅助快速定位问题
    返回数据结构保持不变，但日志更丰富
    """
    # 初始化返回结果（带默认值，避免后续KeyError）
    result = {
        "main_order_state": None,
        "attach_algo_ids": [],
        "algo_orders_details": [],
        "raw_main_order": None,
        "raw_pending_orders": None
    }
    
    logger.info("\n" + "="*60)
    logger.info(f"🚀 开始执行全能订单信息查询：主订单ID={main_ord_id}，产品ID={inst_id}")
    logger.info("="*60)
    
    try:
        # --------------------------
        # 1. 查询主订单详情（核心步骤）
        # --------------------------
        logger.info("\n🔍 步骤1/2：查询主订单详情（GET /trade/order）")
        main_order_params = {
            "instId": inst_id,
            "ordId": main_ord_id
        }
        logger.info(f"   请求参数：{main_order_params}")
        
        # 执行查询
        main_order_resp = exchange.private_get_trade_order(main_order_params)
        result["raw_main_order"] = main_order_resp
        logger.info(f"   接口返回状态：{'成功' if main_order_resp.get('code') == '0' else '失败'}")
        logger.info(f"   原始响应（简版）：code={main_order_resp.get('code')}, msg={main_order_resp.get('msg')}")
        
        # 校验主订单响应有效性
        if not main_order_resp:
            logger.error("   ❌ 主订单查询失败：接口未返回任何数据（可能网络超时）")
            return result
        if main_order_resp.get("code") != "0":
            logger.error(f"   ❌ 主订单查询失败：接口返回错误，code={main_order_resp.get('code')}, msg={main_order_resp.get('msg')}")
            logger.error("   可能原因：主订单ID错误、产品ID不匹配或权限不足")
            return result
        if not main_order_resp.get("data"):
            logger.error("   ❌ 主订单查询失败：响应中无data字段（可能订单已被删除）")
            return result
        
        # 解析主订单核心数据
        main_order_data = main_order_resp["data"][0]
        logger.info(f"   主订单数据解析成功：ordId={main_order_data.get('ordId')}, state={main_order_data.get('state')}")
        
        # 提取主订单状态
        result["main_order_state"] = main_order_data.get("state")
        if result["main_order_state"]:
            logger.info(f"   ✅ 提取主订单状态：{result['main_order_state']}")
        else:
            logger.warning("   ⚠️ 未提取到主订单状态（state字段为空），可能接口响应格式变更")
        
        # 提取未成交时的附带止盈止损ID（attachAlgoId）
        logger.info("   开始提取未成交阶段的附带止盈止损ID（attachAlgoId）")
        attach_algo_ords = main_order_data.get("attachAlgoOrds", [])
        logger.info(f"   主订单关联的attachAlgoOrds数量：{len(attach_algo_ords)}")
        
        # 过滤有效ID
        valid_attach_ids = []
        for idx, ord_info in enumerate(attach_algo_ords):
            attach_id = ord_info.get("attachAlgoId")
            if attach_id and attach_id != "Unknown":
                valid_attach_ids.append(attach_id)
                logger.info(f"   第{idx+1}个附带订单：attachAlgoId={attach_id}（有效）")
            else:
                logger.info(f"   第{idx+1}个附带订单：attachAlgoId={attach_id}（无效，跳过）")
        
        result["attach_algo_ids"] = valid_attach_ids
        logger.info(f"   ✅ 提取到有效attachAlgoId数量：{len(valid_attach_ids)}")
        
        # --------------------------
        # 2. 查询已委托的止盈止损单（针对主订单成交后场景）
        # --------------------------
        logger.info("\n🔍 步骤2/2：查询已委托的止盈止损单（GET /trade/orders-pending）")
        pending_params = {
            "instType": "SWAP",
            "instId": inst_id,
            "ordType": "conditional,oco",  # 仅查条件单和OCO单
            "state": "live"                 # 仅查活跃订单
        }
        logger.info(f"   请求参数：{pending_params}")
        
        # 执行查询
        pending_resp = exchange.private_get_trade_orders_pending(pending_params)
        result["raw_pending_orders"] = pending_resp
        logger.info(f"   接口返回状态：{'成功' if pending_resp.get('code') == '0' else '失败'}")
        logger.info(f"   原始响应（简版）：code={pending_resp.get('code')}, 订单数量={len(pending_resp.get('data', []))}")
        
        # 校验未成交订单响应有效性
        if not pending_resp:
            logger.error("   ❌ 未成交订单查询失败：接口未返回任何数据（可能网络超时）")
            return result
        if pending_resp.get("code") != "0":
            logger.error(f"   ❌ 未成交订单查询失败：code={pending_resp.get('code')}, msg={pending_resp.get('msg')}")
            logger.error("   可能原因：产品类型错误（非SWAP）、权限不足或参数格式错误")
            return result
        
        # 筛选与当前主订单关联的已委托订单（通过attachOrdId匹配）
        logger.info("   开始筛选与主订单关联的已委托止盈止损单（匹配attachOrdId）")
        related_algos = []
        all_pending_orders = pending_resp.get("data", [])
        logger.info(f"   接口返回的未成交订单总数：{len(all_pending_orders)}")
        
        for idx, order in enumerate(all_pending_orders):
            order_attach_ord_id = order.get("attachOrdId")  # 关联的主订单ID
            algo_id = order.get("algoId")
            ord_type = order.get("ordType")
            
            # 匹配主订单ID
            if order_attach_ord_id == main_ord_id:
                related_algos.append({
                    "algoId": algo_id,
                    "ordType": ord_type,
                    "slTriggerPx": order.get("slTriggerPx", ""),
                    "tpTriggerPx": order.get("tpTriggerPx", "")
                })
                logger.info(f"   第{idx+1}个订单：匹配主订单！algoId={algo_id}, ordType={ord_type}")
            else:
                # 不匹配的订单仅简要记录（避免日志冗余）
                logger.debug(f"   第{idx+1}个订单：attachOrdId={order_attach_ord_id}（不匹配当前主订单，跳过）")
        
        result["algo_orders_details"] = related_algos
        logger.info(f"   ✅ 筛选到与主订单关联的已委托止盈止损单数量：{len(related_algos)}")
        
        # --------------------------
        # 查询完成总结
        # --------------------------
        logger.info("\n" + "="*60)
        logger.info("📊 全能订单信息查询完成，关键结果总结：")
        logger.info(f"   主订单状态：{result['main_order_state']}")
        logger.info(f"   未成交附带止盈止损ID数量：{len(result['attach_algo_ids'])}")
        logger.info(f"   已成交已委托止盈止损单数量：{len(result['algo_orders_details'])}")
        logger.info("="*60 + "\n")
        
        return result
        
    except Exception as e:
        logger.error("\n" + "="*60, exc_info=True)  # 打印完整堆栈信息
        logger.error(f"💥 全能订单信息查询异常终止：{str(e)}")
        logger.error("   可能原因：网络中断、接口版本变更或参数格式错误")
        logger.error("="*60 + "\n")
        return result


"""全能撤销函数（区分主订单状态，调用对应接口）"""
def cancel_all_sl_tp_versatile(main_ord_id: str) -> bool:
    if not main_ord_id:
        logger.error("❌ 必须提供主订单ID")
        return False
        
    inst_id = get_correct_inst_id()
    # 使用全能信息查询函数获取所有需要的信息
    sl_tp_info = get_sl_tp_related_info(main_ord_id, inst_id)
    main_state = sl_tp_info["main_order_state"]
    
    if not main_state:
        logger.error("❌ 无法获取主订单状态，撤销中止")
        return False
        
    logger.info(f"📊 主订单{main_ord_id}当前状态：{main_state}")
    success = True
    
    # 分支1：主订单未完全成交（live/partially_filled）
    if main_state in ["live", "partially_filled"]:
        logger.info("🔹 处理未完全成交场景：使用amend-order接口")
        # 从全能查询结果中获取附带止盈止损单的attachAlgoId
        attach_algo_ids = sl_tp_info["attach_algo_ids"]
        if not attach_algo_ids:
            logger.info("✅ 未发现未委托的止盈止损单")
            return True
            
        # 逐个修改
        for attach_id in attach_algo_ids:
            if not amend_untraded_sl_tp(main_ord_id, attach_id, inst_id):
                logger.error(f"❌ 未成交阶段撤销失败：attachAlgoId={attach_id}")
                success = False
            time.sleep(1)
    
    # 分支2：主订单已完全成交（filled）
    elif main_state == "filled":
        logger.info("🔹 处理已完全成交场景：使用amend-algos接口")
        # 从全能查询结果中获取已委托止盈止损单详情
        algo_orders_details = sl_tp_info["algo_orders_details"]
        if not algo_orders_details:
            logger.info("✅ 未发现已委托的止盈止损单")
            return True
            
        # 逐个修改
        for algo_detail in algo_orders_details:
            algo_id = algo_detail.get("algoId")
            ord_type = algo_detail.get("ordType", "conditional")
            if not amend_traded_sl_tp(algo_id, ord_type, inst_id):
                logger.error(f"❌ 已成交阶段撤销失败：algoId={algo_id}")
                success = False
            time.sleep(1)
    
    # 其他状态（如已撤销）
    else:
        logger.info(f"ℹ️ 主订单状态为{main_state}，无需处理止盈止损单")
        return True
    
    # 最终检查 + 失败时查询详细信息
    time.sleep(3)
    if success:
        logger.info("✅ 所有止盈止损单撤销成功")
        return True
    else:
        logger.error("❌ 部分止盈止损单撤销失败，查询详细信息：")
        get_raw_order_info(main_ord_id, inst_id)  # 调用之前的增强版查询函数
        return False

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

def check_sl_tp_from_main_order(order_id: str, cl_ord_id: Optional[str] = None) -> bool:
    """
    根据OKX客服建议：通过主订单查询止损止盈信息
    使用 GET /api/v5/trade/order 查询主订单的止损止盈信息
    增加cl_ord_id参数辅助查询
    """
    try:
        logger.info(f"🔍 通过主订单查询止损止盈信息: {order_id} (自定义ID: {cl_ord_id or '未设置'})")
        
        params = {
            'instId': get_correct_inst_id(),
            'ordId': order_id,
        }
        # 如果有自定义ID，也尝试用自定义ID查询
        if cl_ord_id:
            alt_params = {
                'instId': get_correct_inst_id(),
                'clOrdId': cl_ord_id,
            }
        
        response = exchange.private_get_trade_order(params)
        
        # 如果主查询失败且有自定义ID，尝试用自定义ID查询
        if (not response or response.get('code') != '0') and cl_ord_id:
            logger.info(f"🔍 尝试用自定义ID查询: {cl_ord_id}")
            response = exchange.private_get_trade_order(alt_params)
        
        if response and response.get('code') == '0':
            orders = response.get('data', [])
            if orders:
                order_info = orders[0]
                logger.info(f"📋 主订单信息:")
                logger.info(f"   订单ID: {order_info.get('ordId')}")
                logger.info(f"   自定义ID: {order_info.get('clOrdId')}")
                logger.info(f"   状态: {order_info.get('state')}")
                logger.info(f"   方向: {order_info.get('side')}")
                logger.info(f"   数量: {order_info.get('sz')}")
                
                # 检查是否有附加的止损止盈信息
                attach_algo_ords = order_info.get('attachAlgoOrds', [])
                if attach_algo_ords:
                    logger.info(f"✅ 发现附加的止损止盈订单: {len(attach_algo_ords)}个")
                    for algo_ord in attach_algo_ords:
                        algo_id = algo_ord.get('algoId', 'Unknown')
                        algo_cl_ord_id = algo_ord.get('algoClOrdId', 'Unknown')
                        algo_type = algo_ord.get('algoOrdType', 'Unknown')
                        logger.info(f"   算法订单ID: {algo_id}")
                        logger.info(f"   算法自定义ID: {algo_cl_ord_id}")
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

def check_algo_order_detail(algo_id: str, algo_cl_ord_id: Optional[str] = None) -> bool:
    """
    根据OKX客服建议：通过算法订单ID查询完整信息（适用于已触发的订单）
    使用 GET /api/v5/trade/order-algo 查询算法订单完整信息
    增加algo_cl_ord_id参数辅助查询
    """
    try:
        logger.info(f"🔍 查询算法订单完整信息: {algo_id} (自定义ID: {algo_cl_ord_id or '未设置'})")
        
        params = {
            'algoId': algo_id,
        }
        # 如果有自定义算法ID，准备备用查询参数
        if algo_cl_ord_id:
            alt_params = {
                'algoClOrdId': algo_cl_ord_id,
            }
        
        response = exchange.private_get_trade_order_algo(params)
        
        # 如果主查询失败且有自定义算法ID，尝试用自定义ID查询
        if (not response or response.get('code') != '0') and algo_cl_ord_id:
            logger.info(f"🔍 尝试用算法自定义ID查询: {algo_cl_ord_id}")
            response = exchange.private_get_trade_order_algo(alt_params)
        
        if response and response.get('code') == '0':
            orders = response.get('data', [])
            if orders:
                order_info = orders[0]
                logger.info(f"✅ 算法订单详细信息:")
                logger.info(f"   算法ID: {order_info.get('algoId')}")
                logger.info(f"   算法自定义ID: {order_info.get('algoClOrdId')}")
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

def cancel_sl_tp_orders(algo_ids: List[str], algo_cl_ord_ids: List[str]) -> bool:
    """通过algoId或algoClOrdId撤销止损止盈单（支持OCO订单）"""
    if not algo_ids and not algo_cl_ord_ids:
        logger.warning("⚠️ 没有需要撤销的订单ID")
        return True
    
    try:
        inst_id = get_correct_inst_id()
        success = True
        
        # 先尝试通过algoId撤销
        for algo_id in algo_ids:
            logger.info(f"🔄 尝试撤销算法订单: {algo_id}")
            params = {
                'instId': inst_id,
                'algoId': algo_id
            }
            response = exchange.private_post_trade_cancel_algos(params)
            
            if response and response.get('code') == '0':
                logger.info(f"✅ 成功撤销算法订单: {algo_id}")
            else:
                logger.error(f"❌ 撤销算法订单失败: {algo_id}, 响应: {response}")
                success = False
            
            time.sleep(1)
        
        # 再尝试通过algoClOrdId撤销
        for cl_ord_id in algo_cl_ord_ids:
            logger.info(f"🔄 尝试撤销算法订单(自定义ID): {cl_ord_id}")
            params = {
                'instId': inst_id,
                'algoClOrdId': cl_ord_id
            }
            response = exchange.private_post_trade_cancel_algos(params)
            
            if response and response.get('code') == '0':
                logger.info(f"✅ 成功撤销算法订单(自定义ID): {cl_ord_id}")
            else:
                logger.error(f"❌ 撤销算法订单失败(自定义ID): {cl_ord_id}, 响应: {response}")
                success = False
            
            time.sleep(1)
        
        return success
        
    except Exception as e:
        logger.error(f"撤销止损止盈单失败: {str(e)}")
        return False