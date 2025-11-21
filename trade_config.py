import os
import time
import subprocess
import re
from typing import Tuple, List, Dict, Any

# --- 简单版本配置 ---
VERSION_CONFIG = {
    'version': '1.0.5',  # 基础版本号
    'auto_increment': True,  # 是否自动基于Git提交递增
    # 'git_commit_count_as_build': True,  # 使用Git提交次数作为构建号
}

# --- NEW: Multi-Symbol Configuration Structure ---
MULTI_SYMBOL_CONFIGS = {
    # 默认/参考配置 (BTC)
    'BTC/USDT:USDT': {
        'leverage': int(os.getenv('BTC_LEVERAGE', 50)),
        'base_usdt_amount': float(os.getenv('BTC_BASE_USDT_AMOUNT', 100)),
        'max_position_ratio': 10,
    },
    'UNI/USDT:USDT': {
        # 警告：请根据您的策略修改这些值
        'leverage': int(os.getenv('UNI_LEVERAGE', 20)),
        'base_usdt_amount': float(os.getenv('UNI_BASE_USDT_AMOUNT', 80)),
        'max_position_ratio': 7,
    },
    'OKB/USDT:USDT': {
        # 警告：请根据您的策略修改这些值
        'leverage': int(os.getenv('OKB_LEVERAGE', 15)),
        'base_usdt_amount': float(os.getenv('OKB_BASE_USDT_AMOUNT', 80)),
        'max_position_ratio': 8,
    },
    'HYPE/USDT:USDT': {
        # 警告：请根据您的策略修改这些值
        'leverage': int(os.getenv('HYPE_LEVERAGE', 10)),
        'base_usdt_amount': float(os.getenv('HYPE_BASE_USDT_AMOUNT', 30)),
        'max_position_ratio': 4,
    },    

    # ETH 配置
    'ETH/USDT:USDT': {
        'leverage': int(os.getenv('ETH_LEVERAGE', 20)),
        'base_usdt_amount': float(os.getenv('ETH_BASE_USDT_AMOUNT', 80)),
        'max_position_ratio': 8,
    },
    # BCH 配置 (示例)
    'BCH/USDT:USDT': {
        'leverage': int(os.getenv('BCH_LEVERAGE', 20)),
        'base_usdt_amount': float(os.getenv('BCH_BASE_USDT_AMOUNT', 60)),
        'max_position_ratio': 7,
    },
    'ASTER/USDT:USDT': {
        # 警告：请根据您的策略修改这些值
        'leverage': int(os.getenv('ASTER_LEVERAGE', 15)),
        'base_usdt_amount': float(os.getenv('ASTER_BASE_USDT_AMOUNT', 80)),
        'max_position_ratio': 5,
    },
    # LTC 配置 (示例)
    'LTC/USDT:USDT': {
        'leverage': int(os.getenv('LTC_LEVERAGE', 20)),
        'base_usdt_amount': float(os.getenv('LTC_BASE_USDT_AMOUNT', 60)),
        'max_position_ratio': 5,
    },
    # SOL 配置 (示例)
    'SOL/USDT:USDT': {
        'leverage': int(os.getenv('SOL_LEVERAGE', 20)),
        'base_usdt_amount': float(os.getenv('SOL_BASE_USDT_AMOUNT', 60)),
        'max_position_ratio': 5,
    },
    # DOGE 配置 (示例)
    'DOGE/USDT:USDT': {
        'leverage': int(os.getenv('DOGE_LEVERAGE', 15)),
        'base_usdt_amount': float(os.getenv('DOGE_BASE_USDT_AMOUNT', 60)),
        'max_position_ratio': 7,
    },
    # BNB 配置 (示例)
    'BNB/USDT:USDT': {
        'leverage': int(os.getenv('BNB_LEVERAGE', 15)),
        'base_usdt_amount': float(os.getenv('BNB_BASE_USDT_AMOUNT', 60)),
        'max_position_ratio': 7,
    },
    'TRX/USDT:USDT': {
        # 警告：请根据您的策略修改这些值
        'leverage': int(os.getenv('TRX_LEVERAGE', 20)),
        'base_usdt_amount': float(os.getenv('TRX_BASE_USDT_AMOUNT', 70)),
        'max_position_ratio': 5,
    },
    # DASH 配置 (示例)
    'DASH/USDT:USDT': {
        'leverage': int(os.getenv('DASH_LEVERAGE', 15)),
        'base_usdt_amount': float(os.getenv('DASH_BASE_USDT_AMOUNT', 60)),
        'max_position_ratio': 7,
    },
}

# 定义不同账号对应的交易品种列表
# 请确保这里的品种名称与 MULTI_SYMBOL_CONFIGS 中的键一致
ACCOUNT_SYMBOL_MAPPING = {
    # 主账号用于交易 BTC, ETH
    "okxMain": [
        'BTC/USDT:USDT',
        'UNI/USDT:USDT',
        'OKB/USDT:USDT',
        'HYPE/USDT:USDT',
    ],
    # 子账号用于交易 SOL, LTC, BCH
    "okxSub1": [
        'ETH/USDT:USDT',
        'BCH/USDT:USDT',
        'ASTER/USDT:USDT',
        'LTC/USDT:USDT',
    ],
    # 子账号用于交易 SOL, LTC, BCH
    "okxSub2": [
        'DOGE/USDT:USDT',
        'SOL/USDT:USDT',
        'BNB/USDT:USDT',
        'TRX/USDT:USDT',
    ],
    # 默认账号 (如果运行程序时未指定账号)
    "default": [
        'BTC/USDT:USDT', # 示例中只保留一个默认品种
        'DASH/USDT:USDT',
        'ASTER/USDT:USDT',
        'LTC/USDT:USDT',
    ],
}

ACCOUNT_ENV_SUFFIX = {
    "okxMain": "_1",      # 对应 .env 里的 OKX_API_KEY_1
    "okxSub1": "_2",      # 对应 .env 里的 OKX_API_KEY_2
    "OkxSub2": "_3",      # 对应 .env 里的 OKX_API_KEY_3
    "default": "",        # 对应 .env 里的 OKX_API_KEY (无后缀)
    # 以后增加账号，在这里加一行:
    # "okxNew": "_NEW",   # 对应 .env 里的 OKX_API_KEY_NEW
}

class TradingConfig:
    """Dynamic configuration management for trading bot"""
    
    # 🚀 关键修改点：将 config_data: dict 替换为 **kwargs: Any，以捕获所有传入的关键字参数
    def __init__(self, symbol: str, **kwargs: Any):
        # 1. 设置品种信息
        self.symbol = symbol
        
        # 🆕 修复点：将 kwargs（即 **config_dict 展开后的内容）作为当前配置数据
        current_config = kwargs 

        # Trading parameters
        self.leverage = current_config.get('leverage', int(os.getenv('LEVERAGE', 50)))
        self.base_usdt_amount = current_config.get('base_usdt_amount', float(os.getenv('BASE_USDT_AMOUNT', 100)))
        self.timeframe = os.getenv('TIMEFRAME', '15m')
        self.test_mode = os.getenv('TEST_MODE', 'False').lower() == 'true'
        self.data_points = int(os.getenv('DATA_POINTS', 96))
        self.margin_mode = os.getenv('MARGIN_MODE', 'isolated')

        # 🆕 --- 交易所合约规则 (将在 setup_exchange 中被动态填充) ---
        # 合约面值 (e.g., 1.0 for BTC)
        self.contract_size = 1.0
        # 最小下单量 (e.g., 0.01 for BTC, 1 for BCH)
        self.min_amount = 0.01 
        # 数量精度步长 (e.g., 0.01 for BTC, 1 for BCH)
        self.amount_precision_step = 0.01 
        # 价格精度步长 (e.g., 0.1 for BTC)
        self.price_precision_step = 0.1
        # 是否只支持整数张合约
        self.requires_integer = False

        # Exchange settings
        self.exchange_name = 'okx'
        self.default_type = 'swap'
        
        # 添加缺失的配置属性
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
            'first_position_min_ratio': current_config.get('first_position_min_ratio', 0.05),  # 头仓最小比例，默认5%
            'add_position_max_ratio': 1.0,     # 加仓最大比例（相对于头仓，默认100%，即不超过头仓）
            'add_position_min_ratio': 0.2,      # 加仓最小比例（相对于头仓，默认20%）
            'base_usdt_amount': current_config.get('base_usdt_amount', 100.0),
            'high_confidence_multiplier': 1.5,
            'medium_confidence_multiplier': 1.0,
            'low_confidence_multiplier': 0.5,
            'max_position_ratio': current_config.get('max_position_ratio', 10),
            'trend_strength_multiplier': 1.2,
            "enable_scaling_in": True,  # 是否允许加仓
            "max_scaling_times": 3,     # 最大加仓次数
            "scaling_multiplier": 0.5,  # 每次加仓的仓位乘数（相对于首次开仓）
            "min_interval_minutes": 30  # 加仓最小时间间隔（分钟）
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
        self.health_check_interval = 300
        self.max_signal_history = 100
        
        # 🆕 简单版本控制
        self._version_info = self._get_version_info()
        
        self._last_update = time.time()
    
    # 🆕 简单版本控制方法
    def _get_git_commit_count(self) -> int:
        """获取Git提交次数"""
        try:
            result = subprocess.run(
                ['git', 'rev-list', '--count', 'HEAD'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return int(result.stdout.strip())
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, ValueError):
            pass
        return 0
    
    def _get_git_short_hash(self) -> str:
        """获取Git短哈希"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', '--short', 'HEAD'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass
        return "unknown"
    
    def _get_git_branch(self) -> str:
        """获取当前Git分支"""
        try:
            result = subprocess.run(
                ['git', 'branch', '--show-current'],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            pass
        return "unknown"
    
    def _get_version_info(self) -> Dict[str, Any]:
        """获取版本信息"""
        base_version = VERSION_CONFIG['version']
        
        if VERSION_CONFIG.get('auto_increment') and VERSION_CONFIG.get('git_commit_count_as_build'):
            commit_count = self._get_git_commit_count()
            short_hash = self._get_git_short_hash()
            branch = self._get_git_branch()
            
            # 格式: 1.0.1+build.15.gabc1234 (main)
            full_version = f"{base_version}+build.{commit_count}.g{short_hash} ({branch})"
        else:
            full_version = base_version
            commit_count = 0
            short_hash = "unknown"
            branch = "unknown"
        
        return {
            'base_version': base_version,
            'full_version': full_version,
            'commit_count': commit_count,
            'commit_hash': short_hash,
            'branch': branch,
            'build_time': time.strftime("%Y-%m-%d %H:%M:%S")
        }
    
    def get_version(self) -> str:
        """获取完整版本号"""
        return self._version_info['full_version']
    
    def get_version_details(self) -> Dict[str, Any]:
        """获取详细版本信息"""
        return self._version_info.copy()
    
    def check_for_updates(self) -> Dict[str, Any]:
        """检查是否有新版本（基于Git）"""
        try:
            # 获取远程更新
            subprocess.run(['git', 'fetch'], capture_output=True, timeout=10)
            
            # 比较本地和远程
            result = subprocess.run(
                ['git', 'rev-list', '--count', 'HEAD..origin/main'],
                capture_output=True, text=True, timeout=5
            )
            
            behind_count = 0
            if result.returncode == 0 and result.stdout.strip():
                behind_count = int(result.stdout.strip())
            
            return {
                'behind_remote': behind_count,
                'update_available': behind_count > 0,
                'current_commit': self._get_git_short_hash(),
                'message': f"落后远程 {behind_count} 个提交" if behind_count > 0 else "已是最新版本"
            }
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, ValueError):
            return {
                'behind_remote': 0,
                'update_available': False,
                'current_commit': self._get_git_short_hash(),
                'message': "检查更新失败"
            }

    def should_reload(self):
        """Check if configuration should be reloaded from environment"""
        return time.time() - self._last_update > self.health_check_interval
    
    def reload(self):
        """Reload configuration from environment variables"""
        # 重新获取当前品种配置
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
        self.position_management['max_position_ratio'] = current_config.get(
            'max_position_ratio', self.position_management['max_position_ratio']
        )

        self.margin_mode = os.getenv('MARGIN_MODE', 'isolated')
        
        self._last_update = time.time()
        print("🔄 Configuration reloaded from environment variables")

    def update_exchange_rules(self, contract_size: float, min_amount: float, amount_step: float, price_step: float, requires_integer: bool):
        """
        Update all contract and precision information from exchange market data.
        这是连接交易所获取数据和交易逻辑的关键步骤。
        """
        self.contract_size = contract_size
        self.min_amount = min_amount
        self.amount_precision_step = amount_step  # 🆕 修正点：更新数量精度步长
        self.price_precision_step = price_step    # 🆕 修正点：更新价格精度步长
        self.requires_integer = requires_integer  # 🆕 修正点：更新是否为整数合约

    def get_position_config(self):
        """Get position management configuration"""
        return self.position_management

    def to_dict(self):
        """Convert configuration to dictionary for backward compatibility"""
        config_dict = {
            'symbol': self.symbol,
            'leverage': self.leverage,
            'timeframe': self.timeframe,
            'test_mode': self.test_mode,
            'data_points': self.data_points,
            'analysis_periods': self.analysis_periods,
            'position_management': self.position_management,
            'contract_size': getattr(self, 'contract_size', 0.01),
            'min_amount': getattr(self, 'min_amount', 0.01),
            'margin_mode': getattr(self, 'margin_mode', 'isolated'),
            'version': self.get_version()  # 🆕 包含版本信息
        }
        return config_dict

    def get_symbol_config(self, symbol: str) -> dict:
        """获取特定交易品种的配置，未找到则返回 BTC 默认配置"""
        return MULTI_SYMBOL_CONFIGS.get(symbol, MULTI_SYMBOL_CONFIGS.get('BTC/USDT:USDT', {}))

    def validate_config(self, symbol: str = None) -> Tuple[bool, List[str], List[str]]:
        """验证配置是否有效"""
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

        # 5. 检查合约信息（如果已设置）
        if hasattr(self, 'contract_size'):
            if self.contract_size <= 0:
                errors.append("合约大小必须大于0")
        
        if hasattr(self, 'min_amount'):
            if self.min_amount <= 0:
                errors.append("最小交易量必须大于0")

        return len(errors) == 0, errors, warnings

    def get_config_summary(self) -> dict:
        """获取配置摘要（用于日志记录）"""
        return {
            'symbol': self.symbol,
            'leverage': self.leverage,
            'timeframe': self.timeframe,
            'test_mode': self.test_mode,
            'base_usdt_amount': self.position_management['base_usdt_amount'],
            'contract_size': getattr(self, 'contract_size', 'Not set'),
            'min_amount': getattr(self, 'min_amount', 'Not set'),
            'version': self.get_version()  # 🆕 包含版本信息
        }

# 简单的版本工具函数
def print_version_banner(config: 'TradingConfig'): # 接受一个 TradingConfig 实例
    """打印版本横幅"""
    # 直接使用传入的 config 实例
    version_info = config.get_version_details() 
    print("=" * 50)
    print(f"🚀 Trading Bot {version_info['full_version']}")
    print(f"📅 Build Time: {version_info['build_time']}")
    print(f"🌿 Branch: {version_info['branch']}")
    print("=" * 50)