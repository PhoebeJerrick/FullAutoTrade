import os
import time
from functools import wraps
import schedule
from openai import OpenAI
import ccxt
import pandas as pd
import re
from dotenv import load_dotenv
import json
import requests
from datetime import datetime, timedelta

# Trading parameter configuration - combining advantages of both versions
from trade_config import TRADE_CONFIG

# Global logger
from trade_logger import logger

# Use relative path
env_path = '../ExApiConfig/ExApiConfig.env'  # .env file in config folder of parent directory
logger.log_info(f"📁Add config file: {env_path}")
load_dotenv(dotenv_path=env_path)

# Initialize DeepSeek client with error handling
deepseek_client = None

def get_deepseek_client():
    global deepseek_client
    if deepseek_client is None:
        try:
            api_key = os.getenv('DEEPSEEK_API_KEY')
            if not api_key:
                raise ValueError("DEEPSEEK_API_KEY environment variable is not set")
            
            deepseek_client = OpenAI(
                api_key=api_key,
                base_url=TRADE_CONFIG.deepseek_base_url
            )
            logger.log_info("DeepSeek client initialized successfully")
        except Exception as e:
            logger.log_error("deepseek_client_init", str(e))
            raise
    return deepseek_client


# 添加账号参数支持
if len(sys.argv) > 1:
    account = sys.argv[1]
    logger.log_info(f"🎯 使用交易账号: {account}")
else:
    account = "default"
    logger.log_info("🎯 使用默认交易账号")

# 在全局变量中记录当前账号
CURRENT_ACCOUNT = account

# 根据账号选择对应的环境变量
def get_account_config(account_name):
    """根据账号名称获取对应的配置"""
    if account_name == "account1":
        return {
            'api_key': os.getenv('OKX_API_KEY_1') or os.getenv('OKX_API_KEY'),
            'secret': os.getenv('OKX_SECRET_1') or os.getenv('OKX_SECRET'),
            'password': os.getenv('OKX_PASSWORD_1') or os.getenv('OKX_PASSWORD')
        }
    elif account_name == "account2":
        return {
            'api_key': os.getenv('OKX_API_KEY_2'),
            'secret': os.getenv('OKX_SECRET_2'),
            'password': os.getenv('OKX_PASSWORD_2')
        }
    else:  # default
        return {
            'api_key': os.getenv('OKX_API_KEY'),
            'secret': os.getenv('OKX_SECRET'),
            'password': os.getenv('OKX_PASSWORD')
        }

# 获取当前账号配置
account_config = get_account_config(account)
print(f"🔑 账号配置加载: API_KEY={account_config['api_key'][:10]}...")

# 修改订单标签函数，包含账号信息
def create_order_tag():
    """创建包含账号信息的订单标签"""
    base_tag = '60bb4a8d3416BCDE'
    return f"{base_tag}_{CURRENT_ACCOUNT}"

# 初始化交易所 - 使用动态配置
exchange = ccxt.okx({
    'options': {
        'defaultType': 'swap',
    },
    'apiKey': account_config['api_key'],
    'secret': account_config['secret'],
    'password': account_config['password'],
})

def check_existing_positions():
    # Check existing positions and return whether there are isolated positions and the information of isolated positions.
    logger.log_info("🔍 Checking existing position mode..")
    positions = exchange.fetch_positions([TRADE_CONFIG.symbol])

    has_isolated_position = False
    isolated_position_info = None

    for pos in positions:
        if pos['symbol'] == TRADE_CONFIG.symbol:
            contracts = float(pos.get('contracts', 0))
            mode = pos.get('mgnMode')

            if contracts > 0 and mode == 'isolated':
                has_isolated_position = True
                isolated_position_info = {
                    'side': pos.get('side'),
                    'size': contracts,
                    'entry_price': pos.get('entryPrice'),
                    'mode': mode
                }
                break

    return has_isolated_position, isolated_position_info

def setup_exchange():
    """Intelligent exchange setup"""
    try:
        # Get contract specifications
        logger.log_info("🔍 Getting BTC contract specifications...")
        markets = exchange.load_markets()
        btc_market = markets[TRADE_CONFIG.symbol]
        
        TRADE_CONFIG.contract_size = float(btc_market['contractSize'])
        TRADE_CONFIG.min_amount = btc_market['limits']['amount']['min']
        
        logger.log_info(f"✅ Contract: 1 contract = {TRADE_CONFIG.contract_size} BTC")
        logger.log_info(f"📏 Min trade: {TRADE_CONFIG.min_amount} contracts")

        # Check current position status
        current_position = get_current_position()
        
        if current_position:
            logger.log_info("📦 Existing position detected")
            logger.log_info(f"   - Side: {current_position['side']}")
            logger.log_info(f"   - Size: {current_position['size']} contracts")
            logger.log_info(f"   - PnL: {current_position['unrealized_pnl']:.2f} USDT")
            
            # If there are open positions, only set the leverage without changing the mode.
            logger.log_info("⚙️ Setting leverage for existing position...")
            exchange.set_leverage(TRADE_CONFIG.leverage, TRADE_CONFIG.symbol)
            logger.log_warning(f"✅ Leverage set: {TRADE_CONFIG.leverage}x")
            
        else:
            # No positions, you can safely set the mode
            logger.log_info("🔄 Setting one-way position mode...")
            try:
                exchange.set_position_mode(False, TRADE_CONFIG.symbol)
                logger.log_info("✅ One-way position mode set")
            except Exception as e:
                logger.log_warning(f"⚠️ Position mode setting: {e}")
            
            logger.log_info("⚙️ Setting cross margin mode and leverage...")
            exchange.set_leverage(
                TRADE_CONFIG.leverage,
                TRADE_CONFIG.symbol,
                {'mgnMode': 'cross'}
            )
            logger.log_warning(f"✅ Cross margin + Leverage {TRADE_CONFIG.leverage}x")

        # Account information
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']
        logger.log_info(f"💰 USDT balance: {usdt_balance:.2f}")
        
        return True

    except Exception as e:
        logger.log_error("exchange_setup", str(e))
        return False


# Global variables to store historical data
price_history = []
signal_history = []
position = None


def calculate_intelligent_position(signal_data, price_data, current_position):
    """Calculate intelligent position size - fixed version"""
    config = TRADE_CONFIG.position_management

    # 🆕 New: If intelligent position is disabled, use fixed position
    if not config.get('enable_intelligent_position', True):
        fixed_contracts = 0.1  # Fixed position size, can be adjusted as needed
        logger.log_warning(f"🔧 Intelligent position disabled, using fixed position: {fixed_contracts} contracts")
        return fixed_contracts

    try:
        # Get account balance
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']

        # Base USDT investment
        base_usdt = config['base_usdt_amount']
        logger.log_warning(f"💰 Available USDT balance: {usdt_balance:.2f}, base investment {base_usdt}")

        # Adjust based on confidence level - fix here
        confidence_multiplier = {
            'HIGH': config['high_confidence_multiplier'],
            'MEDIUM': config['medium_confidence_multiplier'],
            'LOW': config['low_confidence_multiplier']
        }.get(signal_data['confidence'], 1.0)  # Add default value

        # Adjust based on trend strength
        trend = price_data['trend_analysis'].get('overall', 'Consolidation')
        if trend in ['Strong uptrend', 'Strong downtrend']:
            trend_multiplier = config['trend_strength_multiplier']
        else:
            trend_multiplier = 1.0

        # Adjust based on RSI status (reduce position in overbought/oversold areas)
        rsi = price_data['technical_data'].get('rsi', 50)
        if rsi > 75 or rsi < 25:
            rsi_multiplier = 0.7
        else:
            rsi_multiplier = 1.0

        # Calculate suggested USDT investment amount
        suggested_usdt = base_usdt * confidence_multiplier * trend_multiplier * rsi_multiplier

        # Risk management: not exceeding specified ratio of total funds - remove duplicate definition
        max_usdt = usdt_balance * config['max_position_ratio']
        final_usdt = min(suggested_usdt, max_usdt)

        # Correct contract quantity calculation!
        # Formula: Contract quantity = (Investment USDT) / (Current price * Contract multiplier)
        contract_size = (final_usdt) / (price_data['price'] * TRADE_CONFIG.contract_size)

        logger.log_info(f"📊 Position calculation details:")
        logger.log_info(f"   - Base USDT: {base_usdt}")
        logger.log_info(f"   - Confidence multiplier: {confidence_multiplier}")
        logger.log_info(f"   - Trend multiplier: {trend_multiplier}")
        logger.log_info(f"   - RSI multiplier: {rsi_multiplier}")
        logger.log_info(f"   - Suggested USDT: {suggested_usdt:.2f}")
        logger.log_info(f"   - Final USDT: {final_usdt:.2f}")
        logger.log_info(f"   - Contract multiplier: {TRADE_CONFIG.contract_size}")
        logger.log_info(f"   - Calculated contracts: {contract_size:.4f} contracts")

        # Precision handling: OKX BTC contract minimum trading unit is 0.01 contracts
        contract_size = round(contract_size, 2)  # Keep 2 decimal places

        # Ensure minimum trading volume
        min_contracts = getattr(TRADE_CONFIG, 'min_amount', 0.01)
        if contract_size < min_contracts:
            contract_size = min_contracts
            logger.log_warning(f"⚠️ Position less than minimum, adjusted to: {contract_size} contracts")

        logger.log_info(f"🎯 Final position: {final_usdt:.2f} USDT → {contract_size:.2f} contracts")
        return contract_size

    except Exception as e:
        logger.log_error("Position calculation failed, using base position", str(e))
        # Emergency backup calculation
        base_usdt = config['base_usdt_amount']
        contract_size = (base_usdt * TRADE_CONFIG.leverage) / (
                    price_data['price'] * getattr(TRADE_CONFIG, 'contract_size', 0.01))
        return round(max(contract_size, getattr(TRADE_CONFIG, 'min_amount', 0.01)), 2)


def calculate_technical_indicators(df):
    """Calculate technical indicators - from first strategy"""
    try:
        # Moving averages
        df['sma_5'] = df['close'].rolling(window=5, min_periods=1).mean()
        df['sma_20'] = df['close'].rolling(window=20, min_periods=1).mean()
        df['sma_50'] = df['close'].rolling(window=50, min_periods=1).mean()

        # Exponential moving averages
        df['ema_12'] = df['close'].ewm(span=12).mean()
        df['ema_26'] = df['close'].ewm(span=26).mean()
        df['macd'] = df['ema_12'] - df['ema_26']
        df['macd_signal'] = df['macd'].ewm(span=9).mean()
        df['macd_histogram'] = df['macd'] - df['macd_signal']

        # Relative Strength Index (RSI)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # Bollinger Bands
        df['bb_middle'] = df['close'].rolling(20).mean()
        bb_std = df['close'].rolling(20).std()
        df['bb_upper'] = df['bb_middle'] + (bb_std * 2)
        df['bb_lower'] = df['bb_middle'] - (bb_std * 2)
        df['bb_position'] = (df['close'] - df['bb_lower']) / (df['bb_upper'] - df['bb_lower'])

        # Volume moving average
        df['volume_ma'] = df['volume'].rolling(20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_ma']

        # Support resistance levels
        df['resistance'] = df['high'].rolling(20).max()
        df['support'] = df['low'].rolling(20).min()

        # Fill NaN values
        df = df.bfill().ffill()

        return df
    except Exception as e:
        logger.log_error("technical_indicators", str(e))
        return df


def get_support_resistance_levels(df, lookback=20):
    """Calculate support resistance levels"""
    try:
        recent_high = df['high'].tail(lookback).max()
        recent_low = df['low'].tail(lookback).min()
        current_price = df['close'].iloc[-1]

        resistance_level = recent_high
        support_level = recent_low

        # Dynamic support resistance (based on Bollinger Bands)
        bb_upper = df['bb_upper'].iloc[-1]
        bb_lower = df['bb_lower'].iloc[-1]

        return {
            'static_resistance': resistance_level,
            'static_support': support_level,
            'dynamic_resistance': bb_upper,
            'dynamic_support': bb_lower,
            'price_vs_resistance': ((resistance_level - current_price) / current_price) * 100,
            'price_vs_support': ((current_price - support_level) / support_level) * 100
        }
    except Exception as e:
        logger.log_error("support_resistance", str(e))
        return {}


def get_sentiment_indicators():
    """Get sentiment indicators - simplified version"""
    try:
        API_URL = TRADE_CONFIG.sentiment_api_url
        API_KEY = TRADE_CONFIG.sentiment_api_key

        # Get recent 4-hour data
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=4)

        request_body = {
            "apiKey": API_KEY,
            "endpoints": ["CO-A-02-01", "CO-A-02-02"],  # Keep only core indicators
            "startTime": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "endTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            "timeType": "15m",
            "token": ["BTC"]
        }

        headers = {"Content-Type": "application/json", "X-API-KEY": API_KEY}
        response = requests.post(API_URL, json=request_body, headers=headers)

        if response.status_code == 200:
            data = response.json()
            if data.get("code") == 200 and data.get("data"):
                time_periods = data["data"][0]["timePeriods"]

                # Find first time period with valid data
                for period in time_periods:
                    period_data = period.get("data", [])

                    sentiment = {}
                    valid_data_found = False

                    for item in period_data:
                        endpoint = item.get("endpoint")
                        value = item.get("value", "").strip()

                        if value:  # Only process non-empty values
                            try:
                                if endpoint in ["CO-A-02-01", "CO-A-02-02"]:
                                    sentiment[endpoint] = float(value)
                                    valid_data_found = True
                            except (ValueError, TypeError):
                                continue

                    # If valid data found
                    if valid_data_found and "CO-A-02-01" in sentiment and "CO-A-02-02" in sentiment:
                        positive = sentiment['CO-A-02-01']
                        negative = sentiment['CO-A-02-02']
                        net_sentiment = positive - negative

                        # Correct time delay calculation
                        data_delay = int((datetime.now() - datetime.strptime(
                            period['startTime'], '%Y-%m-%d %H:%M:%S')).total_seconds() // 60)

                        logger.log_warning(f"✅ Using sentiment data time: {period['startTime']} (Delay: {data_delay} minutes)")

                        return {
                            'positive_ratio': positive,
                            'negative_ratio': negative,
                            'net_sentiment': net_sentiment,
                            'data_time': period['startTime'],
                            'data_delay_minutes': data_delay
                        }

                logger.log_warning("❌ All time period data is empty")
                return None

        return None
    except Exception as e:
        logger.log_error("sentiment_data", str(e))
        return None


def get_market_trend(df):
    """Determine market trend"""
    try:
        current_price = df['close'].iloc[-1]

        # Multi-timeframe trend analysis
        trend_short = "Uptrend" if current_price > df['sma_20'].iloc[-1] else "Downtrend"
        trend_medium = "Uptrend" if current_price > df['sma_50'].iloc[-1] else "Downtrend"

        # MACD trend
        macd_trend = "bullish" if df['macd'].iloc[-1] > df['macd_signal'].iloc[-1] else "bearish"

        # Comprehensive trend judgment
        if trend_short == "Uptrend" and trend_medium == "Uptrend":
            overall_trend = "Strong uptrend"
        elif trend_short == "Downtrend" and trend_medium == "Downtrend":
            overall_trend = "Strong downtrend"
        else:
            overall_trend = "Consolidation"

        return {
            'short_term': trend_short,
            'medium_term': trend_medium,
            'macd': macd_trend,
            'overall': overall_trend,
            'rsi_level': df['rsi'].iloc[-1]
        }
    except Exception as e:
        logger.log_error("trend_analysis", str(e))
        return {}
    
def fetch_ohlcv_with_retry(max_retries=None):
    if max_retries is None:
        max_retries = TRADE_CONFIG.max_retries
    
    for i in range(max_retries):
        try:
            return exchange.fetch_ohlcv(TRADE_CONFIG.symbol, TRADE_CONFIG.timeframe, limit=TRADE_CONFIG.data_points)
        except Exception as e:
            logger.log_error(f"Get K line fail, retry {i+1}/{max_retries}", str(e))
            time.sleep(1)
    return None

# Optimization: Add a unified error handling and retry decorator
def retry_on_failure(max_retries=None, delay=None, exceptions=(Exception,)):
    # """Unified error handling and retry decorator"""
    if max_retries is None:
        max_retries = TRADE_CONFIG.max_retries
    if delay is None:
        delay = TRADE_CONFIG.retry_delay
        
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    logger.log_error(f"⚠️ {func.__name__} attempt {attempt + 1}", str(e))
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

@retry_on_failure(max_retries=TRADE_CONFIG.max_retries, delay=TRADE_CONFIG.retry_delay)
def get_btc_ohlcv_enhanced():
    """Enhanced version: Get BTC K-line data and calculate technical indicators"""
    try:
        # Get K-line data
        ohlcv = fetch_ohlcv_with_retry()

        if ohlcv is None:
            logger.log_warning("❌ Failed to fetch K-line data")
            return None

        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

        # Calculate technical indicators
        df = calculate_technical_indicators(df)

        current_data = df.iloc[-1]
        previous_data = df.iloc[-2]

        # Get technical analysis data
        trend_analysis = get_market_trend(df)
        levels_analysis = get_support_resistance_levels(df)

        return {
            'price': current_data['close'],
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'high': current_data['high'],
            'low': current_data['low'],
            'volume': current_data['volume'],
            'timeframe': TRADE_CONFIG.timeframe,
            'price_change': ((current_data['close'] - previous_data['close']) / previous_data['close']) * 100,
            'kline_data': df[['timestamp', 'open', 'high', 'low', 'close', 'volume']].tail(10).to_dict('records'),
            'technical_data': {
                'sma_5': current_data.get('sma_5', 0),
                'sma_20': current_data.get('sma_20', 0),
                'sma_50': current_data.get('sma_50', 0),
                'rsi': current_data.get('rsi', 0),
                'macd': current_data.get('macd', 0),
                'macd_signal': current_data.get('macd_signal', 0),
                'macd_histogram': current_data.get('macd_histogram', 0),
                'bb_upper': current_data.get('bb_upper', 0),
                'bb_lower': current_data.get('bb_lower', 0),
                'bb_position': current_data.get('bb_position', 0),
                'volume_ratio': current_data.get('volume_ratio', 0)
            },
            'trend_analysis': trend_analysis,
            'levels_analysis': levels_analysis,
            'full_data': df
        }
    except Exception as e:
        logger.log_error("kline_data", str(e))
        return None
    
def add_to_signal_history(signal_data):
    global signal_history
    
    signal_history.append(signal_data)  # 改为追加信号数据
    
    # Limit the history to 100 records
    max_history = 100
    if len(signal_history) > max_history:
        # Keep the latest 80% and remove the oldest 20%
        keep_count = int(max_history * 0.8)
        signal_history = signal_history[-keep_count:]

def add_to_price_history(price_data):
    global price_history
    
    price_history.append(price_data)
    
    # Limit the history to 200 records
    max_history = 200
    if len(price_history) > max_history:
        keep_count = int(max_history * 0.8)
        price_history = price_history[-keep_count:]

def generate_technical_analysis_text(price_data):
    """Generate technical analysis text"""
    if 'technical_data' not in price_data:
        return "Technical indicator data unavailable"

    tech = price_data['technical_data']
    trend = price_data.get('trend_analysis', {})
    levels = price_data.get('levels_analysis', {})

    # Check data validity
    def safe_float(value, default=0):
        return float(value) if value and pd.notna(value) else default

    analysis_text = f"""
    【Technical Indicator Analysis】
    📈 Moving Averages:
    - 5-period: {safe_float(tech['sma_5']):.2f} | Price relative: {(price_data['price'] - safe_float(tech['sma_5'])) / safe_float(tech['sma_5']) * 100:+.2f}%
    - 20-period: {safe_float(tech['sma_20']):.2f} | Price relative: {(price_data['price'] - safe_float(tech['sma_20'])) / safe_float(tech['sma_20']) * 100:+.2f}%
    - 50-period: {safe_float(tech['sma_50']):.2f} | Price relative: {(price_data['price'] - safe_float(tech['sma_50'])) / safe_float(tech['sma_50']) * 100:+.2f}%

    🎯 Trend Analysis:
    - Short-term trend: {trend.get('short_term', 'N/A')}
    - Medium-term trend: {trend.get('medium_term', 'N/A')}
    - Overall trend: {trend.get('overall', 'N/A')}
    - MACD direction: {trend.get('macd', 'N/A')}

    📊 Momentum Indicators:
    - RSI: {safe_float(tech['rsi']):.2f} ({'Overbought' if safe_float(tech['rsi']) > 70 else 'Oversold' if safe_float(tech['rsi']) < 30 else 'Neutral'})
    - MACD: {safe_float(tech['macd']):.4f}
    - Signal line: {safe_float(tech['macd_signal']):.4f}

    🎚️ Bollinger Band position: {safe_float(tech['bb_position']):.2%} ({'Upper' if safe_float(tech['bb_position']) > 0.7 else 'Lower' if safe_float(tech['bb_position']) < 0.3 else 'Middle'})

    💰 Key Levels:
    - Static resistance: {safe_float(levels.get('static_resistance', 0)):.2f}
    - Static support: {safe_float(levels.get('static_support', 0)):.2f}
    """
    return analysis_text


def get_current_position():
    """Get current position status - OKX version"""
    try:
        positions = exchange.fetch_positions([TRADE_CONFIG.symbol])

        for pos in positions:
            if pos['symbol'] == TRADE_CONFIG.symbol:
                contracts = float(pos['contracts']) if pos['contracts'] else 0

                if contracts > 0:
                    return {
                        'side': pos['side'],  # 'long' or 'short'
                        'size': contracts,
                        'entry_price': float(pos['entryPrice']) if pos['entryPrice'] else 0,
                        'unrealized_pnl': float(pos['unrealizedPnl']) if pos['unrealizedPnl'] else 0,
                        'leverage': float(pos['leverage']) if pos['leverage'] else TRADE_CONFIG.leverage,
                        'symbol': pos['symbol']
                    }

        return None

    except Exception as e:
        logger.log_error("position_fetch", str(e))
        import traceback
        traceback.print_exc()
        return None


def safe_json_parse(json_str):
    """Safely parse JSON, handle non-standard format situations"""
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        try:
            # Fix common JSON format issues
            json_str = json_str.replace("'", '"')
            json_str = re.sub(r'(\w+):', r'"\1":', json_str)
            json_str = re.sub(r',\s*}', '}', json_str)
            json_str = re.sub(r',\s*]', ']', json_str)
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.log_error("json_parsing", f"Failed to parse: {json_str}")
            logger.log_error(f"Error details: {e}")
            return None


def create_fallback_signal(price_data):
    """Create backup trading signal"""
    return {
        "signal": "HOLD",
        "reason": "Conservative strategy adopted due to temporary unavailability of technical analysis",
        "stop_loss": price_data['price'] * 0.98,  # -2%
        "take_profit": price_data['price'] * 1.02,  # +2%
        "confidence": "LOW",
        "is_fallback": True
    }

@retry_on_failure(max_retries=TRADE_CONFIG.max_retries, delay=TRADE_CONFIG.retry_delay)
def analyze_with_deepseek(price_data):
    """Use DeepSeek to analyze market and generate trading signals (enhanced version)"""
    try:
        # Get the client (will be initialized on the first call)
        client = get_deepseek_client()
    
        # Generate technical analysis text
        technical_analysis = generate_technical_analysis_text(price_data)

        # Build K-line data text
        kline_text = f"【Recent 5 {TRADE_CONFIG.timeframe} K-line Data】\n"
        for i, kline in enumerate(price_data['kline_data'][-5:]):
            trend = "Bullish" if kline['close'] > kline['open'] else "Bearish"
            change = ((kline['close'] - kline['open']) / kline['open']) * 100
            kline_text += f"K-line {i + 1}: {trend} Open:{kline['open']:.2f} Close:{kline['close']:.2f} Change:{change:+.2f}%\n"

        # Add previous trading signal
        signal_text = ""
        if signal_history:
            last_signal = signal_history[-1]
            signal_text = f"\n【Previous Trading Signal】\nSignal: {last_signal.get('signal', 'N/A')}\nConfidence: {last_signal.get('confidence', 'N/A')}"

        # Get sentiment data
        sentiment_data = get_sentiment_indicators()
        # Simplified sentiment text - too much is useless
        if sentiment_data:
            sign = '+' if sentiment_data['net_sentiment'] >= 0 else ''
            sentiment_text = f"【Market Sentiment】Optimistic {sentiment_data['positive_ratio']:.1%} Pessimistic {sentiment_data['negative_ratio']:.1%} Net {sign}{sentiment_data['net_sentiment']:.3f}"
        else:
            sentiment_text = "【Market Sentiment】Data temporarily unavailable"

        # Add current position information
        current_pos = get_current_position()
        position_text = "No position" if not current_pos else f"{current_pos['side']} position, Quantity: {current_pos['size']}, P&L: {current_pos['unrealized_pnl']:.2f}USDT"
        pnl_text = f", Position P&L: {current_pos['unrealized_pnl']:.2f} USDT" if current_pos else ""

        prompt = f"""
        You are a professional cryptocurrency trading analyst. Please analyze based on the following BTC/USDT {TRADE_CONFIG.timeframe} period data:

        {kline_text}

        {technical_analysis}

        {signal_text}

        {sentiment_text}  # Add sentiment analysis

        【Current Market】
        - Current price: ${price_data['price']:,.2f}
        - Time: {price_data['timestamp']}
        - Current K-line high: ${price_data['high']:,.2f}
        - Current K-line low: ${price_data['low']:,.2f}
        - Current K-line volume: {price_data['volume']:.2f} BTC
        - Price change: {price_data['price_change']:+.2f}%
        - Current position: {position_text}{pnl_text}

        【Anti-Frequent Trading Important Principles】
        1. **Trend Continuity Priority**: Do not change overall trend judgment based on single K-line or short-term fluctuations
        2. **Position Stability**: Maintain existing position direction unless trend clearly reverses strongly
        3. **Reversal Confirmation**: Require at least 2-3 technical indicators to simultaneously confirm trend reversal before changing signal
        4. **Cost Awareness**: Reduce unnecessary position adjustments, every trade has costs

        【Trading Guidance Principles - Must Follow】
        1. **Technical Analysis Dominant** (Weight 60%): Trend, support resistance, K-line patterns are main basis
        2. **Market Sentiment Auxiliary** (Weight 30%): Sentiment data used to verify technical signals, cannot be used alone as trading reason
        - Sentiment and technical same direction → Enhance signal confidence
        - Sentiment and technical divergence → Mainly based on technical analysis, sentiment only as reference
        - Sentiment data delay → Reduce weight, use real-time technical indicators as main
        3. **Risk Management** (Weight 10%): Consider position, profit/loss status and stop loss position
        4. **Trend Following**: Take immediate action when clear trend appears, do not over-wait
        5. Because trading BTC, long position weight can be slightly higher
        6. **Signal Clarity**:
        - Strong uptrend → BUY signal
        - Strong downtrend → SELL signal
        - Only in narrow range consolidation, no clear direction → HOLD signal
        7. **Technical Indicator Weight**:
        - Trend (moving average arrangement) > RSI > MACD > Bollinger Bands
        - Price breaking key support/resistance levels is important signal

        【Current Technical Condition Analysis】
        - Overall trend: {price_data['trend_analysis'].get('overall', 'N/A')}
        - Short-term trend: {price_data['trend_analysis'].get('short_term', 'N/A')}
        - RSI status: {price_data['technical_data'].get('rsi', 0):.1f} ({'Overbought' if price_data['technical_data'].get('rsi', 0) > 70 else 'Oversold' if price_data['technical_data'].get('rsi', 0) < 30 else 'Neutral'})
        - MACD direction: {price_data['trend_analysis'].get('macd', 'N/A')}

        【Intelligent Position Management Rules - Must Follow】

        1. **Reduce Over-Conservatism**:
        - Do not over-HOLD due to slight overbought/oversold in clear trends
        - RSI in 30-70 range is healthy range, should not be main HOLD reason
        - Bollinger Band position in 20%-80% is normal fluctuation range

        2. **Trend Following Priority**:
        - Strong uptrend + any RSI value → Active BUY signal
        - Strong downtrend + any RSI value → Active SELL signal
        - Consolidation + no clear direction → HOLD signal

        3. **Breakout Trading Signals**:
        - Price breaks key resistance + volume amplification → High confidence BUY
        - Price breaks key support + volume amplification → High confidence SELL

        4. **Position Optimization Logic**:
        - Existing position and trend continues → Maintain or BUY/SELL signal
        - Clear trend reversal → Timely reverse signal
        - Do not over-HOLD because of existing position

        【Important】Please make clear judgments based on technical analysis, avoid missing trend opportunities due to over-caution!

        【Analysis Requirements】
        Based on above analysis, please provide clear trading signal

        Please reply in following JSON format:
        {{
            "signal": "BUY|SELL|HOLD",
            "reason": "Brief analysis reason (including trend judgment and technical basis)",
            "stop_loss": specific price,
            "take_profit": specific price,
            "confidence": "HIGH|MEDIUM|LOW"
        }}
        """

        try:
            response = client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {"role": "system",
                    "content": f"You are a professional trader focusing on {TRADE_CONFIG.timeframe} period trend analysis. Please make judgments combining K-line patterns and technical indicators, and strictly follow JSON format requirements."},
                    {"role": "user", "content": prompt}
                ],
                stream=False,
                temperature=0.1
            )

            # Safely parse JSON
            result = response.choices[0].message.content
            logger.log_info(f"DeepSeek original reply: {result}")

            # Extract JSON part
            start_idx = result.find('{')
            end_idx = result.rfind('}') + 1

            if start_idx != -1 and end_idx != 0:
                json_str = result[start_idx:end_idx]
                signal_data = safe_json_parse(json_str)

                if signal_data is None:
                    signal_data = create_fallback_signal(price_data)
            else:
                signal_data = create_fallback_signal(price_data)

            # Verify required fields
            required_fields = ['signal', 'reason', 'stop_loss', 'take_profit', 'confidence']
            if not all(field in signal_data for field in required_fields):
                signal_data = create_fallback_signal(price_data)

            # Save signal to history record
            signal_data['timestamp'] = price_data['timestamp']
            add_to_signal_history(signal_data)
            if len(signal_history) > 30:
                signal_history.pop(0)

            # Signal statistics
            signal_count = len([s for s in signal_history if s.get('signal') == signal_data['signal']])
            total_signals = len(signal_history)
            logger.log_info(f"Signal statistics: {signal_data['signal']} (Appeared {signal_count} times in recent {total_signals} signals)")

            # Signal continuity check
            if len(signal_history) >= 3:
                last_three = [s['signal'] for s in signal_history[-3:]]
                if len(set(last_three)) == 1:
                    logger.log_warning(f"⚠️ Note: Consecutive 3 {signal_data['signal']} signals")

            return signal_data

        except Exception as api_error:
                # 🔴API call or response processing failed
                logger.log_error("deepseek_api_call", str(api_error))
                return create_fallback_signal(price_data)
            
    except Exception as prep_error:
        # 🔴Preparation phase failed
        logger.log_error("analysis_preparation", str(prep_error))
        return create_fallback_signal(price_data)

def check_market_conditions():
    """Check if market conditions are suitable for trading."""
    try:
        ticker = exchange.fetch_ticker(TRADE_CONFIG.symbol)
        spread = (ticker['ask'] - ticker['bid']) / ticker['bid']
        
        # If spread is too wide, avoid trading
        if spread > 0.002:  # 0.2%
            logger.log_warning(f"⚠️ Wide spread: {spread:.4%}, avoiding trade.")
            return False
            
        return True
    except Exception as e:
        logger.log_error("market_conditions", str(e))
        return False

def check_trading_frequency():
    """Check if we are trading too frequently."""
    global signal_history
    
    if len(signal_history) < 3:
        return True
    
    recent_signals = [s['signal'] for s in signal_history[-3:]]
    signal_changes = sum(1 for i in range(1, len(recent_signals)) 
                      if recent_signals[i] != recent_signals[i-1])
    
    # If there are too many signal changes, pause trading
    if signal_changes >= 2:
        logger.log_info("⚠️ Too frequent signal changes, pausing trading.")
        return False
    
    return True

def execute_intelligent_trade(signal_data, price_data):
    """Execute intelligent trading - OKX version (supports same direction position increase/decrease)"""
    global position

    # add order tag
    order_tag = create_order_tag()

    # Add these checks at the beginning:
    if not check_market_conditions():
        return
    if not check_trading_frequency():
        return
    
    current_position = get_current_position()

    # Prevent frequent reversal logic remains unchanged
    if current_position and signal_data['signal'] != 'HOLD':
        current_side = current_position['side']  # 'long' or 'short'

        if signal_data['signal'] == 'BUY':
            new_side = 'long'
        elif signal_data['signal'] == 'SELL':
            new_side = 'short'
        else:
            new_side = None

        # If direction opposite, need high confidence to execute
        # if new_side != current_side:
        #     if signal_data['confidence'] != 'HIGH':
        #         logger.log_warning(f"🔒 Non-high confidence reversal signal, maintain existing {current_side} position")
        #         return

        #     if len(signal_history) >= 2:
        #         last_signals = [s['signal'] for s in signal_history[-2:]]
        #         if signal_data['signal'] in last_signals:
        #             logger.log_warning(f"🔒 Recently appeared {signal_data['signal']} signal, avoid frequent reversal")
        #             return

    # Calculate intelligent position
    position_size = calculate_intelligent_position(signal_data, price_data, current_position)

    logger.log_info(f"Trading signal: {signal_data['signal']}")
    logger.log_info(f"Confidence level: {signal_data['confidence']}")
    logger.log_info(f"Intelligent position: {position_size:.2f} contracts")
    logger.log_info(f"Reason: {signal_data['reason']}")
    logger.log_info(f"Current position: {current_position}")

    # Risk management
    if signal_data['confidence'] == 'LOW' and not TRADE_CONFIG.test_mode:
        logger.log_warning("⚠️ Low confidence signal, skipping execution")
        return

    if TRADE_CONFIG.test_mode:
        logger.log_info("Test mode - simulated trading only")
        return
    
    try:
        # Execute trading logic - supports same direction position increase/decrease
        if signal_data['signal'] == 'BUY':
            if current_position and current_position['side'] == 'short':
                # First check if short position actually exists and quantity is correct
                if current_position['size'] > 0:
                    logger.log_info(f"Closing short position {current_position['size']:.2f} contracts and opening long position {position_size:.2f} contracts...")
                    # Close short position
                    exchange.create_market_order(
                        TRADE_CONFIG.symbol,
                        'buy',
                        current_position['size'],
                        params={'reduceOnly': True, 'tag': order_tag}
                    )
                    time.sleep(1)
                    # Open long position
                    exchange.create_market_order(
                        TRADE_CONFIG.symbol,
                        'buy',
                        position_size,
                        params={'tag': order_tag}
                    )
                else:
                    logger.log_warning("⚠️ Detected short position but quantity is 0, directly opening long position")
                    exchange.create_market_order(
                        TRADE_CONFIG.symbol,
                        'buy',
                        position_size,
                        params={'tag': order_tag}
                    )

            elif current_position and current_position['side'] == 'long':
                # Same direction, check if position adjustment needed
                size_diff = position_size - current_position['size']

                if abs(size_diff) >= 0.01:  # Adjustable difference exists
                    if size_diff > 0:
                        # Increase position
                        add_size = round(size_diff, 2)
                        logger.log_info(f"Long position increase {add_size:.2f} contracts (Current:{current_position['size']:.2f} → Target:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG.symbol,
                            'buy',
                            add_size,
                            params={'tag': order_tag}
                        )
                    else:
                        # Decrease position
                        reduce_size = round(abs(size_diff), 2)
                        logger.log_info(
                            f"Long position decrease {reduce_size:.2f} contracts (Current:{current_position['size']:.2f} → Target:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG.symbol,
                            'sell',
                            reduce_size,
                            params={'reduceOnly': True, 'tag': order_tag}
                        )
                else:
                    logger.log_info(
                        f"Existing long position, position appropriate maintaining status (Current:{current_position['size']:.2f}, Target:{position_size:.2f})")
            else:
                # Open long position when no position
                logger.log_info(f"Opening long position {position_size:.2f} contracts...")
                exchange.create_market_order(
                    TRADE_CONFIG.symbol,
                    'buy',
                    position_size,
                    params={'tag': order_tag}
                )

        elif signal_data['signal'] == 'SELL':
            if current_position and current_position['side'] == 'long':
                # First check if long position actually exists and quantity is correct
                if current_position['size'] > 0:
                    logger.log_warning(f"Closing long position {current_position['size']:.2f} contracts and opening short position {position_size:.2f} contracts...")
                    # Close long position
                    exchange.create_market_order(
                        TRADE_CONFIG.symbol,
                        'sell',
                        current_position['size'],
                        params={'reduceOnly': True, 'tag': order_tag}
                    )
                    time.sleep(1)
                    # Open short position
                    exchange.create_market_order(
                        TRADE_CONFIG.symbol,
                        'sell',
                        position_size,
                        params={'tag': order_tag}
                    )
                else:
                    logger.log_warning("⚠️ Detected long position but quantity is 0, directly opening short position")
                    exchange.create_market_order(
                        TRADE_CONFIG.symbol,
                        'sell',
                        position_size,
                        params={'tag': order_tag}
                    )

            elif current_position and current_position['side'] == 'short':
                # Same direction, check if position adjustment needed
                size_diff = position_size - current_position['size']

                if abs(size_diff) >= 0.01:  # Adjustable difference exists
                    if size_diff > 0:
                        # Increase position
                        add_size = round(size_diff, 2)
                        logger.log_warning(
                            f"Short position increase {add_size:.2f} contracts (Current:{current_position['size']:.2f} → Target:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG.symbol,
                            'sell',
                            add_size,
                            params={'tag': order_tag}
                        )
                    else:
                        # Decrease position
                        reduce_size = round(abs(size_diff), 2)
                        logger.log_warning(
                            f"Short position decrease {reduce_size:.2f} contracts (Current:{current_position['size']:.2f} → Target:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG.symbol,
                            'buy',
                            reduce_size,
                            params={'reduceOnly': True, 'tag': order_tag}
                        )
                else:
                    logger.log_info(
                        f"Existing short position, position appropriate maintaining status (Current:{current_position['size']:.2f}, Target:{position_size:.2f})")
            else:
                # Open short position when no position
                logger.log_info(f"Opening short position {position_size:.2f} contracts...")
                exchange.create_market_order(
                    TRADE_CONFIG.symbol,
                    'sell',
                    position_size,
                    params={'tag': order_tag}
                )

        elif signal_data['signal'] == 'HOLD':
            logger.log_info("Suggest observing, no trade execution")
            return

        logger.log_info("Intelligent trading executed successfully")
        time.sleep(2)
        position = get_current_position()
        logger.log_info(f"Updated position: {position}")

    except Exception as e:
        logger.log_error("trade_execution", str(e))

        # If it's a position doesn't exist error, try to directly open new position
        if "don't have any positions" in str(e):
            logger.log_warning("Attempting to directly open new position...")
            try:
                if signal_data['signal'] == 'BUY':
                    exchange.create_market_order(
                        TRADE_CONFIG.symbol,
                        'buy',
                        position_size,
                        params={'tag': order_tag}
                    )
                elif signal_data['signal'] == 'SELL':
                    exchange.create_market_order(
                        TRADE_CONFIG.symbol,
                        'sell',
                        position_size,
                        params={'tag': order_tag}
                    )
                logger.log_info("Direct position opening successful")
            except Exception as e2:
                logger.log_error("Direct position opening also failed", str(e2))

        import traceback
        traceback.print_exc()


def analyze_with_deepseek_with_retry(price_data, max_retries=TRADE_CONFIG.max_retries):
    """DeepSeek analysis with retry"""
    for attempt in range(max_retries):
        try:
            signal_data = analyze_with_deepseek(price_data)
            if signal_data and not signal_data.get('is_fallback', False):
                return signal_data

            logger.log_warning(f"Attempt {attempt + 1} failed, retrying...")
            time.sleep(1)

        except Exception as e:
            logger.log_error("DeepSeek analysis failed", str(e))
            if attempt == max_retries - 1:
                return create_fallback_signal(price_data)
            time.sleep(1)

    return create_fallback_signal(price_data)

def wait_for_next_period():
    """Wait until next 15-minute mark"""
    now = datetime.now()
    current_minute = now.minute
    current_second = now.second

    # Calculate next mark time (00, 15, 30, 45 minutes)
    next_period_minute = ((current_minute // 15) + 1) * 15
    if next_period_minute == 60:
        next_period_minute = 0

    # Calculate total seconds to wait
    if next_period_minute > current_minute:
        minutes_to_wait = next_period_minute - current_minute
    else:
        minutes_to_wait = 60 - current_minute + next_period_minute

    seconds_to_wait = minutes_to_wait * 60 - current_second

    # If the waiting time exceeds 10 minutes, reduce the waiting time to the next 5-minute interval.
    if seconds_to_wait > 600:  # 10 minutes
        logger.log_warning(f"🕒 Long wait detected ({seconds_to_wait}s), adjusting to shorter interval...")
        # Adjust to wait until the next 5-minute mark
        next_5min = ((current_minute // 5) + 1) * 5
        if next_5min == 60:
            next_5min = 0
        minutes_to_wait = next_5min - current_minute
        if minutes_to_wait < 0:
            minutes_to_wait += 60
        seconds_to_wait = minutes_to_wait * 60 - current_second

    # Display friendly waiting time
    display_minutes = int(seconds_to_wait // 60)
    display_seconds = int(seconds_to_wait % 60)

    if display_minutes > 0:
        logger.log_info(f"🕒 Waiting {display_minutes} minutes {display_seconds} seconds until mark...")
    else:
        logger.log_info(f"🕒 Waiting {display_seconds} seconds until mark...")

    return seconds_to_wait

def filter_signal(signal_data, price_data):
    # If the signal is to buy, but the RSI is above 70, then change it to hold.
    rsi = price_data['technical_data'].get('rsi', 50)
    if signal_data['signal'] == 'BUY' and rsi > 70:
        return {
            **signal_data,
            'signal': 'HOLD',
            'reason': f'RSI overbought ({rsi:.2f}), hold instead',
            'confidence': 'LOW'
        }
    # Similarly, other filtering conditions can be added.
    return signal_data

def trading_bot():
    # Wait until mark before executing
    wait_seconds = wait_for_next_period()
    if wait_seconds > 0:
        time.sleep(wait_seconds)

    """Main trading bot function"""
    logger.log_info("\n" + "=" * 60)
    logger.log_info(f"Execution time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.log_info("=" * 60)

    # 1. Get enhanced K-line data
    price_data = get_btc_ohlcv_enhanced()
    if not price_data:
        return

    logger.log_info(f"BTC current price: ${price_data['price']:,.2f}")
    logger.log_info(f"Data period: {TRADE_CONFIG.timeframe}")
    logger.log_info(f"Price change: {price_data['price_change']:+.2f}%")

    # 2. Use DeepSeek analysis (with retry)
    signal_data = analyze_with_deepseek_with_retry(price_data)

    # Filter signals
    signal_data = filter_signal(signal_data, price_data)

    if signal_data.get('is_fallback', False):
        logger.log_warning("⚠️ Using backup trading signal")

    # 3. Execute intelligent trading
    execute_intelligent_trade(signal_data, price_data)

def health_check():
    """Check the health of the system."""
    checks = []
    
    # Check API connection
    try:
        exchange.fetch_balance()
        checks.append(("API Connection", "✅"))
    except Exception as e:
        checks.append(("API Connection", "❌"))
        logger.log_error("health_check_api", str(e))
    
    # Check network
    try:
        import requests
        requests.get(TRADE_CONFIG.deepseek_base_url, timeout=5)
        checks.append(("Network", "✅"))
    except Exception as e:
        checks.append(("Network", "❌"))
        logger.log_error("health_check_network", str(e))
    
    # Check data freshness - improvements
    if price_history:
        latest_data = price_history[-1]
        try:
            data_age = (datetime.now() - datetime.strptime(latest_data['timestamp'], '%Y-%m-%d %H:%M:%S')).total_seconds()
            status = "✅" if data_age < 300 else "⚠️"
            checks.append(("Data Freshness", f"{status} ({data_age:.0f}s)"))
        except Exception as e:
            checks.append(("Data Freshness", f"⚠️ (Parse error: {e})"))
    else:
        checks.append(("Data Freshness", "⚠️ (No data yet)"))
    
    # Build detailed status string for logging
    details = "; ".join([f"{check}: {status}" for check, status in checks])
    
    # 🆕Improvement: Temporary data loss should not cause the overall health check to fail.
    overall_status = all("❌" not in status for _, status in checks)
    
    # Use logger.log_health_check instead of print
    logger.log_health_check(overall_status, details)
    
    return overall_status

def log_performance_metrics():
    """Log performance metrics."""
    if not signal_history:
        return
    
    signals = [s['signal'] for s in signal_history]
    buy_count = signals.count('BUY')
    sell_count = signals.count('SELL')
    hold_count = signals.count('HOLD')
    total = len(signals)
    
    # Use logger.log_performance instead of print
    performance_metrics = {
        'buy_signals': f"{buy_count}/{total}",
        'sell_signals': f"{sell_count}/{total}", 
        'hold_signals': f"{hold_count}/{total}",
        'total_signals': total
    }
    logger.log_performance(performance_metrics)

def main():
    logger.log_info("BTC/USDT OKX Automated Trading Bot Started!")
    
    if not setup_exchange():
        logger.log_error("exchange_setup", "Initialization failed")
        return
    
    # 🆕 在健康检查前先获取一次数据
    logger.log_info("🔄 Initial data fetch...")
    initial_price_data = get_btc_ohlcv_enhanced()
    if initial_price_data:
        add_to_price_history(initial_price_data)
        logger.log_info("✅ Initial data fetched successfully")
    else:
        logger.log_warning("⚠️ Initial data fetch failed")
    
    consecutive_errors = 0
    TRADE_CONFIG.max_consecutive_errors = 5
    
    # Timing variables for different intervals
    last_health_check = time.time()  # 🆕 立即开始计时
    health_check_interval = TRADE_CONFIG.health_check_interval  # 300 seconds
    
    last_config_check = time.time()
    config_check_interval = TRADE_CONFIG.config_check_interval  # 300 seconds

    last_perf_log = time.time()
    perf_log_interval = TRADE_CONFIG.perf_log_interval  # 600 seconds

    while True:
        try:
            current_time = time.time()
            
            # Health check - every 5 minutes
            if current_time - last_health_check >= health_check_interval:
                logger.log_info("🔍 Running scheduled health check...")
                if not health_check():
                    consecutive_errors += 1
                    if consecutive_errors >= TRADE_CONFIG.max_consecutive_errors:
                        logger.log_info("🚨 Too many consecutive errors, exiting.")
                        break
                else:
                    consecutive_errors = 0
                
                last_health_check = current_time
            
            # Configuration reload check - every 5 minutes
            if current_time - last_config_check >= config_check_interval:
                if TRADE_CONFIG.should_reload():
                    TRADE_CONFIG.reload()  
                last_config_check = current_time

            # Run trading bot
            trading_bot()
            
            # Log performance
            log_performance_metrics()
            
            # Wait for next cycle
            time.sleep(60)
            
        except KeyboardInterrupt:
            logger.log_warning("\n🛑 User interrupted the program.")
            break
        except Exception as e:
            logger.log_error("main_loop", str(e))
            consecutive_errors += 1
            if consecutive_errors >= TRADE_CONFIG.max_consecutive_errors:
                logger.log_warning("🚨 Too many consecutive errors, exiting.")
                break
            time.sleep(60)

if __name__ == "__main__":
    main()
