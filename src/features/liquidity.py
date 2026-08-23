"""
Liquidity Analysis Engine — Equal Highs/Lows, Liquidity Sweeps, and Fair Value Gaps.
"""
from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class LiquidityPool:
    price_level: float
    pool_type: str  # "EQH" (Equal Highs) or "EQL" (Equal Lows)
    strength: int   # Number of touches
    swept: bool     # True if liquidity was already raided


@dataclass
class FairValueGap:
    top: float
    bottom: float
    gap_type: str   # "BULLISH" or "BEARISH"
    timestamp_ms: int
    mitigated: bool


class LiquidityAnalyzer:
    """
    Identifies institutional liquidity pools, stop hunts, sweeps, and order imbalances.
    """

    def __init__(self, eq_tolerance_pct: float = 0.20):
        self.eq_tolerance_pct = eq_tolerance_pct

    def find_equal_highs_lows(self, df: pd.DataFrame, window: int = 50) -> list[LiquidityPool]:
        """Detect clusters of equal highs and equal lows where retail stop orders accumulate."""
        if len(df) < window:
            return []

        recent = df.iloc[-window:]
        highs = recent["high"].values
        lows = recent["low"].values
        pools: list[LiquidityPool] = []

        # Equal Highs Check
        for i in range(len(highs) - 5):
            h1 = highs[i]
            touches = 1
            for j in range(i + 3, len(highs)):
                h2 = highs[j]
                if abs(h1 - h2) / h1 * 100.0 <= self.eq_tolerance_pct:
                    touches += 1
            if touches >= 2:
                # Check if already swept by subsequent bars
                is_swept = any(h > h1 * (1 + self.eq_tolerance_pct / 100) for h in highs[i:])
                pools.append(LiquidityPool(price_level=float(h1), pool_type="EQH", strength=touches, swept=is_swept))

        # Equal Lows Check
        for i in range(len(lows) - 5):
            l1 = lows[i]
            touches = 1
            for j in range(i + 3, len(lows)):
                l2 = lows[j]
                if abs(l1 - l2) / l1 * 100.0 <= self.eq_tolerance_pct:
                    touches += 1
            if touches >= 2:
                is_swept = any(low_val < l1 * (1 - self.eq_tolerance_pct / 100) for low_val in lows[i:])
                pools.append(LiquidityPool(price_level=float(l1), pool_type="EQL", strength=touches, swept=is_swept))

        return pools

    def detect_liquidity_sweep(self, df: pd.DataFrame) -> dict[str, Any]:
        """
        Detect if the most recent bar executed a liquidity sweep:
        - Bullish sweep: low probed below key support/swing low, but closed firmly above it.
        - Bearish sweep: high probed above key resistance/swing high, but closed firmly below it.
        """
        if len(df) < 10:
            return {"sweep_bullish": False, "sweep_bearish": False, "swept_level": None}

        recent_high = df["high"].iloc[-15:-1].max()
        recent_low = df["low"].iloc[-15:-1].min()

        last_bar = df.iloc[-1]
        cur_open = float(last_bar["open"])
        cur_high = float(last_bar["high"])
        cur_low = float(last_bar["low"])
        cur_close = float(last_bar["close"])

        sweep_bullish = False
        sweep_bearish = False
        swept_level = None

        # Bullish Sweep: wicked below recent low, but closed above recent low
        if cur_low < recent_low and cur_close > recent_low and cur_close > cur_open:
            sweep_bullish = True
            swept_level = float(recent_low)

        # Bearish Sweep: wicked above recent high, but closed below recent high
        elif cur_high > recent_high and cur_close < recent_high and cur_close < cur_open:
            sweep_bearish = True
            swept_level = float(recent_high)

        return {
            "sweep_bullish": sweep_bullish,
            "sweep_bearish": sweep_bearish,
            "swept_level": swept_level,
        }

    def detect_fair_value_gaps(self, df: pd.DataFrame, max_lookback: int = 30) -> list[FairValueGap]:
        """Detect 3-candle Fair Value Gaps (FVG) / Liquidity Imbalances."""
        if len(df) < 3:
            return []

        fvgs: list[FairValueGap] = []
        lookback = min(len(df), max_lookback)
        subset = df.iloc[-lookback:].reset_index(drop=True)

        for i in range(2, len(subset)):
            c0_high = float(subset.loc[i - 2, "high"])
            c0_low = float(subset.loc[i - 2, "low"])
            c2_high = float(subset.loc[i, "high"])
            c2_low = float(subset.loc[i, "low"])
            ts = int(subset.loc[i, "timestamp_ms"]) if "timestamp_ms" in subset.columns else 0

            # Bullish FVG: Low of candle 2 is strictly above High of candle 0
            if c2_low > c0_high:
                # Check if mitigated later
                subsequent_lows = subset.loc[i + 1 :, "low"].values if i + 1 < len(subset) else []
                mitigated = any(low_val <= c0_high for low_val in subsequent_lows) if len(subsequent_lows) > 0 else False
                fvgs.append(FairValueGap(top=c2_low, bottom=c0_high, gap_type="BULLISH", timestamp_ms=ts, mitigated=mitigated))

            # Bearish FVG: High of candle 2 is strictly below Low of candle 0
            elif c2_high < c0_low:
                subsequent_highs = subset.loc[i + 1 :, "high"].values if i + 1 < len(subset) else []
                mitigated = any(h >= c0_low for h in subsequent_highs) if len(subsequent_highs) > 0 else False
                fvgs.append(FairValueGap(top=c0_low, bottom=c2_high, gap_type="BEARISH", timestamp_ms=ts, mitigated=mitigated))

        return fvgs
