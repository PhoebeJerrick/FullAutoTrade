# Strategy/config_manager.py
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
import json
import os

@dataclass
class StopLossConfig:
    """止损配置"""
    min_stop_loss_ratio: float = 0.02
    max_stop_loss_ratio: float = 0.30
    kline_based_stop_loss: bool = True
    atr_multiplier: float = 1.5
    enable_trailing_stop: bool = True
    trailing_activation_ratio: float = 0.03
    trailing_distance_ratio: float = 0.015

@dataclass
class TakeProfitConfig:
    """止盈配置"""
    min_risk_reward: float = 1.2
    max_risk_reward: float = 3.0
    atr_multiplier: float = 1.5 
    trend_strength_multipliers: Dict[str, float] = field(default_factory=lambda: {
        'STRONG_UPTREND': 1.5,
        'UPTREND': 1.2,
        'CONSOLIDATION': 1.0,
        'DOWNTREND': 1.2,
        'STRONG_DOWNTREND': 1.5
    })

@dataclass
class MultiLevelTakeProfitConfig:
    """多级止盈配置"""
    enable: bool = True
    levels: list = field(default_factory=lambda: [
        {
            'profit_multiplier': 1.5,
            'take_profit_ratio': 0.3,
            'set_breakeven_stop': True,
            'description': '第一级止盈'
        },
        {
            'profit_multiplier': 2.0,
            'take_profit_ratio': 0.4,
            'set_breakeven_stop': True,
            'description': '第二级止盈'
        },
        {
            'profit_multiplier': 3.0,
            'take_profit_ratio': 0.3,
            'set_breakeven_stop': False,
            'description': '第三级止盈'
        }
    ])

@dataclass
class StrategyConfig:
    """策略总配置"""
    stop_loss: StopLossConfig
    take_profit: TakeProfitConfig
    multi_level_take_profit: MultiLevelTakeProfitConfig
    symbol_specific_config: Dict[str, Any] = field(default_factory=dict)
    default_atr_period: int = 14  # 这是一个全局配置项

class ConfigManager:
    """
    配置管理器
    """
    
    def __init__(self, config_file: str = "st_config.json"):
        self.config_file = config_file
        # 初始化时先建立默认配置
        self.current_config = self._create_default_config()
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
                # 1. 读取 JSON 文件
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    # 如果文件里有 // 注释，json.load 会失败。
                    # 生产环境建议去除 JSON 注释
                    config_data = json.load(f)
                
                # 2. 解析结构
                # JSON 结构是 {"global": {...}, "symbol_specific_config": {...}}
                # 我们需要先提取 global 这一层
                global_section = config_data.get("global", {})
                symbol_section = config_data.get("symbol_specific_config", {})

                # 3. 构建配置对象
                # 注意：这里一定要从 global_section 取数据，而不是 config_data
                self.current_config = StrategyConfig(
                    stop_loss=StopLossConfig(**global_section.get('stop_loss', {})),
                    take_profit=TakeProfitConfig(**global_section.get('take_profit', {})),
                    multi_level_take_profit=MultiLevelTakeProfitConfig(**global_section.get('multi_level_take_profit', {})),
                    symbol_specific_config=symbol_section,
                    default_atr_period=global_section.get('default_atr_period', 14)
                )
                
                print(f"✅ 策略配置已从 {self.config_file} 加载")
                return True
            else:
                print(f"ℹ️ 配置文件 {self.config_file} 不存在，使用默认配置")
                self.save_config()
                return True
        except Exception as e:
            import traceback
            print(f"❌ 加载策略配置失败: {e}")
            print(traceback.format_exc()) # 打印详细错误堆栈
            return False
    
    def save_config(self) -> bool:
        """保存配置到文件"""
        try:
            # 保存时需要还原成 JSON 的嵌套结构 {"global": ..., "symbol...": ...}
            config_dict = self._config_to_dict(self.current_config)
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            print(f"✅ 策略配置已保存到 {self.config_file}")
            return True
        except Exception as e:
            print(f"❌ 保存策略配置失败: {e}")
            return False
        
    def update_config(self, new_config: StrategyConfig) -> bool:
        self.current_config = new_config
        return self.save_config()
    
    def _config_to_dict(self, config: StrategyConfig) -> Dict[str, Any]:
        """将配置对象转换为符合 JSON 结构的字典"""
        return {
            "global": {
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
                    'atr_multiplier': config.take_profit.atr_multiplier,
                    'trend_strength_multipliers': config.take_profit.trend_strength_multipliers
                },
                'multi_level_take_profit': {
                    'enable': config.multi_level_take_profit.enable,
                    'levels': config.multi_level_take_profit.levels
                },
                'default_atr_period': config.default_atr_period
            },
            "symbol_specific_config": config.symbol_specific_config
        }
    
    def get_symbol_config(self, symbol: str) -> Dict[str, Any]:
        """获取品种特定配置"""
        base_symbol = self._get_base_symbol(symbol)
        # 返回特定配置，如果没有则返回空字典
        return self.current_config.symbol_specific_config.get(base_symbol, {})
    
    def update_symbol_config(self, symbol: str, config: Dict[str, Any]) -> bool:
        """更新品种特定配置"""
        base_symbol = self._get_base_symbol(symbol)
        # 更新内存中的配置
        self.current_config.symbol_specific_config[base_symbol] = config
        # 保存到文件
        return self.save_config()
    
    def _get_base_symbol(self, symbol: str) -> str:
        """提取基础交易品种 (例如 'BTC/USDT' -> 'BTC')"""
        return symbol.split('/')[0] if '/' in symbol else symbol

# ... (Getters definitions remain the same)

    def print_current_config(self):
        """打印当前配置"""
        print("\n📊 当前策略配置:")
        config_dict = self._config_to_dict(self.current_config)
        print(json.dumps(config_dict, indent=2, ensure_ascii=False))

# 全局配置管理器实例
_config_manager = None

def get_config_manager(config_file: str = "st_config.json") -> ConfigManager:
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(config_file)
    return _config_manager