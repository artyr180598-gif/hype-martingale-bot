"""Strategies package."""
from src.strategies.base import BaseStrategy, StrategySignal
from src.strategies.breakout import BreakoutStrategy
from src.strategies.funding_squeeze import FundingSqueezeStrategy
from src.strategies.liquidity_sweep import LiquiditySweepStrategy
from src.strategies.mean_reversion import MeanReversionStrategy
from src.strategies.order_flow_alpha import OrderFlowAlphaStrategy
from src.strategies.registry import StrategyRegistry
from src.strategies.trend_following import TrendFollowingStrategy

# Auto-register strategies into registry
StrategyRegistry.register(TrendFollowingStrategy())
StrategyRegistry.register(BreakoutStrategy())
StrategyRegistry.register(MeanReversionStrategy())
StrategyRegistry.register(OrderFlowAlphaStrategy())
StrategyRegistry.register(FundingSqueezeStrategy())
StrategyRegistry.register(LiquiditySweepStrategy())

__all__ = [
    "BaseStrategy",
    "BreakoutStrategy",
    "FundingSqueezeStrategy",
    "LiquiditySweepStrategy",
    "MeanReversionStrategy",
    "OrderFlowAlphaStrategy",
    "StrategyRegistry",
    "StrategySignal",
    "TrendFollowingStrategy",
]
