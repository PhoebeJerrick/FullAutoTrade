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
        single_order_response = exchange.private_get_trade_order(single_order_params)
        all_responses["single_order"] = single_order_response
        logger.info(f"📋 单个订单原始响应：{single_order_response}")
        
        # --------------------------
        # 2. 查询未成交订单（含条件单/止盈止损单）
        # --------------------------
        pending_orders_params = {
            "instType": "SWAP",  # 现货/合约类型，根据实际场景调整
            "instId": inst_id,   # 限定当前产品
            "ordType": "conditional,oco",  # 重点查询条件单和OCO单（止盈止损常用类型）
            "state": "live"      # 只查活跃的未成交订单
        }
        logger.info(f"\n🔍 [2/3] 调用GET /trade/orders-pending（未成交订单）：instId={inst_id}")
        pending_orders_response = exchange.private_get_trade_orders_pending(pending_orders_params)
        all_responses["pending_orders"] = pending_orders_response
        logger.info(f"📋 未成交订单原始响应：{pending_orders_response}")
        
        # --------------------------
        # 3. 查询历史订单（含已成交/已撤销）
        # --------------------------
        history_orders_params = {
            "instType": "SWAP",
            "instId": inst_id,
            "ordId": ord_id,     # 限定查询当前主订单的历史记录
            "state": "filled,canceled"  # 重点查已成交和已撤销状态
        }
        logger.info(f"\n🔍 [3/3] 调用GET /trade/orders-history（历史订单）：ordId={ord_id}, instId={inst_id}")
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


"""获取主订单关联的所有附带止盈止损单的attachAlgoId"""
def get_attach_algo_ids_from_main_order(main_ord_id: str) -> List[str]:
    """从主订单详情中提取附带止盈止损单的attachAlgoId（文档中attachAlgoOrds字段）"""
    try:
        inst_id = get_correct_inst_id()
        params = {
            "instId": inst_id,
            "ordId": main_ord_id  # 主订单ID
        }
        
        # 查询主订单详情（包含attachAlgoOrds字段）
        response = exchange.private_get_trade_order(params)
        
        if response and response.get("code") == "0":
            main_order_data = response.get("data", [])[0] if response.get("data") else {}
            attach_algo_ords = main_order_data.get("attachAlgoOrds", [])  # 附带的止盈止损单数组
            attach_algo_ids = [ord.get("attachAlgoId") for ord in attach_algo_ords if ord.get("attachAlgoId")]
            
            if attach_algo_ids:
                logger.info(f"📌 从主订单{main_ord_id}获取到{len(attach_algo_ids)}个attachAlgoId")
                return attach_algo_ids
            else:
                logger.warning(f"⚠️ 主订单{main_ord_id}未关联任何附带止盈止损单")
                return []
        else:
            logger.error(f"❌ 查询主订单详情失败：{response}")
            return []
            
    except Exception as e:
        logger.error(f"获取attachAlgoId出错：{str(e)}")
        return []


"""全能撤销当前币种的所有附带止盈止损单"""
def cancel_all_attached_sl_tp_versatile(main_ord_id: Optional[str] = None) -> bool:
    """
    完整撤销逻辑：
    1. 优先通过主订单ID获取attachAlgoId（最精准，文档推荐）
    2. 若主订单ID未知，全局查询所有附带止盈止损单
    3. 逐个通过attachAlgoId修改触发价为0实现撤销
    """
    inst_id = get_correct_inst_id()
    success = True
    attach_algo_ids = []
    
    # 步骤1：通过主订单ID获取attachAlgoId（最可靠）
    if main_ord_id:
        logger.info(f"🔍 步骤1：通过主订单ID={main_ord_id}查询附带止盈止损单")
        attach_algo_ids = get_attach_algo_ids_from_main_order(main_ord_id)
    
    # 步骤2：若未获取到，全局查询活跃的附带止盈止损单
    if not attach_algo_ids:
        logger.info("🔍 步骤2：全局查询活跃的附带止盈止损单")
        params = {
            "instType": "SWAP",
            "instId": inst_id,
            "ordType": "conditional,oco",
            "state": "live"
        }
        response = exchange.private_get_trade_orders_algo_pending(params)
        if response and response.get("code") == "0":
            # 从全局订单中提取attachAlgoId（适用于主订单ID未知的场景）
            for order in response.get("data", []):
                if "attachAlgoId" in order:  # 筛选附带的止盈止损单
                    attach_algo_ids.append(order["attachAlgoId"])
            logger.info(f"📌 全局查询到{len(attach_algo_ids)}个附带止盈止损单")
    
    if not attach_algo_ids:
        logger.info("✅ 没有需要撤销的附带止盈止损单")
        return True
    
    # 步骤3：逐个撤销（必传attachAlgoId，严格遵循文档）
    logger.warning(f"⚠️ 开始撤销{len(attach_algo_ids)}个附带止盈止损单...")
    for attach_id in attach_algo_ids:
        if not amend_attached_sl_tp_to_zero(attach_id, inst_id, main_ord_id):
            logger.error(f"❌ 撤销失败：attachAlgoId={attach_id}")
            success = False
        time.sleep(1)  # 避免接口限流
    
    # 最终检查
    time.sleep(3)
    final_attach_ids = get_attach_algo_ids_from_main_order(main_ord_id) if main_ord_id else []
    if not final_attach_ids:
        logger.info("✅ 所有附带止盈止损单已成功撤销")
        return True
    else:
        logger.error(f"❌ 仍有{len(final_attach_ids)}个附带止盈止损单未撤销")
        return success

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
        return False
    
    success = True
    inst_id = get_correct_inst_id()
    
    # 优先通过自定义ID撤销（更可靠）
    for cl_ord_id in algo_cl_ord_ids:
        try:
            params = {
                'instId': inst_id,
                'algoClOrdId': cl_ord_id,  # 使用自定义ID撤销
            }
            response = exchange.private_post_trade_cancel_order_algo(params)
            if response.get('code') != '0':
                logger.error(f"❌ 撤销自定义ID {cl_ord_id} 失败: {response}")
                success = False
            else:
                logger.info(f"✅ 撤销自定义ID {cl_ord_id} 成功")
        except Exception as e:
            logger.error(f"撤销自定义ID {cl_ord_id} 出错: {str(e)}")
            success = False
    
    # 再通过系统algoId撤销（兜底）
    for algo_id in algo_ids:
        try:
            params = {
                'instId': inst_id,
                'algoId': algo_id,
            }
            response = exchange.private_post_trade_cancel_order_algo(params)
            if response.get('code') != '0':
                logger.error(f"❌ 撤销algoId {algo_id} 失败: {response}")
                success = False
            else:
                logger.info(f"✅ 撤销algoId {algo_id} 成功")
        except Exception as e:
            logger.error(f"撤销algoId {algo_id} 出错: {str(e)}")
            success = False
    
    return success

def get_algo_orders_from_main_order(order_id: str) -> Dict[str, List[str]]:
    """从主订单获取关联的算法订单ID"""
    result = {
        'algo_ids': [],
        'algo_cl_ord_ids': []
    }
    
    try:
        params = {
            'instId': get_correct_inst_id(),
            'ordId': order_id,
        }
        
        response = exchange.private_get_trade_order(params)
        
        if response and response.get('code') == '0':
            orders = response.get('data', [])
            if orders:
                attach_algo_ords = orders[0].get('attachAlgoOrds', [])
                for algo in attach_algo_ords:
                    algo_id = algo.get('algoId')
                    cl_ord_id = algo.get('algoClOrdId')
                    if algo_id and algo_id != 'Unknown':
                        result['algo_ids'].append(algo_id)
                    if cl_ord_id and cl_ord_id != 'Unknown':
                        result['algo_cl_ord_ids'].append(cl_ord_id)
        
        if not result['algo_ids'] and not result['algo_cl_ord_ids']:
            logger.warning(f"⚠️ 未获取到algoId或algoClOrdId，主订单ID: {order_id}")
            
        return result
        
    except Exception as e:
        logger.error(f"获取算法订单ID失败: {str(e)}")
        return result

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
    增加了attachAlgoClOrdId支持，用于更可靠地追踪止损止盈订单
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
        
        # 生成主订单自定义ID
        cl_ord_id = generate_cl_ord_id(side)
        
        # 基础参数构建
        params = {
            'instId': inst_id,
            'tdMode': config.margin_mode,
            'side': side,
            'ordType': ord_type,
            'sz': str(amount),
            'clOrdId': cl_ord_id  # 添加主订单自定义ID
        }
        
        # 限价单价格设置
        if ord_type == 'limit' and price is not None:
            params['px'] = str(price)
            logger.info(f"💰 限价单价格: {price:.2f}")
                
        # 整合止损和止盈到同一个algo参数（兼容单/双参数场景）
        algo_ords = []
        opposite_side = 'buy' if side == 'sell' else 'sell'  # 止损止盈方向为相反方向
        algo = {}  # 初始化空的算法订单配置

        # 添加止损参数（如果存在）
        if stop_loss_price is not None:
            algo['slTriggerPx'] = str(stop_loss_price)
            algo['slOrdPx'] = '-1'  # 市价止损
            logger.info(f"🛡️ 止损: {stop_loss_price:.2f} (方向: {opposite_side})")

        # 添加止盈参数（如果存在）
        if take_profit_price is not None:
            algo['tpTriggerPx'] = str(take_profit_price)
            algo['tpOrdPx'] = '-1'  # 市价止盈
            logger.info(f"🎯 止盈: {take_profit_price:.2f} (方向: {opposite_side})")

        # 如果存在止损或止盈，补充共用参数并添加到列表
        if algo:  # 只有当至少有一个参数时才处理
            # 补充共用参数（数量、方向、订单类型）
            algo['sz'] = str(amount)
            algo['side'] = opposite_side
            algo['algoOrdType'] = 'conditional'
            # 为算法订单添加自定义ID（关键改进点）
            algo['algoClOrdId'] = generate_cl_ord_id(side)
            logger.info(f"📌 算法订单自定义ID: {algo['algoClOrdId']}")
            algo_ords.append(algo)  # 此时algo_ords最多只有一个元素    

        # 添加止损止盈到主订单参数
        if algo_ords:
            params['attachAlgoOrds'] = algo_ords
        
        # 日志与订单执行
        action_name = f"{'做多' if side == 'buy' else '做空'}{'市价' if ord_type == 'market' else '限价'}单"
        log_order_params(action_name, params, "create_universal_order")
        logger.info(f"🎯 执行{action_name}: {amount} 张 (自定义ID: {cl_ord_id})")
        if algo_ords:
            logger.info(f"📋 附带条件单: {'、'.join(['止损' if 'slTriggerPx' in a else '止盈' for a in algo_ords])}")
        
        # 发送订单并处理响应
        response = exchange.private_post_trade_order(params)
        log_api_response(response, "create_universal_order")
        
        result = {
            'order_id': None, 
            'cl_ord_id': cl_ord_id,  # 返回主订单自定义ID
            'response': response, 
            'algo_ids': [], 
            'algo_cl_ord_ids': [],  # 返回算法订单自定义ID
            'success': False
        }
        
        if response and response.get('code') == '0':
            result['success'] = True
            result['order_id'] = response['data'][0]['ordId'] if response.get('data') else 'Unknown'
            logger.info(f"✅ {action_name}创建成功: {result['order_id']} (自定义ID: {cl_ord_id})")
            
            # 提取algoId和algoClOrdId
            if response and response.get('code') == '0' and response.get('data'):
                # 遍历所有数据
                for data in response['data']:
                    # 检查是否存在附加的算法订单信息
                    if 'attachAlgoOrds' in data:
                        for algo_ord in data['attachAlgoOrds']:
                            if 'algoId' in algo_ord:
                                algo_id = algo_ord['algoId']
                                if algo_id not in result['algo_ids']:
                                    result['algo_ids'].append(algo_id)
                                    logger.info(f"✅ 条件单创建成功: {algo_id}")
                            if 'algoClOrdId' in algo_ord:
                                algo_cl_ord_id = algo_ord['algoClOrdId']
                                if algo_cl_ord_id not in result['algo_cl_ord_ids']:
                                    result['algo_cl_ord_ids'].append(algo_cl_ord_id)
                                    logger.info(f"✅ 条件单自定义ID: {algo_cl_ord_id}")
                    # 同时检查当前data是否直接包含algoId（兼容不同返回格式）
                    elif 'algoId' in data:
                        algo_id = data['algoId']
                        if algo_id not in result['algo_ids']:
                            result['algo_ids'].append(algo_id)
                            logger.info(f"✅ 条件单创建成功: {algo_id}")
                    elif 'algoClOrdId' in data:
                        algo_cl_ord_id = data['algoClOrdId']
                        if algo_cl_ord_id not in result['algo_cl_ord_ids']:
                            result['algo_cl_ord_ids'].append(algo_cl_ord_id)
                            logger.info(f"✅ 条件单自定义ID: {algo_cl_ord_id}")
            
            # 验证止损止盈设置
            if verify_sl_tp and algo_ords:
                logger.info("🔍 验证止损止盈设置...")
                time.sleep(2)
                if check_sl_tp_from_main_order(result['order_id'], result['cl_ord_id']):
                    logger.info("✅ 止损止盈设置验证成功")
                else:
                    logger.warning("⚠️ 止损止盈设置验证失败，建议手动确认")
        else:
            logger.error(f"❌ {action_name}创建失败: {response}")
        
        return result
            
    except Exception as e:
        logger.error(f"创建全能订单失败: {str(e)}")
        logger.error(f"详细错误信息: {traceback.format_exc()}")
        return {
            'order_id': None, 
            'cl_ord_id': None,
            'response': None, 
            'algo_ids': [], 
            'algo_cl_ord_ids': [],
            'success': False
        }

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

def set_sl_tp_separately(side: str, amount: float, stop_loss_price: float, take_profit_price: float) -> Dict[str, List[str]]:
    """分开设置止损和止盈订单 - 备选方案，返回算法订单ID和自定义ID"""
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
            'algoClOrdId': sl_cl_ord_id  # 添加止损订单自定义ID
        }
        
        logger.info(f"🛡️ 设置止损订单 (自定义ID: {sl_cl_ord_id})...")
        sl_response = exchange.private_post_trade_order_algo(sl_params)
        
        if sl_response and sl_response.get('code') == '0':
            sl_algo_id = sl_response['data'][0]['algoId'] if sl_response.get('data') else 'Unknown'
            logger.info(f"✅ 止损订单设置成功: {sl_algo_id} (自定义ID: {sl_cl_ord_id})")
            result['algo_ids'].append(sl_algo_id)
            result['algo_cl_ord_ids'].append(sl_cl_ord_id)
        else:
            logger.error(f"❌ 止损订单设置失败: {sl_response}")
            return result
        
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
            'algoClOrdId': tp_cl_ord_id  # 添加止盈订单自定义ID
        }
        
        logger.info(f"🎯 设置止盈订单 (自定义ID: {tp_cl_ord_id})...")
        tp_response = exchange.private_post_trade_order_algo(tp_params)
        
        if tp_response and tp_response.get('code') == '0':
            tp_algo_id = tp_response['data'][0]['algoId'] if tp_response.get('data') else 'Unknown'
            logger.info(f"✅ 止盈订单设置成功: {tp_algo_id} (自定义ID: {tp_cl_ord_id})")
            result['algo_ids'].append(tp_algo_id)
            result['algo_cl_ord_ids'].append(tp_cl_ord_id)
            return result
        else:
            logger.error(f"❌ 止盈订单设置失败: {tp_response}")
            # 如果止盈设置失败，尝试撤销已设置的止损
            cancel_sl_tp_orders([sl_algo_id], [sl_cl_ord_id])
            return result
            
    except Exception as e:
        logger.error(f"分开设置止损止盈失败: {str(e)}")
        return result

def cancel_sl_tp_by_custom_id(target_cl_ord_ids: List[str]) -> bool:
    """兜底方案：查询所有算法订单，通过自定义ID匹配并撤销"""
    if not target_cl_ord_ids:
        return False
    
    try:
        inst_id = get_correct_inst_id()
        params = {'instId': inst_id, 'algoType': 'conditional'}  # 查询条件单
        response = exchange.private_get_trade_orders_algo(params)
        
        if response.get('code') != '0':
            logger.error(f"查询算法订单失败: {response}")
            return False
        
        # 遍历所有算法订单，匹配自定义ID并撤销
        for order in response.get('data', []):
            if order.get('algoClOrdId') in target_cl_ord_ids:
                algo_id = order.get('algoId')
                if algo_id and cancel_sl_tp_orders([algo_id], []):
                    return True
        return False
    except Exception as e:
        logger.error(f"兜底撤销失败: {str(e)}")
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
    short_cl_ord_id = short_order_result['cl_ord_id']
    initial_algo_ids = short_order_result['algo_ids']
    initial_algo_cl_ord_ids = short_order_result['algo_cl_ord_ids']
    
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
    has_sl_tp = check_sl_tp_from_main_order(short_order_id, short_cl_ord_id)
    sl_tp_ids = {
        'algo_ids': initial_algo_ids,
        'algo_cl_ord_ids': initial_algo_cl_ord_ids
    }
    
    if not has_sl_tp:
        logger.warning("⚠️ 通过主订单未发现止损止盈信息，尝试分开设置...")
        
        # 备选方案：分开设置止损止盈
        recalculated_sl, recalculated_tp = calculate_stop_loss_take_profit_prices('short', short_position['entry_price'])
        
        sl_tp_ids = set_sl_tp_separately('short', short_position['size'], recalculated_sl, recalculated_tp)
        
        if sl_tp_ids['algo_ids'] or sl_tp_ids['algo_cl_ord_ids']:
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

    # 使用新的全能撤销函数
    if cancel_all_attached_sl_tp_versatile(short_order_id):
        logger.info("✅ 止盈止损单取消成功")
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
        if cancel_all_attached_sl_tp_versatile(short_order_id) and not check_sl_tp_orders():
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
    sl_tp_ids = set_sl_tp_separately('short', short_position['size'], new_sl, new_tp)
    if not sl_tp_ids['algo_ids'] and not sl_tp_ids['algo_cl_ord_ids']:
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