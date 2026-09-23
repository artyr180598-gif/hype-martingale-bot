"""Market structure — HH/HL/LH/LL, BOS/CHoCH, Support/Resistance, Fibonacci, pivots."""

from __future__ import annotations

import numpy as np
import pandas as pd


def find_pivots(df: pd.DataFrame, left: int = 2, right: int = 2) -> tuple[list[int], list[int]]:
    """Find swing highs/lows indices."""
    highs = []
    lows = []
    if len(df) < left + right + 1:
        return highs, lows

    high = df["high"].values
    low = df["low"].values

    for i in range(left, len(df) - right):
        # Swing high
        if all(high[i] > high[i - j] for j in range(1, left + 1)) and all(high[i] > high[i + j] for j in range(1, right + 1)):
            highs.append(i)
        # Swing low
        if all(low[i] < low[i - j] for j in range(1, left + 1)) and all(low[i] < low[i + j] for j in range(1, right + 1)):
            lows.append(i)

    return highs, lows


def compute_structure(df: pd.DataFrame) -> dict:
    """
    Compute market structure from OHLCV.
    Returns dict with levels, trend structure, BOS/CHoCH.
    """
    if df.empty or len(df) < 30:
        return {
            "swing_highs": [],
            "swing_lows": [],
            "support": [],
            "resistance": [],
            "structure": "RANGING",
            "bos": None,
            "choch": None,
            "higher_highs": False,
            "higher_lows": False,
            "lower_highs": False,
            "lower_lows": False,
        }

    highs_idx, lows_idx = find_pivots(df, left=3, right=3)

    swing_highs = [float(df["high"].iloc[i]) for i in highs_idx[-10:]]
    swing_lows = [float(df["low"].iloc[i]) for i in lows_idx[-10:]]

    # Support/Resistance clustering
    support = sorted(swing_lows)[-5:] if swing_lows else []
    resistance = sorted(swing_highs, reverse=True)[:5] if swing_highs else []

    # Determine HH/HL structure
    higher_highs = False
    higher_lows = False
    lower_highs = False
    lower_lows = False

    if len(swing_highs) >= 2:
        higher_highs = swing_highs[-1] > swing_highs[-2]
        lower_highs = swing_highs[-1] < swing_highs[-2]
    if len(swing_lows) >= 2:
        higher_lows = swing_lows[-1] > swing_lows[-2]
        lower_lows = swing_lows[-1] < swing_lows[-2]

    # BOS / CHoCH detection (simplified)
    structure = "RANGING"
    bos = None
    choch = None

    close = df["close"].iloc[-1]

    # If price breaks recent swing high/low
    if swing_highs and close > max(swing_highs[:-1] or [0]):
        bos = "BULLISH_BOS"
        structure = "TRENDING_UP"
    elif swing_lows and close < min(swing_lows[:-1] or [float("inf")]):
        bos = "BEARISH_BOS"
        structure = "TRENDING_DOWN"

    # CHoCH = change of character (trend reversal)
    if higher_highs and higher_lows:
        structure = "TRENDING_UP"
    elif lower_highs and lower_lows:
        structure = "TRENDING_DOWN"
    elif (higher_highs and lower_lows) or (lower_highs and higher_lows):
        structure = "RANGING"
        choch = "POTENTIAL_CHOCH"

    return {
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "support": support,
        "resistance": resistance,
        "structure": structure,
        "bos": bos,
        "choch": choch,
        "higher_highs": higher_highs,
        "higher_lows": higher_lows,
        "lower_highs": lower_highs,
        "lower_lows": lower_lows,
        "last_swing_high": swing_highs[-1] if swing_highs else None,
        "last_swing_low": swing_lows[-1] if swing_lows else None,
    }


def fibonacci_levels(high: float, low: float) -> dict[str, float]:
    diff = high - low
    return {
        "0": low,
        "0.236": low + diff * 0.236,
        "0.382": low + diff * 0.382,
        "0.5": low + diff * 0.5,
        "0.618": low + diff * 0.618,
        "0.786": low + diff * 0.786,
        "1": high,
        "1.272": high + diff * 0.272,
        "1.618": high + diff * 0.618,
    }


def nearest_support_resistance(price: float, support: list[float], resistance: list[float]) -> tuple[float | None, float | None]:
    """Return nearest support below and resistance above."""
    below = [s for s in support if s < price]
    above = [r for r in resistance if r > price]
    nearest_sup = max(below) if below else None
    nearest_res = min(above) if above else None
    return nearest_sup, nearest_res
