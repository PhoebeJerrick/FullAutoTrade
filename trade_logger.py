import logging
import os
import sys
from datetime import datetime

class TradingLogger:
    def __init__(self, log_file='../Output/trading.log', log_level=logging.INFO):
        self.log_file = log_file
        self.setup_logging(log_level)
    
    def setup_logging(self, log_level):
        """Setup logging with rotation and better formatting"""
        # Create logs directory if it doesn't exist
        log_dir = os.path.dirname(self.log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # Clear previous basic config
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        
        # Create formatter - 修改格式
        formatter = logging.Formatter(
            '%(asctime)s-%(name)s-%(levelname)s-%(message)s',  # 移除空格和冒号，使用连字符分隔
            datefmt='%Y%m%d-%H%M%S'  # 修改日期格式为 YYYYMMDD-HHMMSS
        )
        
        # Setup logger - 修改记录器名称
        self.logger = logging.getLogger('TradeBot')  # 改为 TradeBot
        self.logger.setLevel(log_level)
        
        # File handler
        file_handler = logging.FileHandler(self.log_file)
        file_handler.setFormatter(formatter)
        
        # Console handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        
        # Add handlers
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
    
    def _format_message(self, message):
        """内部方法：获取当前品种并格式化消息"""
        try:
            # 尝试从 ds_perfect 模块获取 CURRENT_SYMBOL
            from ds_perfect import CURRENT_SYMBOL
            if CURRENT_SYMBOL:
                # 仅保留基础货币（如 BTC, ETH）作为日志前缀
                base_asset = CURRENT_SYMBOL.split('/')[0]
                return f"[{base_asset}] {message}"
        except (ImportError, AttributeError):
            # 模块未加载或变量不存在
            pass
        return message

    def log_signal(self, signal_data, price_data):
        """Log trading signals"""
        message = (
            f"SIGNAL: {signal_data['signal']} | "
            f"Confidence: {signal_data['confidence']} | "
            f"Price: ${price_data['price']:.2f} | "
            f"Reason: {signal_data.get('reason', 'N/A')}"
        )
        self.logger.info(self._format_message(message))

    def log_trade(self, order_id, side, amount, price, status, details="", success=True):
        """Log trade messages"""
        message = f"TRADE | ID: {order_id} | Side: {side} | Amount: {amount} | Price: {price} | Status: {status}"
        if details:
            message += f" | {details}"
        
        # 使用格式化方法
        message = self._format_message(message) 
        
        if success:
            self.logger.info(message)
        else:
            self.logger.error(message)
    
    def log_error(self, context, error):
        """Log error messages with context"""
        # 使用格式化方法
        self.logger.error(self._format_message(f"{context}: {error}")) 
    
    def log_warning(self, message):
        """Log warning messages"""
        # 使用格式化方法
        self.logger.warning(self._format_message(f"{message}")) 
    
    def log_info(self, message):
        """Log general info messages"""
        # 使用格式化方法
        self.logger.info(self._format_message(f"{message}"))
    
    def log_debug(self, message):
        """Log debug messages"""
        self.logger.debug(self._format_message(f"{message}"))

    def log_performance(self, metrics_dict):
        """Log performance metrics"""
        metrics_str = " | ".join([f"{k}: {v}" for k, v in metrics_dict.items()])
        self.logger.info(self._format_message(f"PERFORMANCE: {metrics_str}"))
    
    def log_health_check(self, status, details=""):
        """Log health check results"""
        if status:
            self.logger.info(self._format_message(f"HEALTH CHECK: PASSED | {details}"))
        else:
            self.logger.warning(self._format_message(f"HEALTH CHECK: FAILED | {details}"))

# Initialize logger
logger = TradingLogger()

# HOW TO USE:
# Replace print statements:
# OLD: print(f"Signal generated: {signal_data['signal']}")
# NEW: logger.log_signal(signal_data, price_data)

# OLD: print(f"Trade execution failed: {e}")
# NEW: logger.log_error("trade_execution", str(e))