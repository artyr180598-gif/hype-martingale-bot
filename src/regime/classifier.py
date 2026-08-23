"""
Multi-Factor Market Regime Classifier.
"""
from typing import Any

from src.config.constants import MarketRegimeType, VolatilityRegimeType, VolatilityTrend
from src.core.logging import get_logger
from src.regime.models import MarketRegimeReport

logger = get_logger("regime.classifier")


class MarketRegimeClassifier:
    """
    Synthesizes technical trend, market structure, volatility, volume, and derivatives
    to classify the holistic market regime into actionable states.
    """

    @classmethod
    def classify(cls, features: dict[str, Any]) -> MarketRegimeReport:
        if not features:
            return MarketRegimeReport(
                symbol="UNKNOWN",
                timeframe="15m",
                timestamp_ms=0,
                regime=MarketRegimeType.UNKNOWN,
                volatility_regime=VolatilityRegimeType.NORMAL,
                volatility_trend=VolatilityTrend.STABLE,
                confidence=0.0,
                adx_strength=0.0,
                atr_percentile=50.0,
                description="Insufficient data for regime classification",
                favorable_strategies=[],
                unfavorable_strategies=["ALL"],
            )

        symbol = features.get("symbol", "UNKNOWN")
        timeframe = features.get("timeframe", "15m")
        ts = features.get("timestamp_ms", 0)

        close = features.get("close", 1.0)
        ema50 = features.get("ema_50", close)
        ema200 = features.get("ema_200", close)
        adx = features.get("adx_14", 20.0)
        rsi = features.get("rsi_14", 50.0)
        structure_state = features.get("structure_state", "RANGE")
        vol_regime_str = features.get("volatility_regime", "NORMAL")
        vol_trend_str = features.get("volatility_trend", "STABLE")
        atr_pct_rank = features.get("atr_percentile", 50.0)
        funding_z = features.get("funding_z_score", 0.0)
        is_squeeze = features.get("is_squeeze", False)
        cvd_div = features.get("cvd_divergence", 0.0)
        bos_bullish = features.get("bos_bullish", False)
        bos_bearish = features.get("bos_bearish", False)

        vol_regime = VolatilityRegimeType(vol_regime_str)
        vol_trend = VolatilityTrend(vol_trend_str)

        # 1. Detect Panic / Euphoria Anomalies
        if vol_regime == VolatilityRegimeType.EXTREME_VOLATILITY and rsi < 20.0:
            regime = MarketRegimeType.PANIC
            confidence = 0.90
            desc = "Extreme panic sell-off with peak volatility and capitulation pressure"
            favorable = ["MEAN_REVERSION", "LIQUIDITY_SWEEP"]
            unfavorable = ["TREND_FOLLOWING"]

        elif vol_regime == VolatilityRegimeType.EXTREME_VOLATILITY and rsi > 80.0 and funding_z > 2.5:
            regime = MarketRegimeType.EUPHORIA
            confidence = 0.85
            desc = "Parabolic euphoric rally with extreme long leverage and overheating"
            favorable = ["FUNDING_SQUEEZE", "MEAN_REVERSION"]
            unfavorable = ["BREAKOUT"]

        # 2. Breakouts / Breakdowns
        elif bos_bullish and is_squeeze:
            regime = MarketRegimeType.BREAKOUT
            confidence = 0.88
            desc = "Volatility expansion breakout above consolidation ceiling"
            favorable = ["BREAKOUT", "TREND_FOLLOWING", "ORDER_FLOW"]
            unfavorable = ["MEAN_REVERSION"]

        elif bos_bearish and is_squeeze:
            regime = MarketRegimeType.BREAKDOWN
            confidence = 0.88
            desc = "Volatility expansion breakdown below consolidation floor"
            favorable = ["BREAKOUT", "TREND_FOLLOWING", "ORDER_FLOW"]
            unfavorable = ["MEAN_REVERSION"]

        # 3. Strong Uptrend vs Weak Uptrend
        elif close > ema50 > ema200 and structure_state == "BULLISH":
            if adx >= 25.0:
                regime = MarketRegimeType.STRONG_UPTREND
                confidence = min(0.95, 0.70 + (adx / 100.0))
                desc = "Strong directional uptrend confirmed by moving averages and market structure"
                favorable = ["TREND_FOLLOWING", "BREAKOUT", "ORDER_FLOW"]
                unfavorable = ["MEAN_REVERSION"]
            else:
                regime = MarketRegimeType.WEAK_UPTREND
                confidence = 0.70
                desc = "Uptrend with low or decaying momentum"
                favorable = ["TREND_FOLLOWING", "LIQUIDITY_SWEEP"]
                unfavorable = ["BREAKOUT"]

        # 4. Strong Downtrend vs Weak Downtrend
        elif close < ema50 < ema200 and structure_state == "BEARISH":
            if adx >= 25.0:
                regime = MarketRegimeType.STRONG_DOWNTREND
                confidence = min(0.95, 0.70 + (adx / 100.0))
                desc = "Strong directional downtrend with lower highs/lows and structural weakness"
                favorable = ["TREND_FOLLOWING", "BREAKOUT", "ORDER_FLOW"]
                unfavorable = ["MEAN_REVERSION"]
            else:
                regime = MarketRegimeType.WEAK_DOWNTREND
                confidence = 0.70
                desc = "Downtrend with weak or slowing momentum"
                favorable = ["TREND_FOLLOWING", "LIQUIDITY_SWEEP"]
                unfavorable = ["BREAKOUT"]

        # 5. Accumulation / Distribution Base Building
        elif structure_state == "RANGE" and is_squeeze and cvd_div > 0:
            regime = MarketRegimeType.ACCUMULATION
            confidence = 0.75
            desc = "Compression range with positive cumulative volume delta absorption (Accumulation)"
            favorable = ["LIQUIDITY_SWEEP", "MEAN_REVERSION", "BREAKOUT"]
            unfavorable = ["TREND_FOLLOWING"]

        elif structure_state == "RANGE" and is_squeeze and cvd_div < 0:
            regime = MarketRegimeType.DISTRIBUTION
            confidence = 0.75
            desc = "Compression range with hidden selling delta near resistance (Distribution)"
            favorable = ["LIQUIDITY_SWEEP", "MEAN_REVERSION", "BREAKOUT"]
            unfavorable = ["TREND_FOLLOWING"]

        # 6. High Volatility Range vs Normal Range
        elif vol_regime in (VolatilityRegimeType.HIGH_VOLATILITY, VolatilityRegimeType.EXTREME_VOLATILITY):
            regime = MarketRegimeType.HIGH_VOLATILITY_RANGE
            confidence = 0.75
            desc = "Choppy market with elevated volatility and frequent false moves"
            favorable = ["LIQUIDITY_SWEEP", "MEAN_REVERSION"]
            unfavorable = ["BREAKOUT", "TREND_FOLLOWING"]

        else:
            regime = MarketRegimeType.RANGE
            confidence = 0.80
            desc = "Equilibrium sideways range with mean-reverting price action"
            favorable = ["MEAN_REVERSION", "LIQUIDITY_SWEEP"]
            unfavorable = ["TREND_FOLLOWING", "BREAKOUT"]

        return MarketRegimeReport(
            symbol=symbol,
            timeframe=timeframe,
            timestamp_ms=ts,
            regime=regime,
            volatility_regime=vol_regime,
            volatility_trend=vol_trend,
            confidence=round(confidence, 2),
            adx_strength=round(adx, 1),
            atr_percentile=round(atr_pct_rank, 1),
            description=desc,
            favorable_strategies=favorable,
            unfavorable_strategies=unfavorable,
        )
