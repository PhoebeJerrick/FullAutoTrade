import os
import time
import schedule
from openai import OpenAI
import ccxt
import pandas as pd
import re
from dotenv import load_dotenv
import json
import requests
from datetime import datetime, timedelta

# Use relative path
env_path = '../ExApiConfig/ExApiConfig.env'  # .env file in config folder of parent directory
load_dotenv(dotenv_path=env_path)

# Initialize DeepSeek client
deepseek_client = OpenAI(
    api_key=os.getenv('DEEPSEEK_API_KEY'),
    base_url="https://api.deepseek.com"
)

# Initialize OKX exchange
exchange = ccxt.okx({
    'options': {
        'defaultType': 'swap',  # OKX uses swap for perpetual contracts
    },
    'apiKey': os.getenv('OKX_API_KEY'),
    'secret': os.getenv('OKX_SECRET'),
    'password': os.getenv('OKX_PASSWORD'),  # OKX requires trading password
})

# Trading parameter configuration - combining advantages of both versions
TRADE_CONFIG = {
    'symbol': 'BTC/USDT:USDT',  # OKX contract symbol format
    'leverage': 10,  # Leverage multiplier, only affects margin not order value
    'timeframe': '15m',  # Use 15-minute K-line
    'test_mode': False,  # Test mode
    'data_points': 96,  # 24-hour data (96 15-minute K-lines)
    'analysis_periods': {
        'short_term': 20,  # Short-term moving average
        'medium_term': 50,  # Medium-term moving average
        'long_term': 96  # Long-term trend
    },
    # New intelligent position parameters
    'position_management': {
        'enable_intelligent_position': True,  # 🆕 New: Whether to enable intelligent position management
        'base_usdt_amount': 100,  # USDT base investment amount
        'high_confidence_multiplier': 1.5,
        'medium_confidence_multiplier': 1.0,
        'low_confidence_multiplier': 0.5,
        'max_position_ratio': 10,  # Maximum single position ratio
        'trend_strength_multiplier': 1.2
    }
}


def setup_exchange():
    """Set exchange parameters - force cross margin mode"""
    try:

        # First get contract specification information
        print("🔍 Getting BTC contract specifications...")
        markets = exchange.load_markets()
        btc_market = markets[TRADE_CONFIG['symbol']]

        # Get contract multiplier
        contract_size = float(btc_market['contractSize'])
        print(f"✅ Contract specification: 1 contract = {contract_size} BTC")

        # Store contract specification in global config
        TRADE_CONFIG['contract_size'] = contract_size
        TRADE_CONFIG['min_amount'] = btc_market['limits']['amount']['min']

        print(f"📏 Minimum trading volume: {TRADE_CONFIG['min_amount']} contracts")

        # First check existing positions
        print("🔍 Checking existing position mode...")
        positions = exchange.fetch_positions([TRADE_CONFIG['symbol']])

        has_isolated_position = False
        isolated_position_info = None

        for pos in positions:
            if pos['symbol'] == TRADE_CONFIG['symbol']:
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

        # 2. If there are isolated positions, prompt and exit
        if has_isolated_position:
            print("❌ Detected isolated positions, program cannot continue!")
            print(f"📊 Isolated position details:")
            print(f"   - Direction: {isolated_position_info['side']}")
            print(f"   - Quantity: {isolated_position_info['size']}")
            print(f"   - Entry price: {isolated_position_info['entry_price']}")
            print(f"   - Mode: {isolated_position_info['mode']}")
            print("\n🚨 Solutions:")
            print("1. Manually close all isolated positions")
            print("2. Or convert isolated positions to cross margin mode")
            print("3. Then restart the program")
            return False

        # 3. Set one-way position mode
        print("🔄 Setting one-way position mode...")
        try:
            exchange.set_position_mode(False, TRADE_CONFIG['symbol'])  # False means one-way position
            print("✅ One-way position mode set")
        except Exception as e:
            print(f"⚠️ Failed to set one-way position mode (may already be set): {e}")

        # 4. Set cross margin mode and leverage
        print("⚙️ Setting cross margin mode and leverage...")
        exchange.set_leverage(
            TRADE_CONFIG['leverage'],
            TRADE_CONFIG['symbol'],
            {'mgnMode': 'cross'}  # Force cross margin mode
        )
        print(f"✅ Cross margin mode set, leverage: {TRADE_CONFIG['leverage']}x")

        # 5. Verify settings
        print("🔍 Verifying account settings...")
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']
        print(f"💰 Current USDT balance: {usdt_balance:.2f}")

        # Get current position status
        current_pos = get_current_position()
        if current_pos:
            print(f"📦 Current position: {current_pos['side']} position {current_pos['size']} contracts")
        else:
            print("📦 No current position")

        print("🎯 Program configuration completed: Cross margin mode + One-way position")
        return True

    except Exception as e:
        print(f"❌ Exchange setup failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# Global variables to store historical data
price_history = []
signal_history = []
position = None


def calculate_intelligent_position(signal_data, price_data, current_position):
    """Calculate intelligent position size - fixed version"""
    config = TRADE_CONFIG['position_management']

    # 🆕 New: If intelligent position is disabled, use fixed position
    if not config.get('enable_intelligent_position', True):
        fixed_contracts = 0.1  # Fixed position size, can be adjusted as needed
        print(f"🔧 Intelligent position disabled, using fixed position: {fixed_contracts} contracts")
        return fixed_contracts

    try:
        # Get account balance
        balance = exchange.fetch_balance()
        usdt_balance = balance['USDT']['free']

        # Base USDT investment
        base_usdt = config['base_usdt_amount']
        print(f"💰 Available USDT balance: {usdt_balance:.2f}, base investment {base_usdt}")

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
        contract_size = (final_usdt) / (price_data['price'] * TRADE_CONFIG['contract_size'])

        print(f"📊 Position calculation details:")
        print(f"   - Base USDT: {base_usdt}")
        print(f"   - Confidence multiplier: {confidence_multiplier}")
        print(f"   - Trend multiplier: {trend_multiplier}")
        print(f"   - RSI multiplier: {rsi_multiplier}")
        print(f"   - Suggested USDT: {suggested_usdt:.2f}")
        print(f"   - Final USDT: {final_usdt:.2f}")
        print(f"   - Contract multiplier: {TRADE_CONFIG['contract_size']}")
        print(f"   - Calculated contracts: {contract_size:.4f} contracts")

        # Precision handling: OKX BTC contract minimum trading unit is 0.01 contracts
        contract_size = round(contract_size, 2)  # Keep 2 decimal places

        # Ensure minimum trading volume
        min_contracts = TRADE_CONFIG.get('min_amount', 0.01)
        if contract_size < min_contracts:
            contract_size = min_contracts
            print(f"⚠️ Position less than minimum, adjusted to: {contract_size} contracts")

        print(f"🎯 Final position: {final_usdt:.2f} USDT → {contract_size:.2f} contracts")
        return contract_size

    except Exception as e:
        print(f"❌ Position calculation failed, using base position: {e}")
        # Emergency backup calculation
        base_usdt = config['base_usdt_amount']
        contract_size = (base_usdt * TRADE_CONFIG['leverage']) / (
                    price_data['price'] * TRADE_CONFIG.get('contract_size', 0.01))
        return round(max(contract_size, TRADE_CONFIG.get('min_amount', 0.01)), 2)


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
        print(f"Technical indicator calculation failed: {e}")
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
        print(f"Support resistance calculation failed: {e}")
        return {}


def get_sentiment_indicators():
    """Get sentiment indicators - simplified version"""
    try:
        API_URL = "https://service.cryptoracle.network/openapi/v2/endpoint"
        API_KEY = "7ad48a56-8730-4238-a714-eebc30834e3e"

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

                        print(f"✅ Using sentiment data time: {period['startTime']} (Delay: {data_delay} minutes)")

                        return {
                            'positive_ratio': positive,
                            'negative_ratio': negative,
                            'net_sentiment': net_sentiment,
                            'data_time': period['startTime'],
                            'data_delay_minutes': data_delay
                        }

                print("❌ All time period data is empty")
                return None

        return None
    except Exception as e:
        print(f"Sentiment indicator acquisition failed: {e}")
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
        print(f"Trend analysis failed: {e}")
        return {}


def get_btc_ohlcv_enhanced():
    """Enhanced version: Get BTC K-line data and calculate technical indicators"""
    try:
        # Get K-line data
        ohlcv = exchange.fetch_ohlcv(TRADE_CONFIG['symbol'], TRADE_CONFIG['timeframe'],
                                     limit=TRADE_CONFIG['data_points'])

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
            'timeframe': TRADE_CONFIG['timeframe'],
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
        print(f"Enhanced K-line data acquisition failed: {e}")
        return None


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
        positions = exchange.fetch_positions([TRADE_CONFIG['symbol']])

        for pos in positions:
            if pos['symbol'] == TRADE_CONFIG['symbol']:
                contracts = float(pos['contracts']) if pos['contracts'] else 0

                if contracts > 0:
                    return {
                        'side': pos['side'],  # 'long' or 'short'
                        'size': contracts,
                        'entry_price': float(pos['entryPrice']) if pos['entryPrice'] else 0,
                        'unrealized_pnl': float(pos['unrealizedPnl']) if pos['unrealizedPnl'] else 0,
                        'leverage': float(pos['leverage']) if pos['leverage'] else TRADE_CONFIG['leverage'],
                        'symbol': pos['symbol']
                    }

        return None

    except Exception as e:
        print(f"Position acquisition failed: {e}")
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
            print(f"JSON parsing failed, original content: {json_str}")
            print(f"Error details: {e}")
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


def analyze_with_deepseek(price_data):
    """Use DeepSeek to analyze market and generate trading signals (enhanced version)"""

    # Generate technical analysis text
    technical_analysis = generate_technical_analysis_text(price_data)

    # Build K-line data text
    kline_text = f"【Recent 5 {TRADE_CONFIG['timeframe']} K-line Data】\n"
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
    You are a professional cryptocurrency trading analyst. Please analyze based on the following BTC/USDT {TRADE_CONFIG['timeframe']} period data:

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
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system",
                 "content": f"You are a professional trader focusing on {TRADE_CONFIG['timeframe']} period trend analysis. Please make judgments combining K-line patterns and technical indicators, and strictly follow JSON format requirements."},
                {"role": "user", "content": prompt}
            ],
            stream=False,
            temperature=0.1
        )

        # Safely parse JSON
        result = response.choices[0].message.content
        print(f"DeepSeek original reply: {result}")

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
        signal_history.append(signal_data)
        if len(signal_history) > 30:
            signal_history.pop(0)

        # Signal statistics
        signal_count = len([s for s in signal_history if s.get('signal') == signal_data['signal']])
        total_signals = len(signal_history)
        print(f"Signal statistics: {signal_data['signal']} (Appeared {signal_count} times in recent {total_signals} signals)")

        # Signal continuity check
        if len(signal_history) >= 3:
            last_three = [s['signal'] for s in signal_history[-3:]]
            if len(set(last_three)) == 1:
                print(f"⚠️ Note: Consecutive 3 {signal_data['signal']} signals")

        return signal_data

    except Exception as e:
        print(f"DeepSeek analysis failed: {e}")
        return create_fallback_signal(price_data)


def execute_intelligent_trade(signal_data, price_data):
    """Execute intelligent trading - OKX version (supports same direction position increase/decrease)"""
    global position

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
        #         print(f"🔒 Non-high confidence reversal signal, maintain existing {current_side} position")
        #         return

        #     if len(signal_history) >= 2:
        #         last_signals = [s['signal'] for s in signal_history[-2:]]
        #         if signal_data['signal'] in last_signals:
        #             print(f"🔒 Recently appeared {signal_data['signal']} signal, avoid frequent reversal")
        #             return

    # Calculate intelligent position
    position_size = calculate_intelligent_position(signal_data, price_data, current_position)

    print(f"Trading signal: {signal_data['signal']}")
    print(f"Confidence level: {signal_data['confidence']}")
    print(f"Intelligent position: {position_size:.2f} contracts")
    print(f"Reason: {signal_data['reason']}")
    print(f"Current position: {current_position}")

    # Risk management
    if signal_data['confidence'] == 'LOW' and not TRADE_CONFIG['test_mode']:
        print("⚠️ Low confidence signal, skipping execution")
        return

    if TRADE_CONFIG['test_mode']:
        print("Test mode - simulated trading only")
        return

    try:
        # Execute trading logic - supports same direction position increase/decrease
        if signal_data['signal'] == 'BUY':
            if current_position and current_position['side'] == 'short':
                # First check if short position actually exists and quantity is correct
                if current_position['size'] > 0:
                    print(f"Closing short position {current_position['size']:.2f} contracts and opening long position {position_size:.2f} contracts...")
                    # Close short position
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'buy',
                        current_position['size'],
                        params={'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
                    )
                    time.sleep(1)
                    # Open long position
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'buy',
                        position_size,
                        params={'tag': '60bb4a8d3416BCDE'}
                    )
                else:
                    print("⚠️ Detected short position but quantity is 0, directly opening long position")
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'buy',
                        position_size,
                        params={'tag': '60bb4a8d3416BCDE'}
                    )

            elif current_position and current_position['side'] == 'long':
                # Same direction, check if position adjustment needed
                size_diff = position_size - current_position['size']

                if abs(size_diff) >= 0.01:  # Adjustable difference exists
                    if size_diff > 0:
                        # Increase position
                        add_size = round(size_diff, 2)
                        print(
                            f"Long position increase {add_size:.2f} contracts (Current:{current_position['size']:.2f} → Target:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG['symbol'],
                            'buy',
                            add_size,
                            params={'tag': '60bb4a8d3416BCDE'}
                        )
                    else:
                        # Decrease position
                        reduce_size = round(abs(size_diff), 2)
                        print(
                            f"Long position decrease {reduce_size:.2f} contracts (Current:{current_position['size']:.2f} → Target:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG['symbol'],
                            'sell',
                            reduce_size,
                            params={'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
                        )
                else:
                    print(
                        f"Existing long position, position appropriate maintaining status (Current:{current_position['size']:.2f}, Target:{position_size:.2f})")
            else:
                # Open long position when no position
                print(f"Opening long position {position_size:.2f} contracts...")
                exchange.create_market_order(
                    TRADE_CONFIG['symbol'],
                    'buy',
                    position_size,
                    params={'tag': '60bb4a8d3416BCDE'}
                )

        elif signal_data['signal'] == 'SELL':
            if current_position and current_position['side'] == 'long':
                # First check if long position actually exists and quantity is correct
                if current_position['size'] > 0:
                    print(f"Closing long position {current_position['size']:.2f} contracts and opening short position {position_size:.2f} contracts...")
                    # Close long position
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'sell',
                        current_position['size'],
                        params={'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
                    )
                    time.sleep(1)
                    # Open short position
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'sell',
                        position_size,
                        params={'tag': '60bb4a8d3416BCDE'}
                    )
                else:
                    print("⚠️ Detected long position but quantity is 0, directly opening short position")
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'sell',
                        position_size,
                        params={'tag': '60bb4a8d3416BCDE'}
                    )

            elif current_position and current_position['side'] == 'short':
                # Same direction, check if position adjustment needed
                size_diff = position_size - current_position['size']

                if abs(size_diff) >= 0.01:  # Adjustable difference exists
                    if size_diff > 0:
                        # Increase position
                        add_size = round(size_diff, 2)
                        print(
                            f"Short position increase {add_size:.2f} contracts (Current:{current_position['size']:.2f} → Target:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG['symbol'],
                            'sell',
                            add_size,
                            params={'tag': '60bb4a8d3416BCDE'}
                        )
                    else:
                        # Decrease position
                        reduce_size = round(abs(size_diff), 2)
                        print(
                            f"Short position decrease {reduce_size:.2f} contracts (Current:{current_position['size']:.2f} → Target:{position_size:.2f})")
                        exchange.create_market_order(
                            TRADE_CONFIG['symbol'],
                            'buy',
                            reduce_size,
                            params={'reduceOnly': True, 'tag': '60bb4a8d3416BCDE'}
                        )
                else:
                    print(
                        f"Existing short position, position appropriate maintaining status (Current:{current_position['size']:.2f}, Target:{position_size:.2f})")
            else:
                # Open short position when no position
                print(f"Opening short position {position_size:.2f} contracts...")
                exchange.create_market_order(
                    TRADE_CONFIG['symbol'],
                    'sell',
                    position_size,
                    params={'tag': '60bb4a8d3416BCDE'}
                )

        elif signal_data['signal'] == 'HOLD':
            print("Suggest observing, no trade execution")
            return

        print("Intelligent trading executed successfully")
        time.sleep(2)
        position = get_current_position()
        print(f"Updated position: {position}")

    except Exception as e:
        print(f"Trade execution failed: {e}")

        # If it's a position doesn't exist error, try to directly open new position
        if "don't have any positions" in str(e):
            print("Attempting to directly open new position...")
            try:
                if signal_data['signal'] == 'BUY':
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'buy',
                        position_size,
                        params={'tag': '60bb4a8d3416BCDE'}
                    )
                elif signal_data['signal'] == 'SELL':
                    exchange.create_market_order(
                        TRADE_CONFIG['symbol'],
                        'sell',
                        position_size,
                        params={'tag': '60bb4a8d3416BCDE'}
                    )
                print("Direct position opening successful")
            except Exception as e2:
                print(f"Direct position opening also failed: {e2}")

        import traceback
        traceback.print_exc()


def analyze_with_deepseek_with_retry(price_data, max_retries=2):
    """DeepSeek analysis with retry"""
    for attempt in range(max_retries):
        try:
            signal_data = analyze_with_deepseek(price_data)
            if signal_data and not signal_data.get('is_fallback', False):
                return signal_data

            print(f"Attempt {attempt + 1} failed, retrying...")
            time.sleep(1)

        except Exception as e:
            print(f"Attempt {attempt + 1} exception: {e}")
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

    # Display friendly waiting time
    display_minutes = minutes_to_wait - 1 if current_second > 0 else minutes_to_wait
    display_seconds = 60 - current_second if current_second > 0 else 0

    if display_minutes > 0:
        print(f"🕒 Waiting {display_minutes} minutes {display_seconds} seconds until mark...")
    else:
        print(f"🕒 Waiting {display_seconds} seconds until mark...")

    return seconds_to_wait


def trading_bot():
    # Wait until mark before executing
    wait_seconds = wait_for_next_period()
    if wait_seconds > 0:
        time.sleep(wait_seconds)

    """Main trading bot function"""
    print("\n" + "=" * 60)
    print(f"Execution time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. Get enhanced K-line data
    price_data = get_btc_ohlcv_enhanced()
    if not price_data:
        return

    print(f"BTC current price: ${price_data['price']:,.2f}")
    print(f"Data period: {TRADE_CONFIG['timeframe']}")
    print(f"Price change: {price_data['price_change']:+.2f}%")

    # 2. Use DeepSeek analysis (with retry)
    signal_data = analyze_with_deepseek_with_retry(price_data)

    if signal_data.get('is_fallback', False):
        print("⚠️ Using backup trading signal")

    # 3. Execute intelligent trading
    execute_intelligent_trade(signal_data, price_data)


def main():
    """Main function"""
    print("BTC/USDT OKX automatic trading bot started successfully!")
    print("Combining technical indicator strategy + OKX live trading interface")

    if TRADE_CONFIG['test_mode']:
        print("Currently in simulation mode, no real orders will be placed")
    else:
        print("Live trading mode, please operate carefully!")

    print(f"Trading period: {TRADE_CONFIG['timeframe']}")
    print("Complete technical indicator analysis and position tracking enabled")

    # Setup exchange
    if not setup_exchange():
        print("Exchange initialization failed, program exiting")
        return

    print("Execution frequency: Every 15 minutes at mark")

    # Loop execution (not using schedule)
    while True:
        trading_bot()  # Function will wait for mark internally

        # Wait for a while after execution before checking again (avoid frequent loops)
        time.sleep(60)  # Check every minute


if __name__ == "__main__":
    main()
