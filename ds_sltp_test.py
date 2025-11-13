#!/usr/bin/env python3

# ds_sltp_test.py - BTC空单止盈止损测试程序（基于OKX客服建议优化）

import os
import time
import sys
import traceback
import uuid
import json
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple,Union
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

    logger.info("=" * 80)



def extract_sl_tp_trigger_prices(
    algo_result: Dict[str, Any],
    target_inst_id: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    解析策略委托单返回结果，提取指定交易对的止盈止损触发价格信息
    
    :param algo_result: algo_order_pending_get_comprehensive_info 函数的返回结果
    :param target_inst_id: 可选，指定交易对（如 'BTC-USDT-SWAP'），不指定则返回所有交易对
    :return: 包含止盈止损信息的列表，每个元素结构：
        {
            "inst_id": str,          # 交易对
            "algo_id": str,          # 策略订单ID
            "algo_cl_ord_id": str,   # 自定义策略ID
            "sl_trigger_px": float,  # 止损触发价（None表示未设置）
            "tp_trigger_px": float   # 止盈触发价（None表示未设置）
        }
    """
    # 验证输入有效性
    if not algo_result.get("success"):
        raise ValueError(f"无效的策略委托单数据：{algo_result.get('error', '未知错误')}")

    # 提取核心数据（兼容代码库中 algo_order_pending_get_comprehensive_info 的返回结构）
    pending_algos = algo_result.get("algo_orders", [])
    main_order_data = algo_result.get("main_order_data", {})
    default_inst_id = main_order_data.get("instId") or target_inst_id

    result = []
    for algo in pending_algos:
        # 提取交易对（优先从订单数据取，否则用默认值）
        inst_id = algo.get("instId") or default_inst_id
        if target_inst_id and inst_id != target_inst_id:
            continue  # 跳过非目标交易对

        # 解析触发价格（转换为浮点数，未设置则为None）
        sl_trigger_px = float(algo["slTriggerPx"]) if algo.get("slTriggerPx") else None
        tp_trigger_px = float(algo["tpTriggerPx"]) if algo.get("tpTriggerPx") else None

        # 整理结果
        result.append({
            "inst_id": inst_id,
            "algo_id": algo.get("algoId", "未知"),
            "algo_cl_ord_id": algo.get("algoClOrdId", "未设置"),
            "sl_trigger_px": sl_trigger_px,
            "tp_trigger_px": tp_trigger_px
        })

    return result

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
    全能平仓函数，支持市价平仓和限价平仓
    
    参数:
        side: 原持仓方向 ('buy' 或 'sell'/'short')，函数会自动计算平仓方向
        amount: 平仓数量，None则默认平掉全部持仓
        ord_type: 平仓类型，'market' 市价平仓，'limit' 限价平仓
        price: 限价平仓时的价格，市价平仓时忽略
        
    返回:
        包含平仓结果的字典，结构如下:
        {
            'success': bool,        # 操作是否成功
            'order_id': str,        # 订单ID，成功时有效
            'cl_ord_id': str,       # 自定义订单ID，成功时有效
            'response': Any,        # API响应数据
            'error': Optional[str]  # 错误信息，失败时有效
        }
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
        
        # 5. 生成自定义订单ID
        cl_ord_id = generate_cl_ord_id(close_side)
        
        # 6. 构建订单参数
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': close_side,
            'ordType': ord_type,
            'sz': str(amount),
            'clOrdId': cl_ord_id
        }
        
        # 7. 限价平仓时添加价格参数
        if ord_type == 'limit':
            if price is None:
                # 如果未指定限价，基于当前价格设置一个合理的默认值
                if close_side == 'buy':  # 平空单（买入）时，限价略高于当前价
                    price = current_price * 1.001
                else:  # 平多单（卖出）时，限价略低于当前价
                    price = current_price * 0.999
                logger.warning(f"⚠️ 未指定限价，自动设置为: {price:.2f}")
            
            params['px'] = str(price)
        
        # 8. 打印订单信息
        logger.info(f"📤 {action_name}参数:")
        logger.info(json.dumps(params, indent=2, ensure_ascii=False))
        logger.info(f"🎯 执行{action_name}: {amount} 张 {'@ ' + str(price) if ord_type == 'limit' else ''}")
        
        # 9. 执行平仓订单
        response = exchange.private_post_trade_order(params)
        
        # 10. 处理API响应
        logger.info(f"📥 {action_name}响应:")
        logger.info(json.dumps(response, indent=2, ensure_ascii=False))
        
        if not response or response.get('code') != '0':
            error_msg = response.get('msg', '未知错误') if response else '无响应数据'
            logger.error(f"❌ {action_name}失败: {error_msg}")
            return {
                'success': False,
                'error': error_msg,
                'order_id': None,
                'cl_ord_id': cl_ord_id,
                'response': response
            }
        
        # 11. 提取订单ID
        order_id = response['data'][0]['ordId'] if response.get('data') else None
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
        main_order_info = algo_order_pending_get_comprehensive_info(main_ord_id)
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
        params = [{
            "instId": inst_id,
            "algoId": algo_id
        }]
        
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
                if cancel_algo_order_by_attach_id(algo_cl_ord_id, inst_id):
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
        
        order_detail = algo_order_pending_get_comprehensive_info(order_result['order_id'])
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
    简化版全能交易函数：支持一次开单同时附带止损和止盈（通过同一算法参数数组）
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
            'clOrdId': cl_ord_id,
        }
        
        if ord_type == 'limit' and price is not None:
            params['px'] = str(price)
        
        # 止盈止损的方向与主订单相反（主多则止盈止损为空，主空则相反）
        opposite_side = 'buy' if side == 'sell' else 'sell'
        
        # 核心：整合止损和止盈到同一个算法参数数组（algo_params）
        algo_params = []  # 存放所有算法订单（可同时包含SL和TP）
        
        # 添加止损单（SL）到算法数组
        if stop_loss_price is not None:
            algo_params.append({
                'algoType': 'sl',  # 算法类型：止损
                'instId': inst_id,  # 与主订单标的一致
                'side': opposite_side,  # 方向与主订单相反
                'triggerPx': str(stop_loss_price),  # 止损触发价
                'ordType': 'market',  # 触发后以市价成交
                'sz': str(amount),  # 数量与主订单一致
                'clOrdId': generate_cl_ord_id(f"{side}_sl")  # 止损单唯一标识
            })
        
        # 添加止盈单（TP）到算法数组
        if take_profit_price is not None:
            algo_params.append({
                'algoType': 'tp',  # 算法类型：止盈
                'instId': inst_id,  # 与主订单标的一致
                'side': opposite_side,  # 方向与主订单相反
                'triggerPx': str(take_profit_price),  # 止盈触发价
                'ordType': 'market',  # 触发后以市价成交
                'sz': str(amount),  # 数量与主订单一致
                'clOrdId': generate_cl_ord_id(f"{side}_tp")  # 止盈单唯一标识
            })
        
        # 如果有止损或止盈，将算法数组附加到主订单参数中
        if algo_params:
            params['attachAlgoOrds'] = algo_params  # 关键：一次请求附带所有算法订单
        
        action_name = f"{'做多' if side == 'buy' else '做空'}{'市价' if ord_type == 'market' else '限价'}单"
        logger.info("📤 完整请求参数:")
        logger.info(json.dumps(params, indent=2, ensure_ascii=False))
        logger.info(f"🎯 执行{action_name}: {amount} 张（{'含止损止盈' if algo_params else '无止损止盈'}）")
        
        # 执行API调用（一次请求完成主订单+止损+止盈）
        response = exchange.private_post_trade_order(params)
        
        # 响应处理逻辑（保持不变）
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
        
        order_id = response['data'][0]['ordId'] if response.get('data') else None
        logger.info(f"✅ {action_name}创建成功: {order_id}（{'止损止盈已附加' if algo_params else ''}）")
        
        return {
            'success': True,
            'clOrdId': cl_ord_id,
            'algo_cl_ord_ids': [algo['clOrdId'] for algo in algo_params]  # 返回所有算法订单的ID
        }
            
    except Exception as e:
        logger.error(f"创建全能订单失败: {str(e)}")
        logger.error(f"异常堆栈: {traceback.format_exc()}")
        return {
            'success': False,
            'error': str(e),
            'response': None
        }

# # 1. 调用设置止损止盈
# sl_tp_result = sl_tp_algo_order_set(
#     side="short",
#     amount=0.1,
#     stop_loss_price=40000.0,
#     take_profit_price=38000.0
# )
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

# # 2. 调用确认函数验证--以下是示例。
# confirm_result = confirm_sl_tp_orders_by_params(
#     side="short",
#     amount=0.1,
#     stop_loss_price=40000.0,
#     take_profit_price=38000.0,
#     expected_algo_ids=sl_tp_result["algo_ids"],
#     expected_algo_cl_ord_ids=sl_tp_result["algo_cl_ord_ids"]
# )

def confirm_sl_tp_orders_by_params(
    side: str,
    amount: float,
    stop_loss_price: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    expected_algo_ids: List[str] = None,
    expected_algo_cl_ord_ids: List[str] = None,
    timeout: int = 30,
    interval: int = 3
) -> Dict[str, Any]:
    """
    基于参数原值与实际委托单信息比对，确认止盈止损委托单是否正确设置
    
    参数:
        side: 开仓方向（与set_sl_tp_separately一致）
        amount: 委托数量（与set_sl_tp_separately一致）
        stop_loss_price: 止损价格（与set_sl_tp_separately一致）
        take_profit_price: 止盈价格（与set_sl_tp_separately一致）
        expected_algo_ids: 预期算法订单ID（来自set_sl_tp_separately返回）
        expected_algo_cl_ord_ids: 预期自定义算法ID（来自set_sl_tp_separately返回）
        timeout: 超时时间（秒）
        interval: 检查间隔（秒）
    
    返回:
        确认结果字典，包含匹配状态、详细比对信息及异常原因
    """
    result = {
        "success": False,
        "matched_orders": [],  # 完全匹配的订单详情
        "mismatched_orders": [],  # 存在不匹配的订单详情及原因
        "missing_orders": [],  # 未找到的预期订单ID
        "unexpected_orders": []  # 非预期但存在的订单
    }
    expected_algo_ids = expected_algo_ids or []
    expected_algo_cl_ord_ids = expected_algo_cl_ord_ids or []
    
    # 1. 定义预期参数模板（与set_sl_tp_separately的设置逻辑一致）
    inst_id = get_correct_inst_id()
    opposite_side = "buy" if side in ("sell", "short") else "sell"  # 平仓方向
    expected_ord_type = "conditional"  # 单独设置时均为条件单
    expected_sz = str(amount)  # 数量需转为字符串（与API参数一致）
    
    # 2. 超时循环检查
    start_time = time.time()
    while time.time() - start_time < timeout:
        # 重置本轮状态
        current_matched = []
        current_mismatched = []
        current_unexpected = []
        checked_ids = set()  # 已检查的订单ID，用于排查重复或多余订单
        
        # 3. 获取实际未完成的算法订单（调用综合查询函数）
        try:
            # 假设algo_order_pending_get_comprehensive_info的参数与algo_order_pending_get_comprehensive_info类似
            pending_orders = algo_order_pending_get_comprehensive_info(
                inst_id=inst_id,
                ord_types=["conditional"]  # 单独设置的止损止盈均为条件单
            )
            # 假设返回格式为：{"success": bool, "data": List[订单详情字典]}
            if not pending_orders.get("success"):
                logger.warning("⚠️ 未获取到有效委托单信息，重试中...")
                time.sleep(interval)
                continue
            actual_orders = pending_orders["data"]
        except Exception as e:
            logger.error(f"查询委托单信息异常: {str(e)}", exc_info=True)
            time.sleep(interval)
            continue
        
        # 4. 比对预期订单与实际订单
        # 4.1 处理预期的止损单
        if stop_loss_price is not None:
            expected_sl_trigger = str(stop_loss_price)
            # 遍历实际订单查找匹配的止损单
            sl_matched = False
            for order in actual_orders:
                # 匹配条件：ID匹配 + 核心参数匹配
                if (order.get("algoId") in expected_algo_ids or 
                    order.get("algoClOrdId") in expected_algo_cl_ord_ids):
                    checked_ids.add(order.get("algoId"))
                    checked_ids.add(order.get("algoClOrdId"))
                    
                    # 核心参数比对
                    mismatches = []
                    if order.get("ordType") != expected_ord_type:
                        mismatches.append(f"订单类型不符（预期: {expected_ord_type}, 实际: {order.get('ordType')}）")
                    if order.get("side") != opposite_side:
                        mismatches.append(f"方向不符（预期: {opposite_side}, 实际: {order.get('side')}）")
                    if order.get("sz") != expected_sz:
                        mismatches.append(f"数量不符（预期: {expected_sz}, 实际: {order.get('sz')}）")
                    if order.get("slTriggerPx") != expected_sl_trigger:
                        mismatches.append(f"止损触发价不符（预期: {expected_sl_trigger}, 实际: {order.get('slTriggerPx')}）")
                    if order.get("state") not in ("live", "effective"):
                        mismatches.append(f"状态无效（当前: {order.get('state')}）")
                    
                    if not mismatches:
                        current_matched.append({
                            "type": "stop_loss",
                            "algo_id": order.get("algoId"),
                            "algo_cl_ord_id": order.get("algoClOrdId"),
                            "details": order
                        })
                        sl_matched = True
                    else:
                        current_mismatched.append({
                            "type": "stop_loss",
                            "algo_id": order.get("algoId"),
                            "reason": mismatches
                        })
            
            # 若未匹配到预期的止损单
            if not sl_matched:
                sl_expected_id = next(
                    (id for id in expected_algo_ids if "sl" in id.lower()),  # 假设ID含sl标识
                    None
                ) or next(
                    (cl_id for cl_id in expected_algo_cl_ord_ids if "sl" in cl_id.lower()),
                    "unknown_sl_id"
                )
                current_missing = {
                    "type": "stop_loss",
                    "expected_id": sl_expected_id,
                    "expected_trigger_price": stop_loss_price
                }
                current_missing.extend(current_missing)
        
        # 4.2 处理预期的止盈单
        if take_profit_price is not None:
            expected_tp_trigger = str(take_profit_price)
            # 遍历实际订单查找匹配的止盈单
            tp_matched = False
            for order in actual_orders:
                if (order.get("algoId") in expected_algo_ids or 
                    order.get("algoClOrdId") in expected_algo_cl_ord_ids):
                    checked_ids.add(order.get("algoId"))
                    checked_ids.add(order.get("algoClOrdId"))
                    
                    # 核心参数比对
                    mismatches = []
                    if order.get("ordType") != expected_ord_type:
                        mismatches.append(f"订单类型不符（预期: {expected_ord_type}, 实际: {order.get('ordType')}）")
                    if order.get("side") != opposite_side:
                        mismatches.append(f"方向不符（预期: {opposite_side}, 实际: {order.get('side')}）")
                    if order.get("sz") != expected_sz:
                        mismatches.append(f"数量不符（预期: {expected_sz}, 实际: {order.get('sz')}）")
                    if order.get("tpTriggerPx") != expected_tp_trigger:
                        mismatches.append(f"止盈触发价不符（预期: {expected_tp_trigger}, 实际: {order.get('tpTriggerPx')}）")
                    if order.get("state") not in ("live", "effective"):
                        mismatches.append(f"状态无效（当前: {order.get('state')}）")
                    
                    if not mismatches:
                        current_matched.append({
                            "type": "take_profit",
                            "algo_id": order.get("algoId"),
                            "algo_cl_ord_id": order.get("algoClOrdId"),
                            "details": order
                        })
                        tp_matched = True
                    else:
                        current_mismatched.append({
                            "type": "take_profit",
                            "algo_id": order.get("algoId"),
                            "reason": mismatches
                        })
            
            # 若未匹配到预期的止盈单
            if not tp_matched:
                tp_expected_id = next(
                    (id for id in expected_algo_ids if "tp" in id.lower()),  # 假设ID含tp标识
                    None
                ) or next(
                    (cl_id for cl_id in expected_algo_cl_ord_ids if "tp" in cl_id.lower()),
                    "unknown_tp_id"
                )
                current_missing = {
                    "type": "take_profit",
                    "expected_id": tp_expected_id,
                    "expected_trigger_price": take_profit_price
                }
                result["missing_orders"].append(current_missing)
        
        # 4.3 检查是否存在非预期订单（未在expected_ids中但属于当前交易对的订单）
        for order in actual_orders:
            order_id = order.get("algoId")
            order_cl_id = order.get("algoClOrdId")
            if (order_id not in expected_algo_ids and 
                order_cl_id not in expected_algo_cl_ord_ids and 
                order.get("instId") == inst_id):
                current_unexpected.append({
                    "algo_id": order_id,
                    "algo_cl_ord_id": order_cl_id,
                    "type": "stop_loss" if order.get("slTriggerPx") else "take_profit"
                })
        
        # 5. 更新结果并检查是否完成确认
        result["matched_orders"] = current_matched
        result["mismatched_orders"] = current_mismatched
        result["unexpected_orders"] = current_unexpected
        
        # 所有预期订单均匹配且无异常时，确认成功
        total_expected = sum(1 for p in [stop_loss_price, take_profit_price] if p is not None)
        if len(current_matched) == total_expected and not current_mismatched:
            result["success"] = True
            logger.info(f"🎉 所有止盈止损委托单均匹配成功（{len(current_matched)}/{total_expected}）")
            return result
        
        # 未完成确认，继续等待
        remaining_time = int(timeout - (time.time() - start_time))
        logger.info(f"⏳ 等待{remaining_time}秒后重试，已匹配{len(current_matched)}/{total_expected}个订单")
        time.sleep(interval)
    
    # 超时处理
    logger.error("❌ 止盈止损委托单确认超时")
    return result




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


# 确认策略订单是否正确下发----使用示例如下
    # 验证止盈单
    # tp_confirm = confirm_algo_order_by_clId(
    #     side="short",
    #     amount=0.1,
    #     take_profit_price=38000.0,
    #     algo_cl_ord_id=sl_tp_result["algo_cl_ord_ids"][1],  # 取止盈单ID
    #     timeout=60
    # )
    
    # if sl_confirm["success"] and tp_confirm["success"]:
    #     logger.info("所有止损止盈单均正确下发")
    # else:
    #     if not sl_confirm["success"]:
    #         logger.error(f"止损单验证失败: {sl_confirm['error'] or sl_confirm['reason']}")
    #     if not tp_confirm["success"]:
    #         logger.error(f"止盈单验证失败: {tp_confirm['error'] or tp_confirm['reason']}")

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
        if order_data.get("ordType") != "conditional":
            mismatches.append(
                f"订单类型不符（预期: conditional, 实际: {order_data.get('ordType')}）"
            )
        if order_data.get("state") not in ("live", "effective"):
            mismatches.append(
                f"订单状态无效（当前: {order_data.get('state')}）"
            )
        
        # 2. 区分止损/止盈单，校验触发价
        sl_trigger_px = order_data.get("slTriggerPx")
        tp_trigger_px = order_data.get("tpTriggerPx")
        expected_sl = str(stop_loss_price) if stop_loss_price else None
        expected_tp = str(take_profit_price) if take_profit_price else None
        
        if sl_trigger_px:
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
                "type": "stop_loss" if sl_trigger_px else "take_profit",
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

    # 保存用于后续查找的信息
    cl_order_id = short_order_result['cl_ord_id']
    saved_attach_algo_cl_ord_id = short_order_result['attach_algo_cl_ord_ids']

    logger.info(f"💾 保存的订单信息:")
    logger.info(f"   cl_order_id: {cl_order_id}")
    logger.info(f"   attach_algo_cl_ord_ids: {saved_attach_algo_cl_ord_id}")

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
        
    if sl_tp_set_result["algo_cl_ord_ids"] :
        sltp_confirm = confirm_algo_order_by_clId(
        side="short",
        amount=0.1,
        take_profit_price=new_tp,
        stop_loss_price=new_sl,
        algo_cl_ord_id=sl_tp_set_result["algo_cl_ord_ids"],  # 取止盈单ID
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