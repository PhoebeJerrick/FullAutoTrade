import logging
import os
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
    
    def log_signal(self, signal_data, price_data):
        """Log trading signals"""
        self.logger.info(
            f"SIGNAL: {signal_data['signal']} | "
            f"Confidence: {signal_data['confidence']} | "
            f"Price: ${price_data['price']:.2f} | "
            f"Reason: {signal_data.get('reason', 'N/A')}"
        )
    
    def log_trade(self, action, size, price, success=True, details=""):
        """Log trade executions"""
        status = "SUCCESS" if success else "FAILED"
        message = f"TRADE: {action} {size} contracts @ ${price:.2f} - {status}"
        if details:
            message += f" | {details}"
        
        if success:
            self.logger.info(message)
        else:
            self.logger.error(message)
    
    def log_error(self, context, error):
        """Log error messages with context"""
        self.logger.error(f"{context}: {error}")
    
    def log_warning(self, message):
        """Log warning messages"""
        self.logger.warning(f"{message}")
    
    def log_info(self, message):
        """Log general info messages"""
        self.logger.info(f"{message}")
    
    def log_debug(self, message):
        """Log debug messages"""
        self.logger.debug(f"{message}")
    
    def log_performance(self, metrics_dict):
        """Log performance metrics"""
        metrics_str = " | ".join([f"{k}: {v}" for k, v in metrics_dict.items()])
        self.logger.info(f"PERFORMANCE: {metrics_str}")
    
    def log_health_check(self, status, details=""):
        """Log health check results"""
        if status:
            self.logger.info(f"HEALTH CHECK: PASSED | {details}")
        else:
            self.logger.warning(f"HEALTH CHECK: FAILED | {details}")

# Initialize logger
logger = TradingLogger()

# HOW TO USE:
# Replace print statements:
# OLD: print(f"Signal generated: {signal_data['signal']}")
# NEW: logger.log_signal(signal_data, price_data)

# OLD: print(f"Trade execution failed: {e}")
# NEW: logger.log_error("trade_execution", str(e))