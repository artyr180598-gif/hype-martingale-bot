"""
Global Constants for Quantitative Crypto Futures Intelligence Platform.
"""
from enum import Enum


class ExchangeId(str, Enum):
    BINANCE = "binance"
    BYBIT = "bybit"
    OKX = "okx"
    SIMULATED = "simulated"


class Timeframe(str, Enum):
    M1 = "1m"
    M3 = "3m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H2 = "2h"
    H4 = "4h"
    H6 = "6h"
    H12 = "12h"
    D1 = "1d"


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NO_TRADE = "NO_TRADE"


class SignalTier(str, Enum):
    EXTREME = "EXTREME"       # 90-100
    STRONG = "STRONG"         # 80-89
    VALID = "VALID"           # 70-79
    WATCH = "WATCH"           # 60-69
    NO_TRADE = "NO_TRADE"     # < 60


class MarketRegimeType(str, Enum):
    STRONG_UPTREND = "STRONG_UPTREND"
    WEAK_UPTREND = "WEAK_UPTREND"
    RANGE = "RANGE"
    WEAK_DOWNTREND = "WEAK_DOWNTREND"
    STRONG_DOWNTREND = "STRONG_DOWNTREND"
    HIGH_VOLATILITY_RANGE = "HIGH_VOLATILITY_RANGE"
    PANIC = "PANIC"
    EUPHORIA = "EUPHORIA"
    BREAKOUT = "BREAKOUT"
    BREAKDOWN = "BREAKDOWN"
    ACCUMULATION = "ACCUMULATION"
    DISTRIBUTION = "DISTRIBUTION"
    UNKNOWN = "UNKNOWN"


class VolatilityRegimeType(str, Enum):
    VERY_LOW_VOLATILITY = "VERY_LOW_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    NORMAL = "NORMAL"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    EXTREME_VOLATILITY = "EXTREME_VOLATILITY"


class VolatilityTrend(str, Enum):
    VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
    VOLATILITY_CONTRACTION = "VOLATILITY_CONTRACTION"
    STABLE = "STABLE"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"


class EntryType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    BREAKOUT = "BREAKOUT"
    RETEST = "RETEST"
    PULLBACK = "PULLBACK"


class StrategyStatus(str, Enum):
    EXPERIMENTAL = "EXPERIMENTAL"
    VALIDATING = "VALIDATING"
    PAPER = "PAPER"
    CANDIDATE = "CANDIDATE"
    PRODUCTION = "PRODUCTION"
    DISABLED = "DISABLED"


class NewsSentiment(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    UNKNOWN = "UNKNOWN"


class NewsImpact(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RiskProfile(str, Enum):
    CONSERVATIVE = "CONSERVATIVE"
    BALANCED = "BALANCED"
    AGGRESSIVE = "AGGRESSIVE"


class DataQualityStatus(str, Enum):
    EXCELLENT = "EXCELLENT"   # 100% complete, no gaps
    GOOD = "GOOD"             # minor lag < 1 interval
    DEGRADED = "DEGRADED"     # missing bars or stale data
    INVALID = "INVALID"       # corrupt data


# Default Scoring Weights (0-100 total)
DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "trend": 15.0,
    "market_structure": 15.0,
    "order_flow": 15.0,
    "volatility": 10.0,
    "open_interest": 10.0,
    "volume": 10.0,
    "momentum": 10.0,
    "funding": 5.0,
    "liquidations": 5.0,
    "market_breadth": 5.0,
    "sentiment": 5.0,
}

# Major trading pairs monitored by default
DEFAULT_TRACKED_SYMBOLS: list[str] = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "AVAXUSDT",
    "LINKUSDT",
    "SUIUSDT",
]

# Timeframe to millisecond conversion
TIMEFRAME_MS: dict[str, int] = {
    "1m": 60 * 1000,
    "3m": 3 * 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "2h": 2 * 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "6h": 6 * 60 * 60 * 1000,
    "12h": 12 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}
