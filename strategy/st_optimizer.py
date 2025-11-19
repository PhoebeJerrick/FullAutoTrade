# strategy/strategy_optimizer.py
import json
import pandas as pd
from typing import Dict, Any, List
from datetime import datetime, timedelta
from strategy.st_config_manager import get_config_manager, StrategyConfig

class StrategyOptimizer:
    """
    策略优化器
    用于分析和优化止损止盈策略参数
    """
    
    def __init__(self, config_file: str = "strategy_config.json"):
        self.config_manager = get_config_manager(config_file)
        self.performance_history = []
    
    def backtest_parameters(self, trade_data: List[Dict], parameter_ranges: Dict[str, List]) -> Dict[str, Any]:
        """
        回测参数组合
        trade_data: 交易历史数据
        parameter_ranges: 参数范围，例如 {'atr_multiplier': [1.0, 1.5, 2.0], 'min_risk_reward': [1.0, 1.2, 1.5]}
        """
        best_params = {}
        best_performance = -float('inf')
        
        # 简单的网格搜索
        # 在实际应用中，可以使用更复杂的优化算法
        for atr_mult in parameter_ranges.get('atr_multiplier', [1.5]):
            for min_rr in parameter_ranges.get('min_risk_reward', [1.2]):
                for max_sl in parameter_ranges.get('max_stop_loss_ratio', [0.4]):
                    # 模拟使用这些参数的交易结果
                    performance = self._simulate_performance(trade_data, {
                        'atr_multiplier': atr_mult,
                        'min_risk_reward': min_rr,
                        'max_stop_loss_ratio': max_sl
                    })
                    
                    if performance > best_performance:
                        best_performance = performance
                        best_params = {
                            'atr_multiplier': atr_mult,
                            'min_risk_reward': min_rr,
                            'max_stop_loss_ratio': max_sl,
                            'performance': performance
                        }
        
        return best_params
    
    def _simulate_performance(self, trade_data: List[Dict], params: Dict[str, Any]) -> float:
        """模拟策略性能"""
        # 简化的性能计算
        # 在实际应用中，需要实现完整的回测逻辑
        total_return = 0
        winning_trades = 0
        
        for trade in trade_data:
            # 基于参数计算预期的交易结果
            # 这里需要根据实际交易数据进行计算
            pass
        
        if len(trade_data) > 0:
            win_rate = winning_trades / len(trade_data)
            return total_return * win_rate  # 简化的性能指标
        
        return 0
    
    def analyze_performance(self, symbol: str, period: str = "30d") -> Dict[str, Any]:
        """分析策略性能"""
        # 这里可以集成实际的数据分析逻辑
        # 暂时返回模拟数据
        return {
            'symbol': symbol,
            'period': period,
            'total_trades': 100,
            'win_rate': 0.65,
            'avg_profit': 0.023,
            'max_drawdown': 0.15,
            'sharpe_ratio': 1.8,
            'recommendations': [
                "考虑降低ATR乘数以减小止损距离",
                "在强势趋势中提高风险回报比目标",
                "优化多级止盈比例"
            ]
        }
    
    def generate_optimization_report(self, symbol: str) -> str:
        """生成优化报告"""
        analysis = self.analyze_performance(symbol)
        
        report = f"""
📊 策略优化报告 - {symbol}
⏰ 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

📈 性能指标:
   总交易数: {analysis['total_trades']}
   胜率: {analysis['win_rate']:.1%}
   平均盈利: {analysis['avg_profit']:.1%}
   最大回撤: {analysis['max_drawdown']:.1%}
   夏普比率: {analysis['sharpe_ratio']:.2f}

💡 优化建议:
"""
        for i, recommendation in enumerate(analysis['recommendations'], 1):
            report += f"   {i}. {recommendation}\n"
        
        return report

    def update_config_based_on_analysis(self, symbol: str, analysis: Dict[str, Any]) -> bool:
        """基于分析结果更新配置"""
        try:
            current_config = self.config_manager.current_config
            
            # 根据分析结果调整配置
            if analysis['win_rate'] < 0.6:
                # 胜率较低，考虑收紧止损
                current_config.stop_loss.atr_multiplier = max(1.2, current_config.stop_loss.atr_multiplier - 0.1)
            
            if analysis['max_drawdown'] > 0.2:
                # 回撤过大，降低最大止损比例
                current_config.stop_loss.max_stop_loss_ratio = max(0.3, current_config.stop_loss.max_stop_loss_ratio - 0.05)
            
            return self.config_manager.update_config(current_config)
        except Exception as e:
            print(f"❌ 基于分析更新配置失败: {e}")
            return False