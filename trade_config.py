import os
import time

# --- NEW: Multi-Symbol Configuration Structure ---
# 定义多交易品种的个性化配置，未指定的参数将使用默认值
MULTI_SYMBOL_CONFIGS = {
    # 默认/参考配置 (BTC)
    'BTC/USDT:USDT': {
        'leverage': int(os.getenv('BTC_LEVERAGE', 50)),
        'base_usdt_amount': float(os.getenv('BTC_BASE_USDT_AMOUNT', 100)),
        'max_position_ratio': 10,
    },
    # ETH 配置
    'ETH/USDT:USDT': {
        'leverage': int(os.getenv('ETH_LEVERAGE', 20)),
        'base_usdt_amount': float(os.getenv('ETH_BASE_USDT_AMOUNT', 80)),
        'max_position_ratio': 8,
    },
    # SOL 配置 (示例)
    'SOL/USDT:USDT': {
        'leverage': int(os.getenv('SOL_LEVERAGE', 30)),
        'base_usdt_amount': float(os.getenv('SOL_BASE_USDT_AMOUNT', 50)),
        'max_position_ratio': 5,
    },
    # LTC 配置 (示例)
    'LTC/USDT:USDT': {
        'leverage': int(os.getenv('LTC_LEVERAGE', 20)),
        'base_usdt_amount': float(os.getenv('LTC_BASE_USDT_AMOUNT', 40)),
        'max_position_ratio': 5,
    },
    # BCH 配置 (示例)
    'BCH/USDT:USDT': {
        'leverage': int(os.getenv('BCH_LEVERAGE', 30)),
        'base_usdt_amount': float(os.getenv('BCH_BASE_USDT_AMOUNT', 60)),
        'max_position_ratio': 7,
    },
}

class TradingConfig:
    """
    Dynamic configuration management for trading bot
    """
    def __init__(self, symbol: str, config_data: dict):
        # 1. 设置品种信息
        self.symbol = symbol
        current_config = self.get_symbol_config(self.symbol)
        
        # Trading parameters
        # 使用品种特定的杠杆，如果没有配置则使用环境变量或默认值
        self.leverage = config_data.get('leverage', int(os.getenv('LEVERAGE', 50)))
        self.timeframe = os.getenv('TIMEFRAME', '15m')
        self.test_mode = os.getenv('TEST_MODE', 'False').lower() == 'true'
        self.data_points = int(os.getenv('DATA_POINTS', 96))
        self.margin_mode = os.getenv('MARGIN_MODE', 'isolated')  # 默认为逐仓
        
        # Exchange settings
        self.exchange_name = 'okx'
        self.default_type = 'swap'
        
        # 🆕 添加缺失的配置属性
        self.config_check_interval = 300  # 5 minutes
        self.perf_log_interval = 600      # 10 minutes
        
        # Analysis periods
        self.analysis_periods = {
            'short_term': 20,
            'medium_term': 50,
            'long_term': 96
        }
        
        # Position management
        self.position_management = {
            'enable_intelligent_position': True,
            # 使用品种特定的基础投资额
            'base_usdt_amount': config_data.get('base_usdt_amount', 100.0),
            'high_confidence_multiplier': 1.5,
            'medium_confidence_multiplier': 1.0,
            'low_confidence_multiplier': 0.5,
            # 使用品种特定的最大仓位比例
            'max_position_ratio': config_data.get('max_position_ratio', 10),
            'trend_strength_multiplier': 1.2
        }
        
        # 🆕 新增风险管理和止盈止损配置
        self.risk_management = {
            # 止损配置
            'stop_loss': {
                'max_stop_loss_ratio': 0.40,  # 最大止损比例40%
                'kline_based_stop_loss': True,  # 基于K线结构设置止损
                'min_stop_loss_ratio': 0.02,   # 最小止损比例2%
            },
            # 多级止盈配置
            'profit_taking': {
                'enable_multilevel_take_profit': True,
                'levels': [
                    {
                        'profit_multiplier': 1.0,  # 盈利翻倍
                        'take_profit_ratio': 0.40,  # 止盈40%
                        'description': '第一目标：盈利100%时止盈40%'
                    },
                    {
                        'profit_multiplier': 2.0,  # 再翻一倍（总盈利200%）
                        'take_profit_ratio': 0.30,  # 止盈30%
                        'set_breakeven_stop': True,  # 剩余部分止损设置为开仓价
                        'description': '第二目标：总盈利200%时再止盈30%，剩余部分保本'
                    }
                ]
            },
            # 动态止损调整
            'dynamic_stop_loss': {
                'enable_trailing_stop': True,
                'trailing_activation_ratio': 0.50,  # 盈利50%后启动移动止损
                'trailing_distance_ratio': 0.20,    # 移动止损距离20%
            }
        }
        
        # API settings
        self.deepseek_base_url = "https://api.deepseek.com"
        self.sentiment_api_url = "https://service.cryptoracle.network/openapi/v2/endpoint"
        self.sentiment_api_key = "7ad48a56-8730-4238-a714-eebc30834e3e"
        
        # Trading limits
        self.max_retries = 3
        self.retry_delay = 2
        self.max_consecutive_errors = 5
        
        # Monitoring
        self.health_check_interval = 300  # 5 minutes
        self.max_signal_history = 100
        
        self._last_update = time.time()
    
    def should_reload(self):
        """Check if configuration should be reloaded from environment"""
        return time.time() - self._last_update > self.health_check_interval
    
    def reload(self):
        """Reload configuration from environment variables"""
        # 重新加载当前品种的配置
        current_config = self.get_symbol_config(self.symbol)
        
        # Trading parameters
        self.symbol = os.getenv('TRADING_SYMBOL', self.symbol)
        self.leverage = int(os.getenv('LEVERAGE', current_config.get('leverage', self.leverage)))
        self.timeframe = os.getenv('TIMEFRAME', self.timeframe)
        self.test_mode = os.getenv('TEST_MODE', str(self.test_mode)).lower() == 'true'
        self.data_points = int(os.getenv('DATA_POINTS', self.data_points))
        
        # Position management
        self.position_management['base_usdt_amount'] = float(
            os.getenv('BASE_USDT_AMOUNT', current_config.get('base_usdt_amount', self.position_management['base_usdt_amount']))
        )
        self.position_management['max_position_ratio'] = current_config.get('max_position_ratio', self.position_management['max_position_ratio'])

        # 🆕 新增仓位模式配置
        self.margin_mode = os.getenv('MARGIN_MODE', 'isolated')  # 默认为逐仓
        
        # 🆕 重新加载风险管理配置
        self.risk_management['stop_loss']['max_stop_loss_ratio'] = float(
            os.getenv('MAX_STOP_LOSS_RATIO', self.risk_management['stop_loss']['max_stop_loss_ratio'])
        )
        
        self._last_update = time.time()
        print("🔄 Configuration reloaded from environment variables")

    def update_contract_info(self, contract_size, min_amount):
        """Update contract information from exchange"""
        self.contract_size = contract_size
        self.min_amount = min_amount
    
    def get_position_config(self):
        """Get position management configuration"""
        return self.position_management
    
    def get_risk_config(self):
        """Get risk management configuration"""
        return self.risk_management
    
    def to_dict(self):
        """Convert configuration to dictionary for backward compatibility"""
        return {
            'symbol': self.symbol,
            'leverage': self.leverage,
            'timeframe': self.timeframe,
            'test_mode': self.test_mode,
            'data_points': self.data_points,
            'analysis_periods': self.analysis_periods,
            'position_management': self.position_management,
            'risk_management': self.risk_management,  # 🆕 添加风险管理配置
            'contract_size': getattr(self, 'contract_size', 0.01),
            'min_amount': getattr(self, 'min_amount', 0.01)
        }

    def get_symbol_config(self, symbol: str) -> dict:
        """
        [NEW METHOD] 获取特定交易品种的配置，未找到则返回 BTC 默认配置
        """
        # 使用 MULTI_SYMBOL_CONFIGS 字典
        return MULTI_SYMBOL_CONFIGS.get(symbol, MULTI_SYMBOL_CONFIGS.get('BTC/USDT:USDT', {}))

    def validate_config(self, symbol: str = None) -> Tuple[bool, List[str], List[str]]:
        """
        验证配置是否有效
        返回: (is_valid: bool, error_messages: list)
        """
        errors = []
        warnings = []

        # 1. 检查必需的环境变量
        required_env_vars = ['OKX_API_KEY', 'OKX_SECRET', 'OKX_PASSWORD']
        for var in required_env_vars:
            if not os.getenv(var):
                errors.append(f"缺少必需的环境变量: {var}")

        # 2. 检查 DeepSeek API 密钥
        if not os.getenv('DEEPSEEK_API_KEY'):
            errors.append("缺少 DeepSeek API 密钥 (DEEPSEEK_API_KEY)")

        # 3. 验证交易参数范围
        if self.leverage <= 0 or self.leverage > 100:
            errors.append(f"杠杆倍数必须在 1-100 之间，当前: {self.leverage}")
        
        if self.data_points <= 0:
            errors.append(f"数据点数必须大于0，当前: {self.data_points}")

        # 4. 验证仓位管理参数
        pos_config = self.position_management
        if pos_config['base_usdt_amount'] <= 0:
            errors.append("基础USDT金额必须大于0")
        
        if not (0 <= pos_config['max_position_ratio'] <= 100):
            errors.append("最大仓位比例必须在 0-100 之间")

        # 5. 验证风险管理参数
        risk_config = self.risk_management
        
        # 止损比例验证
        stop_loss = risk_config['stop_loss']
        if not (0.01 <= stop_loss['max_stop_loss_ratio'] <= 1.0):
            errors.append("最大止损比例必须在 1%-100% 之间")
        
        if not (0.01 <= stop_loss['min_stop_loss_ratio'] <= 0.5):
            errors.append("最小止损比例必须在 1%-50% 之间")

        # 多级止盈验证
        profit_taking = risk_config['profit_taking']
        if profit_taking['enable_multilevel_take_profit']:
            total_ratio = 0
            for i, level in enumerate(profit_taking['levels']):
                if not (0 < level['profit_multiplier'] <= 10):
                    warnings.append(f"止盈级别 {i+1} 的盈利倍数异常: {level['profit_multiplier']}")
                
                if not (0 < level['take_profit_ratio'] <= 1):
                    errors.append(f"止盈级别 {i+1} 的止盈比例必须在 0-1 之间")
                
                total_ratio += level['take_profit_ratio']
            
            if total_ratio > 1.0:
                warnings.append(f"总止盈比例超过100%: {total_ratio:.1%}")

        # 6. 验证动态止损
        trailing = risk_config['dynamic_stop_loss']
        if trailing['enable_trailing_stop']:
            if not (0 < trailing['trailing_activation_ratio'] <= 2.0):
                warnings.append(f"移动止损激活比例异常: {trailing['trailing_activation_ratio']}")
            
            if not (0 < trailing['trailing_distance_ratio'] <= 0.5):
                warnings.append(f"移动止损距离比例异常: {trailing['trailing_distance_ratio']}")

        # 7. 检查合约信息（如果已设置）
        if hasattr(self, 'contract_size'):
            if self.contract_size <= 0:
                errors.append("合约大小必须大于0")
        
        if hasattr(self, 'min_amount'):
            if self.min_amount <= 0:
                errors.append("最小交易量必须大于0")

        return len(errors) == 0, errors, warnings

    def get_config_summary(self) -> dict:
        """
        获取配置摘要（用于日志记录）
        """
        return {
            'symbol': self.symbol,
            'leverage': self.leverage,
            'timeframe': self.timeframe,
            'test_mode': self.test_mode,
            'base_usdt_amount': self.position_management['base_usdt_amount'],
            'max_stop_loss_ratio': self.risk_management['stop_loss']['max_stop_loss_ratio'],
            'enable_multilevel_take_profit': self.risk_management['profit_taking']['enable_multilevel_take_profit'],
            'contract_size': getattr(self, 'contract_size', 'Not set'),
            'min_amount': getattr(self, 'min_amount', 'Not set')
        }

# Create global instance
TRADE_CONFIG = TradingConfig()

# For example, TRADE_CONFIG['symbol'] becomes TRADE_CONFIG.symbol.