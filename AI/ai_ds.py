import os
import json
import re
import time
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import requests
from openai import OpenAI
import pandas as pd


# 导入日志模块
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trade_logger import logger

class DeepSeekAnalyzer:
    """DeepSeek 市场分析器"""
    
    def __init__(self, config: Any):
        """
        初始化 DeepSeek 分析器
        
        Args:
            config: 交易配置对象，需要包含以下属性：
                   - deepseek_base_url: DeepSeek API 地址
                   - sentiment_api_url: 情绪数据API地址
                   - sentiment_api_key: 情绪数据API密钥
                   - timeframe: 时间帧
        """
        self.config = config
        self.client = None
        self._initialize_client()
    
    def _initialize_client(self):
        """初始化 DeepSeek 客户端"""
        try:
            api_key = os.getenv('DEEPSEEK_API_KEY')
            if not api_key:
                raise ValueError("DEEPSEEK_API_KEY environment variable is not set")
            
            self.client = OpenAI(
                api_key=api_key,
                base_url=self.config.deepseek_base_url
            )
            logger.log_info("DeepSeek client initialized successfully")
        except Exception as e:
            logger.log_error("deepseek_client_init", str(e))
            raise
    
    def get_sentiment_indicators(self, symbol: str) -> Optional[Dict]:
        """获取情绪指标数据"""
        try:
            API_URL = self.config.sentiment_api_url
            API_KEY = self.config.sentiment_api_key

            # 从 symbol 中提取币种名称
            base_currency = symbol.split('/')[0].upper()
            
            # Get recent 4-hour data
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=4)

            request_body = {
                "apiKey": API_KEY,
                "endpoints": ["CO-A-02-01", "CO-A-02-02"],
                "startTime": start_time.strftime("%Y-%m-%d %H:%M:%S"),
                "endTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                "timeType": "15m",
                "token": [base_currency]
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

                            if value:
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

                            logger.log_warning(f"✅ 使用情绪数据时间: {period['startTime']} (延迟: {data_delay} 分钟)")

                            return {
                                'positive_ratio': positive,
                                'negative_ratio': negative,
                                'net_sentiment': net_sentiment,
                                'data_time': period['startTime'],
                                'data_delay_minutes': data_delay
                            }

                logger.log_warning(f"❌ 所有时间段数据为空")
                return None

            return None
        except Exception as e:
            logger.log_error(f"sentiment_data", str(e))
            return None

    def generate_technical_analysis_text(self, price_data: Dict) -> str:
        """生成技术分析文本"""
        if 'technical_data' not in price_data:
            return "Technical indicator data unavailable"

        tech = price_data['technical_data']
        trend = price_data.get('trend_analysis', {})
        levels = price_data.get('levels_analysis', {})

        # Check data validity
        def safe_float(value, default=0):
            return float(value) if value and pd.notna(value) else default

        analysis_text = f"""
        【技术指标概览】
        📈 趋势: {trend.get('overall', 'N/A')} | RSI: {safe_float(tech['rsi']):.1f}
        📊 均线: 5期{tech.get('sma_5', 0):.2f} | 20期{tech.get('sma_20', 0):.2f} | 50期{tech.get('sma_50', 0):.2f}
        🎯 关键位: 阻力{levels.get('static_resistance', 0):.2f} | 支撑{levels.get('static_support', 0):.2f}
        """
        return analysis_text

    def safe_json_parse(self, json_str: str) -> Optional[Dict]:
        """安全解析 JSON，处理非标准格式情况"""
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            try:
                # Fix common JSON format issues
                json_str = json_str.replace("'", '"')
                json_str = re.sub(r'(\w+):', r'"\1":', json_str)
                json_str = re.sub(r',\s*}', '}', json_str)
                json_str = re.sub(r',\s*]', ']', json_str)
                # 修复：移除数字中的逗号（如 106,600 -> 106600）
                json_str = re.sub(r'(\d),(\d)', r'\1\2', json_str)
                return json.loads(json_str)
            except json.JSONDecodeError as e:
                logger.log_error("json_parsing", f"Failed to parse: {json_str}")
                logger.log_error("json_parsing", f"Error details: {e}")
                return None

    def create_fallback_signal(self, price_data: Dict) -> Dict:
        """创建备用交易信号"""
        return {
            "signal": "HOLD",
            "reason": "Conservative strategy adopted due to temporary unavailability of technical analysis",
            "stop_loss": price_data['price'] * 0.98,  # -2%
            "take_profit": price_data['price'] * 1.02,  # +2%
            "confidence": "LOW",
            "is_fallback": True
        }

    def analyze_market(self, symbol: str, price_data: Dict, signal_history: list, 
                      current_position: Optional[Dict] = None) -> Dict:
        """
        使用 DeepSeek 分析市场并生成交易信号
        
        Args:
            symbol: 交易品种
            price_data: 价格数据字典
            signal_history: 信号历史记录
            current_position: 当前持仓信息
            
        Returns:
            交易信号字典
        """
        try:
            # Generate technical analysis text
            technical_analysis = self.generate_technical_analysis_text(price_data)

            # Build K-line data text
            kline_text = f"【Recent 5 {self.config.timeframe} K-line Data】\n"
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
            sentiment_data = self.get_sentiment_indicators(symbol)
            if sentiment_data:
                sign = '+' if sentiment_data['net_sentiment'] >= 0 else ''
                sentiment_text = f"【Market Sentiment】Optimistic {sentiment_data['positive_ratio']:.1%} Pessimistic {sentiment_data['negative_ratio']:.1%} Net {sign}{sentiment_data['net_sentiment']:.3f}"
            else:
                sentiment_text = "【Market Sentiment】Data temporarily unavailable"

            # Add current position information
            base_currency = symbol.split('/')[0]
            position_text = "No position" if not current_position else f"{current_position['side']} position, Quantity: {current_position['size']}, P&L: {current_position['unrealized_pnl']:.2f}USDT"
            pnl_text = f", Position P&L: {current_position['unrealized_pnl']:.2f} USDT" if current_position else ""

            # Enhanced Trend Reversal Analysis Criteria
            trend_reversal_criteria = f"""
            【Trend Reversal Judgment Criteria - Must meet at least 2 conditions】
            1. Price breaks through key support/resistance levels + volume amplification
            2. Break of major moving averages (e.g., 20-period, 50-period)  
            3. RSI reversal from overbought/oversold areas and forms divergence
            4. MACD shows clear death cross/golden cross signal

            【Position Management Principles】
            - Existing position opposite to current signal → Strongly consider closing position
            - Existing position same as current signal → Continue holding, check stop loss
            - Signal is HOLD but position exists → Decide whether to hold based on technical indicators

            【Key Technical Levels for {base_currency}】
            - Strong Resistance: When price approaches recent high + Bollinger Band upper
            - Strong Support: When price approaches recent low + Bollinger Band lower
            - Breakout Confirmation: Requires closing price break + volume > 20-period average
            - False Breakout: Price breaks but fails to sustain, immediately reverses
            """

            prompt = f"""
            You are a professional cryptocurrency trading analyst. Please analyze based on the following {base_currency} {self.config.timeframe} period data:

            {kline_text}

            {technical_analysis}

            {signal_text}

            {sentiment_text}

            【Current Market】
            - Current price: ${price_data['price']:,.2f}
            - Time: {price_data['timestamp']}
            - Current K-line high: ${price_data['high']:,.2f}
            - Current K-line low: ${price_data['low']:,.2f}
            - Current K-line volume: {price_data['volume']:.2f} {symbol}
            - Price change: {price_data['price_change']:+.2f}%
            - Current position: {position_text}{pnl_text}

            {trend_reversal_criteria}

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
            5. Because trading coins like btc, long position weight can be slightly higher
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

            response = self.client.chat.completions.create(
                model="deepseek-chat",
                messages=[
                    {
                        "role": "system",
                        "content": f"""You are a professional trader specializing in {self.config.timeframe} period trend analysis and trend reversal detection. 
                        Key Responsibilities:
                        1. Analyze trend strength and identify potential reversal points
                        2. Use multiple confirmation criteria for trend reversals
                        3. Provide clear trading signals based on technical analysis
                        4. Consider existing positions in your analysis
                        5. Strictly follow JSON format requirements

                        Trend Reversal Focus:
                        - Pay special attention to breakouts of key support/resistance levels
                        - Look for confirmation from multiple indicators (RSI divergence, MACD cross, volume)
                        - Consider the broader market context in your analysis"""
                    },
                    {"role": "user", "content": prompt}
                ],
                stream=False,
                temperature=0.1
            )

            # Safely parse JSON
            result = response.choices[0].message.content.strip()

            # 关键：清理非法引号
            cleaned_content = re.sub(r'(\d+)-"(\w+)"', r'\1-\2', result)
            cleaned_content = re.sub(r'"(\w+)"-(\d+)', r'\1-\2', cleaned_content)

            # Extract JSON part
            start_idx = cleaned_content.find('{')
            end_idx = cleaned_content.rfind('}') + 1

            if start_idx != -1 and end_idx != 0:
                json_str = cleaned_content[start_idx:end_idx]
                signal_data = self.safe_json_parse(json_str)

                if signal_data is None:
                    signal_data = self.create_fallback_signal(price_data)
            else:
                signal_data = self.create_fallback_signal(price_data)

            # Verify required fields
            required_fields = ['signal', 'reason', 'stop_loss', 'take_profit', 'confidence']
            if not all(field in signal_data for field in required_fields):
                signal_data = self.create_fallback_signal(price_data)

            # 新增逻辑: 检查信号，如果不是 HOLD，则打印 DeepSeek 原始回复
            if signal_data and signal_data.get('signal') != 'HOLD':
                logger.log_info(f"DeepSeek original reply: {result}")

            # 添加时间戳
            signal_data['timestamp'] = price_data['timestamp']

            return signal_data

        except Exception as e:
            logger.log_error("deepseek_analysis", f"DeepSeek分析失败: {str(e)}")
            return self.create_fallback_signal(price_data)

# 全局 DeepSeek 分析器实例
_global_analyzer = None

def get_deepseek_analyzer(config: Any) -> DeepSeekAnalyzer:
    """获取全局 DeepSeek 分析器实例"""
    global _global_analyzer
    if _global_analyzer is None:
        _global_analyzer = DeepSeekAnalyzer(config)
    return _global_analyzer

def analyze_with_deepseek(symbol: str, price_data: Dict, signal_history: list, 
                         current_position: Optional[Dict] = None, config: Any = None) -> Dict:
    """
    使用 DeepSeek 分析市场的便捷函数
    
    Args:
        symbol: 交易品种
        price_data: 价格数据
        signal_history: 信号历史
        current_position: 当前持仓
        config: 交易配置
        
    Returns:
        交易信号字典
    """
    if config is None:
        raise ValueError("Config is required for DeepSeek analysis")
    
    analyzer = get_deepseek_analyzer(config)
    return analyzer.analyze_market(symbol, price_data, signal_history, current_position)