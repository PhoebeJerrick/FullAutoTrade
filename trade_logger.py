import logging
import os
import sys
from datetime import datetime
from cmd_config import CURRENT_ACCOUNT

class TradingLogger:
    def __init__(self, log_level=logging.INFO):
        self.current_account = CURRENT_ACCOUNT
        
        # 🆕 新增：用于存储当前交易品种的上下文变量
        self.context_symbol = None
        
        # 生成日志文件路径
        self.log_file = f'../Output/{self.current_account}/{self.current_account}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
        self.setup_logging(log_level)

    def setup_logging(self, log_level):
        """Setup logging with rotation and better formatting"""
        log_dir = os.path.dirname(self.log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        
        formatter = logging.Formatter(
            '%(asctime)s-%(name)s-%(levelname)s-%(message)s',
            datefmt='%Y%m%d-%H%M%S'
        )
        
        self.logger = logging.getLogger('TradeBot')
        self.logger.setLevel(log_level)
        
        file_handler = logging.FileHandler(self.log_file)
        file_handler.setFormatter(formatter)
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
    
    # 🆕 新增：主动设置当前上下文的方法
    def bind_symbol(self, symbol: str):
        """绑定当前交易品种到日志上下文"""
        self.context_symbol = symbol

    def _format_message(self, message):
        """内部方法：获取当前品种并格式化消息"""
        # 🆕 修改：不再反向导入，而是使用内部状态
        if self.context_symbol:
            try:
                # 仅保留基础货币（如 BTC, ETH）作为日志前缀
                # 处理 'BTC/USDT:USDT' -> 'BTC'
                base_asset = self.context_symbol.split('/')[0]
                return f"[{base_asset}] {message}"
            except Exception:
                return f"[{self.context_symbol}] {message}"
        
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
        
        message = self._format_message(message) 
        
        if success:
            self.logger.info(message)
        else:
            self.logger.error(message)
    
    def log_error(self, context, error):
        self.logger.error(self._format_message(f"{context}: {error}")) 
    
    def log_warning(self, message):
        self.logger.warning(self._format_message(f"{message}")) 
    
    def log_info(self, message):
        self.logger.info(self._format_message(f"{message}"))
    
    def log_debug(self, message):
        self.logger.debug(self._format_message(f"{message}"))

    def log_performance(self, metrics_dict):
        metrics_str = " | ".join([f"{k}: {v}" for k, v in metrics_dict.items()])
        self.logger.info(self._format_message(f"PERFORMANCE: {metrics_str}"))
    
    def log_health_check(self, status, details=""):
        if status:
            self.logger.info(self._format_message(f"HEALTH CHECK: PASSED | {details}"))
        else:
            self.logger.warning(self._format_message(f"HEALTH CHECK: FAILED | {details}"))

#logger 实例创建
logger = TradingLogger()
# HOW TO USE:
# Replace print statements:
# OLD: print(f"Signal generated: {signal_data['signal']}")
# NEW: logger.log_signal(signal_data, price_data)

# OLD: print(f"Trade execution failed: {e}")
# NEW: logger.log_error("trade_execution", str(e))