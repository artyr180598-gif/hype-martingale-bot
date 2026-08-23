"""
Central Feature Registry and Metadata Catalogue.
"""
from dataclasses import dataclass
from enum import Enum


class FeatureCategory(str, Enum):
    TECHNICAL = "TECHNICAL"
    MARKET_STRUCTURE = "MARKET_STRUCTURE"
    LIQUIDITY = "LIQUIDITY"
    ORDER_FLOW = "ORDER_FLOW"
    DERIVATIVES = "DERIVATIVES"
    VOLATILITY = "VOLATILITY"
    CORRELATION = "CORRELATION"
    BREADTH = "BREADTH"
    SENTIMENT = "SENTIMENT"


@dataclass
class FeatureDefinition:
    name: str
    category: FeatureCategory
    description: str
    lookback: int
    normalization: str  # "z_score", "percentile", "ratio", "binary", "none"
    formula_summary: str


class FeatureRegistry:
    """Registry documenting all calculated features across the platform."""

    _registry: dict[str, FeatureDefinition] = {}

    @classmethod
    def register(cls, feat: FeatureDefinition) -> None:
        cls._registry[feat.name] = feat

    @classmethod
    def get(cls, name: str) -> FeatureDefinition | None:
        return cls._registry.get(name)

    @classmethod
    def list_all(cls) -> list[FeatureDefinition]:
        return list(cls._registry.values())

    @classmethod
    def list_by_category(cls, category: FeatureCategory) -> list[FeatureDefinition]:
        return [f for f in cls._registry.values() if f.category == category]


# Register Standard Features
STANDARD_FEATURES = [
    FeatureDefinition("ema_9_21_cross", FeatureCategory.TECHNICAL, "EMA 9 / 21 Trend alignment", 21, "binary", "EMA(9) - EMA(21)"),
    FeatureDefinition("rsi_14", FeatureCategory.TECHNICAL, "Relative Strength Index 14", 14, "ratio", "RSI(14) / 100"),
    FeatureDefinition("adx_14", FeatureCategory.TECHNICAL, "Average Directional Index Trend Strength", 14, "ratio", "ADX(14) / 100"),
    FeatureDefinition("vwap_distance_pct", FeatureCategory.TECHNICAL, "Price deviation from Session VWAP", 50, "z_score", "(Close - VWAP) / VWAP * 100"),
    FeatureDefinition("bb_width_percentile", FeatureCategory.VOLATILITY, "Bollinger Bandwidth Percentile", 100, "percentile", "PercentileRank(BB_Width, 100)"),
    FeatureDefinition("atr_percentile", FeatureCategory.VOLATILITY, "ATR Percentile (Volatility Rank)", 100, "percentile", "PercentileRank(ATR, 100)"),
    FeatureDefinition("structure_state", FeatureCategory.MARKET_STRUCTURE, "Bullish (1), Bearish (-1), Range (0)", 50, "binary", "HH/HL vs LH/LL swing analysis"),
    FeatureDefinition("bos_detected", FeatureCategory.MARKET_STRUCTURE, "Break of structure trigger", 20, "binary", "Close > Recent Swing High or Close < Recent Swing Low"),
    FeatureDefinition("liquidity_sweep", FeatureCategory.LIQUIDITY, "Wick sweep beyond key high/low with reversal", 20, "binary", "Wick beyond swing + close inside"),
    FeatureDefinition("orderbook_imbalance", FeatureCategory.ORDER_FLOW, "Top 20 depth imbalance ratio", 1, "ratio", "(Bid_USD - Ask_USD) / Total_USD"),
    FeatureDefinition("cvd_slope", FeatureCategory.ORDER_FLOW, "Cumulative Volume Delta 20-bar slope", 20, "z_score", "LinearRegressionSlope(CVD, 20)"),
    FeatureDefinition("funding_z_score", FeatureCategory.DERIVATIVES, "Funding rate Z-score vs 30-day history", 90, "z_score", "(Funding - Mean) / StdDev"),
    FeatureDefinition("oi_delta_24h_pct", FeatureCategory.DERIVATIVES, "Open Interest 24-hour change percentage", 24, "z_score", "(OI_now - OI_24h_ago) / OI_24h_ago * 100"),
    FeatureDefinition("btc_correlation_30d", FeatureCategory.CORRELATION, "30-day Rolling Pearson Correlation with BTC", 30, "ratio", "Corr(Asset_returns, BTC_returns)"),
    FeatureDefinition("market_breadth_above_ema50", FeatureCategory.BREADTH, "Percentage of tracked coins above EMA 50", 1, "ratio", "Count(Price > EMA50) / Total_Coins"),
]

for feat in STANDARD_FEATURES:
    FeatureRegistry.register(feat)
