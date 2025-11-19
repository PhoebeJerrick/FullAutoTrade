# Strategy/config_manager.py
from typing import Dict, Any, Optional
from dataclasses import dataclass
import json
import os

@dataclass
class StopLossConfig:
    """止损配置"""
    min_stop_loss_ratio: float = 0.02  # 最小止损比例 2%
    max_stop_loss_ratio: float = 0.40  # 最大止损比例 40%
    kline_based_stop_loss: bool = True  # 是否基于K线结构止损
    atr_multiplier: float = 1.5  # ATR倍数
    enable_trailing_stop: bool = True  # 是否启用移动止损
    trailing_activation_ratio: float = 0.03  # 移动止损激活比例 3%
    trailing_distance_ratio: float = 0.015  # 移动止损距离 1.5%

@dataclass
class TakeProfitConfig:
    """止盈配置"""
    min_risk_reward: float = 1.2  # 最小风险回报比
    max_risk_reward: float = 3.0  # 最大风险回报比
    enable_multilevel_take_profit: bool = True  # 是否启用多级止盈
    trend_strength_multipliers: Dict[str, float] = None  # 趋势强度乘数
    
    def __post_init__(self):
        if self.trend_strength_multipliers is None:
            self.trend_strength_multipliers = {
                'STRONG_UPTREND': 1.5,
                'UPTREND': 1.2,
                'CONSOLIDATION': 1.0,
                'DOWNTREND': 1.2,
                'STRONG_DOWNTREND': 1.5
            }

@dataclass
class MultiLevelTakeProfitConfig:
    """多级止盈配置"""
    enable: bool = True
    levels: list = None
    
    def __post_init__(self):
        if self.levels is None:
            self.levels = [
                {
                    'profit_multiplier': 1.5,  # 盈利倍数
                    'take_profit_ratio': 0.3,  # 平仓比例 30%
                    'set_breakeven_stop': True,
                    'description': '第一级止盈 - 30%仓位，设置保本止损'
                },
                {
                    'profit_multiplier': 2.0,
                    'take_profit_ratio': 0.4,  # 平仓比例 40%
                    'set_breakeven_stop': True,
                    'description': '第二级止盈 - 40%仓位，移动止损'
                },
                {
                    'profit_multiplier': 3.0,
                    'take_profit_ratio': 0.3,  # 平仓比例 30%
                    'set_breakeven_stop': False,
                    'description': '第三级止盈 - 剩余30%仓位，让利润奔跑'
                }
            ]

@dataclass
class StrategyConfig:
    """策略配置"""
    stop_loss: StopLossConfig
    take_profit: TakeProfitConfig
    multi_level_take_profit: MultiLevelTakeProfitConfig
    symbol_specific_config: Dict[str, Any] = None
    
    def __post_init__(self):
        if self.symbol_specific_config is None:
            self.symbol_specific_config = {}

class ConfigManager:
    """
    配置管理器
    负责加载、保存和管理止损止盈策略配置
    """
    
    def __init__(self, config_file: str = "strategy_config.json"):
        self.config_file = config_file
        self.default_config = self._create_default_config()
        self.current_config = self.default_config
        self.load_config()
    
    def _create_default_config(self) -> StrategyConfig:
        """创建默认配置"""
        return StrategyConfig(
            stop_loss=StopLossConfig(),
            take_profit=TakeProfitConfig(),
            multi_level_take_profit=MultiLevelTakeProfitConfig()
        )
    
    def load_config(self) -> bool:
        """从文件加载配置"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                self.current_config = self._dict_to_config(config_data)
                print(f"✅ 策略配置已从 {self.config_file} 加载")
                return True
            else:
                print(f"ℹ️ 配置文件 {self.config_file} 不存在，使用默认配置")
                self.save_config()  # 创建默认配置文件
                return True
        except Exception as e:
            print(f"❌ 加载策略配置失败: {e}，使用默认配置")
            return False
    
    def save_config(self) -> bool:
        """保存配置到文件"""
        try:
            config_dict = self._config_to_dict(self.current_config)
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            print(f"✅ 策略配置已保存到 {self.config_file}")
            return True
        except Exception as e:
            print(f"❌ 保存策略配置失败: {e}")
            return False
    
    def _config_to_dict(self, config: StrategyConfig) -> Dict[str, Any]:
        """将配置对象转换为字典"""
        return {
            'stop_loss': {
                'min_stop_loss_ratio': config.stop_loss.min_stop_loss_ratio,
                'max_stop_loss_ratio': config.stop_loss.max_stop_loss_ratio,
                'kline_based_stop_loss': config.stop_loss.kline_based_stop_loss,
                'atr_multiplier': config.stop_loss.atr_multiplier,
                'enable_trailing_stop': config.stop_loss.enable_trailing_stop,
                'trailing_activation_ratio': config.stop_loss.trailing_activation_ratio,
                'trailing_distance_ratio': config.stop_loss.trailing_distance_ratio
            },
            'take_profit': {
                'min_risk_reward': config.take_profit.min_risk_reward,
                'max_risk_reward': config.take_profit.max_risk_reward,
                'enable_multilevel_take_profit': config.take_profit.enable_multilevel_take_profit,
                'trend_strength_multipliers': config.take_profit.trend_strength_multipliers
            },
            'multi_level_take_profit': {
                'enable': config.multi_level_take_profit.enable,
                'levels': config.multi_level_take_profit.levels
            },
            'symbol_specific_config': config.symbol_specific_config
        }
    
    def _dict_to_config(self, config_dict: Dict[str, Any]) -> StrategyConfig:
        """将字典转换为配置对象"""
        return StrategyConfig(
            stop_loss=StopLossConfig(**config_dict.get('stop_loss', {})),
            take_profit=TakeProfitConfig(**config_dict.get('take_profit', {})),
            multi_level_take_profit=MultiLevelTakeProfitConfig(**config_dict.get('multi_level_take_profit', {})),
            symbol_specific_config=config_dict.get('symbol_specific_config', {})
        )
    
    def update_config(self, new_config: StrategyConfig) -> bool:
        """更新配置"""
        self.current_config = new_config
        return self.save_config()
    
    def get_symbol_config(self, symbol: str) -> Dict[str, Any]:
        """获取品种特定配置"""
        base_symbol = self._get_base_symbol(symbol)
        return self.current_config.symbol_specific_config.get(base_symbol, {})
    
    def update_symbol_config(self, symbol: str, config: Dict[str, Any]) -> bool:
        """更新品种特定配置"""
        base_symbol = self._get_base_symbol(symbol)
        self.current_config.symbol_specific_config[base_symbol] = config
        return self.save_config()
    
    def _get_base_symbol(self, symbol: str) -> str:
        """提取基础交易品种"""
        return symbol.split('/')[0] if '/' in symbol else symbol
    
    def print_current_config(self):
        """打印当前配置"""
        print("\n📊 当前策略配置:")
        config_dict = self._config_to_dict(self.current_config)
        print(json.dumps(config_dict, indent=2, ensure_ascii=False))

# 全局配置管理器实例
_config_manager = None

def get_config_manager(config_file: str = "strategy_config.json") -> ConfigManager:
    """获取配置管理器实例"""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(config_file)
    return _config_manager