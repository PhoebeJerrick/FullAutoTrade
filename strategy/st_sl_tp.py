# Strategy/st_sl_tp.py
import math
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple, Union
import logging

# 导入配置管理器
from Strategy.config_manager import get_config_manager, StrategyConfig

# 导入必要的模块
try:
    from trade_logger import logger
except ImportError:
    # 备用日志
    logger = logging.getLogger(__name__)

class StopLossTakeProfitStrategy:
    """
    止盈止损策略管理器
    负责计算和管理各种止盈止损策略
    """
    
    def __init__(self, symbol_configs: Dict, config_file: str = "strategy_config.json"):
        self.symbol_configs = symbol_configs
        self.config_manager = get_config_manager(config_file)
        self.config = self.config_manager.current_config
    
    def reload_config(self):
        """重新加载配置"""
        self.config_manager.load_config()
        self.config = self.config_manager.current_config
        logger.log_info("🔄 止盈止损策略配置已重新加载")
    
    def calculate_adaptive_stop_loss(self, symbol: str, side: str, current_price: float, price_data: dict) -> float:
        """自适应止损计算 - 集成配置管理"""
        config = self.symbol_configs[symbol]
        sl_config = self.config.stop_loss
        
        try:
            df = price_data['full_data']
            atr = self.calculate_atr(df)
            
            # 使用配置的ATR倍数
            atr_stop_distance = atr * sl_config.atr_multiplier
            
            # 方法2: 基于支撑阻力位的止损
            levels = price_data['levels_analysis']
            
            if side == 'long':
                # 多头：止损在支撑位下方
                support_level = levels.get('static_support', current_price * (1 - sl_config.min_stop_loss_ratio))
                dynamic_support = levels.get('dynamic_support', current_price * (1 - sl_config.min_stop_loss_ratio))
                
                # 选择较近的支撑位
                structure_stop = min(support_level, dynamic_support)
                
                # 结合ATR和结构止损，选择较近的
                atr_stop_price = current_price - atr_stop_distance
                stop_loss = max(structure_stop, atr_stop_price)
                
                # 确保止损合理（使用配置的最大止损比例）
                max_stop_distance = current_price * sl_config.max_stop_loss_ratio
                min_stop_price = current_price - max_stop_distance
                stop_loss = max(stop_loss, min_stop_price)
                
            else:  # short
                # 空头：止损在阻力位上方
                resistance_level = levels.get('static_resistance', current_price * (1 + sl_config.min_stop_loss_ratio))
                dynamic_resistance = levels.get('dynamic_resistance', current_price * (1 + sl_config.min_stop_loss_ratio))
                
                # 选择较远的阻力位（更严格的止损）
                structure_stop = max(resistance_level, dynamic_resistance)
                
                # 结合ATR和结构止损，选择较远的
                atr_stop_price = current_price + atr_stop_distance
                stop_loss = min(structure_stop, atr_stop_price)
                
                # 确保止损合理（使用配置的最大止损比例）
                max_stop_distance = current_price * sl_config.max_stop_loss_ratio
                max_stop_price = current_price + max_stop_distance
                stop_loss = min(stop_loss, max_stop_price)
            
            stop_distance_percent = abs(stop_loss - current_price) / current_price * 100
            direction = "above" if side == 'short' and stop_loss > current_price else "below"
            logger.log_info(f"🎯 {self.get_base_currency(symbol)}: 自适应止损 - {stop_loss:.2f} ({direction}当前价, 距离: {stop_distance_percent:.2f}%)")
            
            # 方向验证
            if side == 'long' and stop_loss >= current_price:
                logger.log_warning(f"⚠️ {self.get_base_currency(symbol)}: 多头止损价格异常({stop_loss:.2f} >= {current_price:.2f})，自动修正")
                stop_loss = current_price * (1 - sl_config.min_stop_loss_ratio)
                
            elif side == 'short' and stop_loss <= current_price:
                logger.log_warning(f"⚠️ {self.get_base_currency(symbol)}: 空头止损价格异常({stop_loss:.2f} <= {current_price:.2f})，自动修正")
                stop_loss = current_price * (1 + sl_config.min_stop_loss_ratio)
                
            return stop_loss
            
        except Exception as e:
            logger.log_error(f"adaptive_stop_loss_{self.get_base_currency(symbol)}", str(e))
            # 备用止损
            if side == 'long':
                return current_price * (1 - sl_config.min_stop_loss_ratio)
            else:
                return current_price * (1 + sl_config.min_stop_loss_ratio)

    def calculate_intelligent_take_profit(self, symbol: str, side: str, entry_price: float, price_data: dict, risk_reward_ratio: float = 2.0) -> float:
        """计算智能止盈价格 - 集成配置管理"""
        config = self.symbol_configs[symbol]
        sl_config = self.config.stop_loss
        tp_config = self.config.take_profit
        
        try:
            current_price = price_data['price']
            df = price_data['full_data']
            
            # 计算默认止盈比例
            default_tp_ratio = sl_config.min_stop_loss_ratio * risk_reward_ratio
            
            if side == 'long':
                # 多头止盈计算
                # 方法1: 基于阻力位
                resistance_level = price_data['levels_analysis'].get('static_resistance', current_price * (1 + default_tp_ratio * 2))
                
                # 方法2: 基于ATR
                atr = self.calculate_atr(df)
                atr_take_profit = current_price + (atr * risk_reward_ratio)
                
                # 方法3: 基于固定风险回报比
                risk = abs(entry_price - price_data.get('stop_loss', entry_price * (1 - sl_config.min_stop_loss_ratio)))
                rr_take_profit = entry_price + (risk * risk_reward_ratio)
                
                # 取最合理的止盈价格
                take_profit_price = min(resistance_level, atr_take_profit, rr_take_profit)
                
                # 确保止盈价格合理
                min_profit_ratio = sl_config.min_stop_loss_ratio * 0.5  # 最小盈利是止损的一半
                min_take_profit = current_price * (1 + min_profit_ratio)
                take_profit_price = max(take_profit_price, min_take_profit)
                
            else:  # short
                # 空头止盈计算
                # 方法1: 基于支撑位
                support_level = price_data['levels_analysis'].get('static_support', current_price * (1 - default_tp_ratio * 2))
                
                # 方法2: 基于ATR
                atr = self.calculate_atr(df)
                atr_take_profit = current_price - (atr * risk_reward_ratio)
                
                # 方法3: 基于固定风险回报比
                risk = abs(price_data.get('stop_loss', entry_price * (1 + sl_config.min_stop_loss_ratio)) - entry_price)
                rr_take_profit = entry_price - (risk * risk_reward_ratio)
                
                # 取最合理的止盈价格
                take_profit_price = max(support_level, atr_take_profit, rr_take_profit)
                
                # 确保止盈价格合理
                max_take_profit = current_price * (1 - min_profit_ratio)
                take_profit_price = min(take_profit_price, max_take_profit)
            
            take_profit_ratio = abs(take_profit_price - entry_price) / entry_price * 100
            profit_type = "above" if side == 'long' and take_profit_price > entry_price else "below"
            logger.log_info(f"🎯 {self.get_base_currency(symbol)}: 智能止盈计算 - 入场{entry_price:.2f}, 止盈{take_profit_price:.2f} ({profit_type}入场价, 盈利{take_profit_ratio:.2f}%)")
            
            return take_profit_price
            
        except Exception as e:
            logger.log_error(f"take_profit_calculation_{self.get_base_currency(symbol)}", f"止盈计算失败: {str(e)}")
            # 备用止盈计算
            default_tp_ratio = sl_config.min_stop_loss_ratio * risk_reward_ratio
            if side == 'long':
                return entry_price * (1 + default_tp_ratio)
            else:
                return entry_price * (1 - default_tp_ratio)

    def calculate_realistic_take_profit(self, symbol: str, side: str, entry_price: float, stop_loss: float, 
                                      price_data: dict, min_risk_reward: float) -> dict:
        """计算现实的止盈位置 - 集成配置管理"""
        sl_config = self.config.stop_loss
        tp_config = self.config.take_profit
        
        try:
            levels = price_data['levels_analysis']
            current_price = price_data['price']
            
            # 首先验证止损价格的合理性
            if side == 'long':
                if stop_loss >= entry_price:
                    logger.log_error(f"❌ {self.get_base_currency(symbol)}: 多头止损价格{stop_loss}高于入场价{entry_price}")
                    # 自动修正止损
                    stop_loss = entry_price * (1 - sl_config.min_stop_loss_ratio)
                    logger.log_warning(f"🔄 自动修正止损为: {stop_loss:.2f}")
            else:  # short
                if stop_loss <= entry_price:
                    logger.log_error(f"❌ {self.get_base_currency(symbol)}: 空头止损价格{stop_loss}低于入场价{entry_price}")
                    # 自动修正止损
                    stop_loss = entry_price * (1 + sl_config.min_stop_loss_ratio)
                    logger.log_warning(f"🔄 自动修正止损为: {stop_loss:.2f}")
            
            if side == 'long':
                # 理论止盈（基于最小盈亏比）
                risk = abs(entry_price - stop_loss)
                theoretical_tp = entry_price + (risk * min_risk_reward)
                
                # 现实止盈（基于阻力位）
                default_tp_ratio = sl_config.min_stop_loss_ratio * min_risk_reward
                resistance_level = levels.get('static_resistance', current_price * (1 + default_tp_ratio))
                dynamic_resistance = levels.get('dynamic_resistance', current_price * (1 + default_tp_ratio))
                realistic_tp = min(resistance_level, dynamic_resistance)
                
                # 选择较近的止盈
                take_profit = min(theoretical_tp, realistic_tp)
                
                # 计算实际盈亏比
                actual_reward = take_profit - entry_price
                actual_rr = actual_reward / risk if risk > 0 else 0
                
            else:  # short
                # 理论止盈（基于最小盈亏比）
                risk = abs(stop_loss - entry_price)
                theoretical_tp = entry_price - (risk * min_risk_reward)
                
                # 现实止盈（基于支撑位）
                default_tp_ratio = sl_config.min_stop_loss_ratio * min_risk_reward
                support_level = levels.get('static_support', current_price * (1 - default_tp_ratio))
                dynamic_support = levels.get('dynamic_support', current_price * (1 - default_tp_ratio))
                realistic_tp = max(support_level, dynamic_support)
                
                # 选择较近的止盈
                take_profit = max(theoretical_tp, realistic_tp)
                
                # 计算实际盈亏比
                actual_reward = entry_price - take_profit
                actual_rr = actual_reward / risk if risk > 0 else 0
            
            return {
                'take_profit': take_profit,
                'actual_risk_reward': actual_rr,
                'is_acceptable': actual_rr >= min_risk_reward * 0.8  # 允许80%的阈值
            }
            
        except Exception as e:
            logger.log_error(f"realistic_take_profit_{self.get_base_currency(symbol)}", str(e))
            # 备用止盈
            default_tp_ratio = sl_config.min_stop_loss_ratio * min_risk_reward
            if side == 'long':
                return {
                    'take_profit': entry_price * (1 + default_tp_ratio),
                    'actual_risk_reward': min_risk_reward,
                    'is_acceptable': True
                }
            else:
                return {
                    'take_profit': entry_price * (1 - default_tp_ratio),
                    'actual_risk_reward': min_risk_reward,
                    'is_acceptable': True
                }

    def calculate_aggressive_take_profit(self, symbol: str, side: str, entry_price: float, stop_loss: float, 
                                       price_data: dict, min_risk_reward: float, trend_strength: str) -> dict:
        """基于趋势强度的积极止盈计算 - 集成配置管理"""
        sl_config = self.config.stop_loss
        tp_config = self.config.take_profit
        
        try:
            levels = price_data['levels_analysis']
            current_price = price_data['price']
            
            # 根据趋势强度调整盈亏比目标
            trend_multiplier = tp_config.trend_strength_multipliers.get(trend_strength, 1.0)
            adjusted_min_rr = min_risk_reward * trend_multiplier
            
            # 限制最大风险回报比
            adjusted_min_rr = min(adjusted_min_rr, tp_config.max_risk_reward)
            
            if side == 'long':
                risk = abs(entry_price - stop_loss)
                
                # 方法1: 理论止盈（基于调整后的盈亏比）
                theoretical_tp = entry_price + (risk * adjusted_min_rr)
                
                # 方法2: 基于主要阻力位
                primary_resistance = levels.get('primary_resistance', current_price * (1 + sl_config.min_stop_loss_ratio * adjusted_min_rr * 2))
                
                # 方法3: 在强势趋势中，看更远的阻力位
                if trend_strength in ['STRONG_UPTREND', 'UPTREND']:
                    # 查看次要阻力位（如果有）
                    resistance_levels = levels.get('resistance_levels', [])
                    if len(resistance_levels) > 1:
                        # 取第二远的阻力位
                        secondary_resistance = sorted(resistance_levels)[-2] if len(resistance_levels) >= 2 else primary_resistance * (1 + sl_config.min_stop_loss_ratio * 0.5)
                    else:
                        secondary_resistance = primary_resistance * (1 + sl_config.min_stop_loss_ratio * 0.8)
                    
                    # 在强势趋势中，选择更远的止盈目标
                    realistic_tp = max(primary_resistance, secondary_resistance)
                else:
                    realistic_tp = primary_resistance
                
                # 选择理论止盈和现实阻力位中较远的一个
                take_profit = max(theoretical_tp, realistic_tp)
                
                # 但不要超过合理的最大止盈
                max_reasonable_tp = entry_price * (1 + sl_config.max_stop_loss_ratio * 3)  # 最大止盈是最大止损的3倍
                take_profit = min(take_profit, max_reasonable_tp)
                
                actual_reward = take_profit - entry_price
                actual_rr = actual_reward / risk if risk > 0 else 0
                
            else:  # short
                risk = abs(stop_loss - entry_price)
                
                # 方法1: 理论止盈
                theoretical_tp = entry_price - (risk * adjusted_min_rr)
                
                # 方法2: 基于主要支撑位
                primary_support = levels.get('primary_support', current_price * (1 - sl_config.min_stop_loss_ratio * adjusted_min_rr * 2))
                
                # 方法3: 在强势下跌趋势中，看更远的支撑位
                if trend_strength in ['STRONG_DOWNTREND', 'DOWNTREND']:
                    support_levels = levels.get('support_levels', [])
                    if len(support_levels) > 1:
                        # 取第二远的支撑位
                        secondary_support = sorted(support_levels)[1] if len(support_levels) >= 2 else primary_support * (1 - sl_config.min_stop_loss_ratio * 0.5)
                    else:
                        secondary_support = primary_support * (1 - sl_config.min_stop_loss_ratio * 0.8)
                    
                    # 在强势下跌趋势中，选择更远的止盈目标
                    realistic_tp = min(primary_support, secondary_support)
                else:
                    realistic_tp = primary_support
                
                # 选择理论止盈和现实支撑位中较近的一个
                take_profit = min(theoretical_tp, realistic_tp)
                
                # 但不低于合理的最小止盈
                min_reasonable_tp = entry_price * (1 - sl_config.max_stop_loss_ratio * 3)
                take_profit = max(take_profit, min_reasonable_tp)
                
                actual_reward = entry_price - take_profit
                actual_rr = actual_reward / risk if risk > 0 else 0
            
            return {
                'take_profit': take_profit,
                'actual_risk_reward': actual_rr,
                'is_acceptable': actual_rr >= min_risk_reward,
                'trend_adjusted_rr': adjusted_min_rr,
                'trend_strength': trend_strength
            }
            
        except Exception as e:
            logger.log_error(f"aggressive_take_profit_{self.get_base_currency(symbol)}", str(e))
            # 备用计算
            return self.calculate_realistic_take_profit(symbol, side, entry_price, stop_loss, price_data, min_risk_reward)

    def calculate_kline_based_stop_loss(self, side: str, entry_price: float, price_data: dict, max_stop_loss_ratio: float = None) -> float:
        """
        基于K线结构计算止损价格 - 集成配置管理
        """
        sl_config = self.config.stop_loss
        
        try:
            df = price_data['full_data']
            current_price = price_data['price']
            
            # 使用配置的最大止损比例，如果没有传入则使用默认值
            if max_stop_loss_ratio is None:
                max_stop_loss_ratio = sl_config.max_stop_loss_ratio
            
            # 计算ATR
            atr = self.calculate_atr(df)
            
            if side == 'long':
                # 多头止损：取支撑位和ATR止损中的较小值（更严格的止损）
                support_level = price_data['levels_analysis'].get('static_support', current_price * (1 - sl_config.min_stop_loss_ratio))
                
                # 基于ATR的止损
                stop_loss_by_atr = current_price - (atr * sl_config.atr_multiplier)
                
                # 选择较严格的止损
                stop_loss_price = min(support_level, stop_loss_by_atr)
                
                # 确保止损不超过最大比例
                max_stop_loss_price = current_price * (1 - max_stop_loss_ratio)
                stop_loss_price = max(stop_loss_price, max_stop_loss_price)
                
                # 确保止损在合理范围内（使用配置的最小止损比例）
                min_stop_loss = current_price * (1 - sl_config.min_stop_loss_ratio)
                stop_loss_price = max(stop_loss_price, min_stop_loss)
                
            else:  # short
                # 空头止损：取阻力位和ATR止损中的较大值（更严格的止损）
                resistance_level = price_data['levels_analysis'].get('static_resistance', current_price * (1 + sl_config.min_stop_loss_ratio))
                
                # 基于ATR的止损
                stop_loss_by_atr = current_price + (atr * sl_config.atr_multiplier)
                
                # 选择较严格的止损
                stop_loss_price = max(resistance_level, stop_loss_by_atr)
                
                # 确保止损不超过最大比例
                max_stop_loss_price = current_price * (1 + max_stop_loss_ratio)
                stop_loss_price = min(stop_loss_price, max_stop_loss_price)
                
                # 确保止损在合理范围内（使用配置的最小止损比例）
                max_stop_loss = current_price * (1 + sl_config.min_stop_loss_ratio)
                stop_loss_price = min(stop_loss_price, max_stop_loss)
            
            stop_loss_ratio = abs(stop_loss_price - current_price) / current_price * 100
            logger.log_info(f"🎯 K线结构止损计算: {side}方向, 入场{current_price:.2f}, 止损{stop_loss_price:.2f} (距离{stop_loss_ratio:.2f}%)")
            return stop_loss_price
            
        except Exception as e:
            logger.log_error("stop_loss_calculation", str(e))
            # 备用止损计算
            if side == 'long':
                return current_price * (1 - sl_config.min_stop_loss_ratio)
            else:
                return current_price * (1 + sl_config.min_stop_loss_ratio)

    def calculate_overall_stop_loss_take_profit(self, symbol: str, position_history: list, current_position: dict, current_price: float, price_data: dict) -> dict:
        """基于整体仓位计算止损止盈 - 集成配置管理"""
        sl_config = self.config.stop_loss
        tp_config = self.config.take_profit
        
        if not position_history or not current_position:
            # 没有历史记录或当前持仓，使用当前价格作为参考
            actual_side = current_position.get('side', 'long') if current_position else 'long'
            stop_loss = self.calculate_adaptive_stop_loss(symbol, actual_side, current_price, price_data)
            take_profit = self.calculate_intelligent_take_profit(symbol, actual_side, current_price, price_data, tp_config.min_risk_reward)
            return {
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'weighted_entry': current_price,
                'total_size': 0
            }
        
        # 修复：使用当前实际持仓方向，而不是历史记录的第一个方向
        actual_side = current_position['side']
        
        # 计算加权平均入场价格（只考虑同方向的持仓）
        same_side_positions = [pos for pos in position_history if pos.get('side') == actual_side]
        
        if not same_side_positions:
            # 如果没有同方向的历史记录，使用当前持仓
            weighted_entry = current_position['entry_price']
            total_size = current_position['size']
        else:
            # 计算同方向持仓的加权平均
            total_size = sum([pos['size'] for pos in same_side_positions])
            weighted_entry = sum([pos['entry_price'] * pos['size'] for pos in same_side_positions]) / total_size
        
        # 基于实际方向计算止损止盈
        if actual_side == 'long':
            # 多头：止损在下方，止盈在上方
            stop_loss = self.calculate_adaptive_stop_loss(symbol, 'long', weighted_entry, price_data)
            take_profit = self.calculate_intelligent_take_profit(symbol, 'long', weighted_entry, price_data, tp_config.min_risk_reward * 0.9)  # 整体仓位使用稍低的风险回报比
            
            # 双重验证：确保价格关系正确
            if stop_loss >= weighted_entry:
                logger.log_warning(f"⚠️ {self.get_base_currency(symbol)}: 多头止损价格异常，自动修正")
                stop_loss = weighted_entry * (1 - sl_config.min_stop_loss_ratio)
                
            if take_profit <= weighted_entry:
                logger.log_warning(f"⚠️ {self.get_base_currency(symbol)}: 多头止盈价格异常，自动修正")
                take_profit = weighted_entry * (1 + sl_config.min_stop_loss_ratio * tp_config.min_risk_reward)
                
        else:  # short
            # 空头：止损在上方，止盈在下方
            stop_loss = self.calculate_adaptive_stop_loss(symbol, 'short', weighted_entry, price_data)
            take_profit = self.calculate_intelligent_take_profit(symbol, 'short', weighted_entry, price_data, tp_config.min_risk_reward * 0.9)
            
            # 双重验证：确保价格关系正确
            if stop_loss <= weighted_entry:
                logger.log_warning(f"⚠️ {self.get_base_currency(symbol)}: 空头止损价格异常，自动修正")
                stop_loss = weighted_entry * (1 + sl_config.min_stop_loss_ratio)
                
            if take_profit >= weighted_entry:
                logger.log_warning(f"⚠️ {self.get_base_currency(symbol)}: 空头止盈价格异常，自动修正")
                take_profit = weighted_entry * (1 - sl_config.min_stop_loss_ratio * tp_config.min_risk_reward)
        
        logger.log_info(f"🎯 {self.get_base_currency(symbol)}: 整体仓位管理 - {actual_side}方向, 平均成本{weighted_entry:.2f}, 止损{stop_loss:.2f}, 止盈{take_profit:.2f}")
        
        return {
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'weighted_entry': weighted_entry,
            'total_size': total_size
        }

    def calculate_risk_reward_ratio(self, entry_price: float, stop_loss_price: float, take_profit_price: float, side: str) -> float:
        """计算风险回报比 - 集成配置管理"""
        try:
            if side == 'long':
                # 多头：风险是入场价到止损价的距离，回报是入场价到止盈价的距离
                risk = abs(entry_price - stop_loss_price)
                reward = abs(take_profit_price - entry_price)
            else:  # short
                # 空头：风险是止损价到入场价的距离，回报是入场价到止盈价的距离
                risk = abs(stop_loss_price - entry_price)
                reward = abs(entry_price - take_profit_price)
            
            # 避免除零错误
            if risk == 0:
                return 0
                
            risk_reward_ratio = reward / risk
            
            # 安全检查：盈亏比应该在合理范围内
            if risk_reward_ratio > 100:  # 异常高的盈亏比
                logger.log_warning(f"⚠️ 异常盈亏比: {risk_reward_ratio:.2f}, 可能价格计算有误")
                return 0
                
            return risk_reward_ratio
            
        except Exception as e:
            logger.log_error("risk_reward_calculation", f"盈亏比计算失败: {str(e)}")
            return 0

    def validate_price_relationship(self, entry_price: float, stop_loss_price: float, take_profit_price: float, side: str) -> bool:
        """验证价格关系的合理性 - 集成配置管理"""
        sl_config = self.config.stop_loss
        tp_config = self.config.take_profit
        
        try:
            if side == 'long':
                # 多头：止损价 < 入场价 < 止盈价
                if not (stop_loss_price < entry_price < take_profit_price):
                    logger.log_error("price_validation", 
                                   f"多头价格关系错误: 止损{stop_loss_price:.2f} < 入场{entry_price:.2f} < 止盈{take_profit_price:.2f}")
                    return False
            else:  # short
                # 空头：止盈价 < 入场价 < 止损价
                if not (take_profit_price < entry_price < stop_loss_price):
                    logger.log_error("price_validation", 
                                   f"空头价格关系错误: 止盈{take_profit_price:.2f} < 入场{entry_price:.2f} < 止损{stop_loss_price:.2f}")
                    return False
            
            # 检查价格是否过于接近（使用配置的最小止损比例）
            min_distance = sl_config.min_stop_loss_ratio * 0.5  # 允许的最小距离是止损比例的一半
            if abs(entry_price - stop_loss_price) / entry_price < min_distance:
                logger.log_warning("⚠️ 止损价格过于接近入场价格")
                return False
                
            if abs(take_profit_price - entry_price) / entry_price < min_distance:
                logger.log_warning("⚠️ 止盈价格过于接近入场价格")
                return False
                
            # 检查盈亏比是否合理
            if side == 'long':
                risk = entry_price - stop_loss_price
                reward = take_profit_price - entry_price
            else:
                risk = stop_loss_price - entry_price
                reward = entry_price - take_profit_price
                
            if risk <= 0:
                logger.log_warning("⚠️ 风险为0或负数")
                return False
                
            risk_reward_ratio = reward / risk
            min_acceptable_rr = tp_config.min_risk_reward * 0.5  # 最小可接受盈亏比是配置的一半
            if risk_reward_ratio < min_acceptable_rr:
                logger.log_warning(f"⚠️ 盈亏比过低: {risk_reward_ratio:.2f}")
                return False
                
            return True
            
        except Exception as e:
            logger.log_error("price_relationship_validation", str(e))
            return False

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """计算平均真实波幅(ATR)"""
        try:
            high_low = df['high'] - df['low']
            high_close = abs(df['high'] - df['close'].shift())
            low_close = abs(df['low'] - df['close'].shift())
            
            true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            atr = true_range.rolling(period).mean().iloc[-1]
            return atr
        except Exception as e:
            logger.log_error("atr_calculation", str(e))
            return df['close'].iloc[-1] * 0.02  # 默认2%作为ATR

    def get_multi_level_take_profit_config(self) -> Dict[str, Any]:
        """获取多级止盈配置"""
        return {
            'enable': self.config.multi_level_take_profit.enable,
            'levels': self.config.multi_level_take_profit.levels
        }

    def update_strategy_config(self, new_config: Dict[str, Any]) -> bool:
        """更新策略配置"""
        try:
            # 这里可以添加配置验证逻辑
            # 暂时直接保存
            return self.config_manager.update_symbol_config('global', new_config)
        except Exception as e:
            logger.log_error("update_strategy_config", f"更新策略配置失败: {e}")
            return False

    def get_base_currency(self, symbol: str) -> str:
        """
        将完整的交易品种名称转换为基础货币简称
        """
        try:
            # 使用 '/' 分割字符串，并取第一个部分
            base_currency = symbol.split('/')[0]
            return base_currency
        except Exception:
            # 如果分割失败，则返回原始字符串
            return symbol

# 全局实例
_sl_tp_strategy = None

def get_sl_tp_strategy(symbol_configs: Dict = None, config_file: str = "strategy_config.json") -> StopLossTakeProfitStrategy:
    """获取止盈止损策略实例"""
    global _sl_tp_strategy
    if _sl_tp_strategy is None and symbol_configs is not None:
        _sl_tp_strategy = StopLossTakeProfitStrategy(symbol_configs, config_file)
    return _sl_tp_strategy

def initialize_sl_tp_strategy(symbol_configs: Dict, config_file: str = "strategy_config.json"):
    """初始化止盈止损策略"""
    global _sl_tp_strategy
    _sl_tp_strategy = StopLossTakeProfitStrategy(symbol_configs, config_file)