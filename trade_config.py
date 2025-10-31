import os
import time

class TradingConfig:
    """
    Dynamic configuration management for trading bot
    """
    def __init__(self):
        # Trading parameters
        self.symbol = os.getenv('TRADING_SYMBOL', 'BTC/USDT:USDT')
        self.leverage = int(os.getenv('LEVERAGE', 10))
        self.timeframe = os.getenv('TIMEFRAME', '15m')
        self.test_mode = os.getenv('TEST_MODE', 'False').lower() == 'true'
        self.data_points = int(os.getenv('DATA_POINTS', 96))
        
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
            'base_usdt_amount': float(os.getenv('BASE_USDT_AMOUNT', 100)),
            'high_confidence_multiplier': 1.5,
            'medium_confidence_multiplier': 1.0,
            'low_confidence_multiplier': 0.5,
            'max_position_ratio': 10,
            'trend_strength_multiplier': 1.2
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
        # Trading parameters
        self.symbol = os.getenv('TRADING_SYMBOL', self.symbol)
        self.leverage = int(os.getenv('LEVERAGE', self.leverage))
        self.timeframe = os.getenv('TIMEFRAME', self.timeframe)
        self.test_mode = os.getenv('TEST_MODE', str(self.test_mode)).lower() == 'true'
        self.data_points = int(os.getenv('DATA_POINTS', self.data_points))
        
        # Position management
        self.position_management['base_usdt_amount'] = float(
            os.getenv('BASE_USDT_AMOUNT', self.position_management['base_usdt_amount'])
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
            'contract_size': getattr(self, 'contract_size', 0.01),
            'min_amount': getattr(self, 'min_amount', 0.01)
        }

# Create global instance
TRADE_CONFIG = TradingConfig()

# For example, TRADE_CONFIG['symbol'] becomes TRADE_CONFIG.symbol.