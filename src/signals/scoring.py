"""
Multi-Factor 0–100 Signal Scoring Engine.
"""
from typing import Any, Optional

from src.config.constants import SignalDirection
from src.config.settings import settings
from src.signals.models import ScoreBreakdown


class SignalScorer:
    """
    Computes objective, granular sub-scores across all quantitative market dimensions.
    """

    @classmethod
    def score_setup(
        cls,
        direction: SignalDirection,
        features: dict[str, Any],
        weights: Optional[dict[str, float]] = None,
    ) -> ScoreBreakdown:
        if direction == SignalDirection.NO_TRADE or not features:
            return ScoreBreakdown()

        w = weights or settings.SCORE_WEIGHTS
        is_long = direction == SignalDirection.LONG

        close = features.get("close", 1.0)
        ema50 = features.get("ema_50", close)
        ema200 = features.get("ema_200", close)
        adx = features.get("adx_14", 20.0)
        rsi = features.get("rsi_14", 50.0)
        structure = features.get("structure_state", "RANGE")
        bos_bull = features.get("bos_bullish", False)
        bos_bear = features.get("bos_bearish", False)
        sweep_bull = features.get("sweep_bullish", False)
        sweep_bear = features.get("sweep_bearish", False)
        imbalance = features.get("orderbook_imbalance", 0.0)
        cvd_div = features.get("cvd_divergence", 0.0)
        funding_z = features.get("funding_z_score", 0.0)
        vol_regime = features.get("volatility_regime", "NORMAL")
        is_squeeze = features.get("is_squeeze", False)
        atr_pct = features.get("atr_percentile", 50.0)

        # 1. Trend (0 to 15.0)
        trend_score = 0.0
        if is_long:
            if close > ema50:
                trend_score += 5.0
            if close > ema200:
                trend_score += 5.0
            if adx >= 25.0:
                trend_score += 5.0
            elif adx >= 18.0:
                trend_score += 3.0
        else:
            if close < ema50:
                trend_score += 5.0
            if close < ema200:
                trend_score += 5.0
            if adx >= 25.0:
                trend_score += 5.0
            elif adx >= 18.0:
                trend_score += 3.0

        # 2. Market Structure (0 to 15.0)
        ms_score = 0.0
        if is_long:
            if structure == "BULLISH":
                ms_score += 9.0
            if bos_bull:
                ms_score += 4.0
            if sweep_bull:
                ms_score += 2.0
        else:
            if structure == "BEARISH":
                ms_score += 9.0
            if bos_bear:
                ms_score += 4.0
            if sweep_bear:
                ms_score += 2.0

        # 3. Order Flow (0 to 15.0)
        of_score = 0.0
        if is_long:
            if imbalance > 0.15:
                of_score += min(7.5, imbalance * 15.0)
            if cvd_div > 0:
                of_score += 7.5
        else:
            if imbalance < -0.15:
                of_score += min(7.5, abs(imbalance) * 15.0)
            if cvd_div < 0:
                of_score += 7.5

        # 4. Volatility (0 to 10.0)
        vol_score = 6.0
        if is_squeeze or (30.0 <= atr_pct <= 75.0):
            vol_score = 10.0
        elif vol_regime == "EXTREME_VOLATILITY":
            vol_score = 4.0

        # 5. Open Interest / Positioning (0 to 10.0)
        oi_score = 7.0
        pos_state = features.get("positioning_state", "NEUTRAL")
        if (is_long and pos_state == "LONG_ACCUMULATION") or (not is_long and pos_state == "SHORT_ACCUMULATION"):
            oi_score = 10.0

        # 6. Volume (0 to 10.0)
        volume_score = 7.5

        # 7. Momentum (0 to 10.0)
        mom_score = 0.0
        if is_long:
            if 42.0 <= rsi <= 68.0:
                mom_score += 10.0
            elif 30.0 <= rsi < 42.0:
                mom_score += 7.0
            else:
                mom_score += 4.0
        else:
            if 32.0 <= rsi <= 58.0:
                mom_score += 10.0
            elif 58.0 < rsi <= 70.0:
                mom_score += 7.0
            else:
                mom_score += 4.0

        # 8. Funding (0 to 5.0)
        funding_score = 3.5
        if (is_long and funding_z < -1.0) or (not is_long and funding_z > 1.5):
            funding_score = 5.0
        elif (is_long and funding_z > 2.0) or (not is_long and funding_z < -2.0):
            funding_score = 1.0

        # 9. Liquidations (0 to 5.0)
        liq_score = 4.0
        if (is_long and sweep_bull) or (not is_long and sweep_bear):
            liq_score = 5.0

        # 10. Market Breadth (0 to 5.0)
        breadth_score = 3.5

        # 11. News / Sentiment (0 to 5.0)
        sentiment_score = 3.5

        return ScoreBreakdown(
            trend=round(min(15.0, trend_score), 1),
            market_structure=round(min(15.0, ms_score), 1),
            order_flow=round(min(15.0, of_score), 1),
            volatility=round(min(10.0, vol_score), 1),
            open_interest=round(min(10.0, oi_score), 1),
            volume=round(min(10.0, volume_score), 1),
            momentum=round(min(10.0, mom_score), 1),
            funding=round(min(5.0, funding_score), 1),
            liquidations=round(min(5.0, liq_score), 1),
            market_breadth=round(min(5.0, breadth_score), 1),
            sentiment=round(min(5.0, sentiment_score), 1),
        )
