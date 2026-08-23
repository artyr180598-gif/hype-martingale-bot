"""
Market Structure Engine — Swing Points, HH/HL/LH/LL, BOS, and CHoCH.
"""
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class SwingPoint:
    index: int
    timestamp_ms: int
    price: float
    is_high: bool  # True for Swing High, False for Swing Low
    label: str     # HH, HL, LH, LL


class MarketStructureAnalyzer:
    """
    Detects market structure pivots, Break of Structure (BOS), and Change of Character (CHoCH).
    """

    def __init__(self, fractal_window: int = 3):
        self.fractal_window = fractal_window

    def find_swing_points(self, df: pd.DataFrame) -> list[SwingPoint]:
        """Find local fractal swing highs and swing lows without future bias."""
        if len(df) < self.fractal_window * 2 + 1:
            return []

        swings: list[SwingPoint] = []
        w = self.fractal_window
        highs = df["high"].values
        lows = df["low"].values
        ts = df["timestamp_ms"].values if "timestamp_ms" in df.columns else np.zeros(len(df))

        # Identify swing highs and lows
        for i in range(w, len(df) - w):
            # Swing High: strict maximum in window
            if highs[i] == max(highs[i - w : i + w + 1]):
                label = "SH"
                if swings and swings[-1].is_high:
                    label = "HH" if highs[i] > swings[-1].price else "LH"
                elif [s for s in swings if s.is_high]:
                    last_h = [s for s in swings if s.is_high][-1]
                    label = "HH" if highs[i] > last_h.price else "LH"

                swings.append(
                    SwingPoint(
                        index=i,
                        timestamp_ms=int(ts[i]),
                        price=float(highs[i]),
                        is_high=True,
                        label=label,
                    )
                )

            # Swing Low: strict minimum in window
            elif lows[i] == min(lows[i - w : i + w + 1]):
                label = "SL"
                if swings and not swings[-1].is_high:
                    label = "HL" if lows[i] > swings[-1].price else "LL"
                elif [s for s in swings if not s.is_high]:
                    last_l = [s for s in swings if not s.is_high][-1]
                    label = "HL" if lows[i] > last_l.price else "LL"

                swings.append(
                    SwingPoint(
                        index=i,
                        timestamp_ms=int(ts[i]),
                        price=float(lows[i]),
                        is_high=False,
                        label=label,
                    )
                )

        return swings

    def analyze_structure(self, df: pd.DataFrame) -> dict[str, Any]:
        """
        Analyze structural trend state, BOS, and CHoCH on the latest candle.
        """
        swings = self.find_swing_points(df)
        if len(swings) < 4:
            return {
                "structure_state": "NEUTRAL",
                "structure_score": 0.0,
                "bos_bullish": False,
                "bos_bearish": False,
                "choch_bullish": False,
                "choch_bearish": False,
                "last_swing_high": float(df["high"].max()) if len(df) > 0 else 0.0,
                "last_swing_low": float(df["low"].min()) if len(df) > 0 else 0.0,
                "swings": swings,
            }

        last_highs = [s for s in swings if s.is_high]
        last_lows = [s for s in swings if not s.is_high]

        last_sh = last_highs[-1] if last_highs else swings[0]
        last_sl = last_lows[-1] if last_lows else swings[0]

        cur_close = float(df["close"].iloc[-1])
        cur_high = float(df["high"].iloc[-1])
        cur_low = float(df["low"].iloc[-1])

        # BOS (Break of structure)
        bos_bullish = cur_close > last_sh.price
        bos_bearish = cur_close < last_sl.price

        # CHoCH (Change of Character): Reversal across previous structural high/low
        choch_bullish = False
        choch_bearish = False
        if len(last_highs) >= 2 and len(last_lows) >= 2:
            prev_structure_bearish = last_highs[-1].price < last_highs[-2].price
            prev_structure_bullish = last_lows[-1].price > last_lows[-2].price
            choch_bullish = prev_structure_bearish and (cur_close > last_highs[-1].price)
            choch_bearish = prev_structure_bullish and (cur_close < last_lows[-1].price)

        # Structure State Determination
        hh_count = sum(1 for s in last_highs[-3:] if s.label == "HH")
        hl_count = sum(1 for s in last_lows[-3:] if s.label == "HL")
        lh_count = sum(1 for s in last_highs[-3:] if s.label == "LH")
        ll_count = sum(1 for s in last_lows[-3:] if s.label == "LL")

        bullish_points = hh_count + hl_count
        bearish_points = lh_count + ll_count

        if bullish_points > bearish_points and (bos_bullish or cur_close > (last_sh.price + last_sl.price) / 2):
            state = "BULLISH"
            score = 1.0
        elif bearish_points > bullish_points and (bos_bearish or cur_close < (last_sh.price + last_sl.price) / 2):
            state = "BEARISH"
            score = -1.0
        else:
            state = "RANGE"
            score = 0.0

        return {
            "structure_state": state,
            "structure_score": score,
            "bos_bullish": bos_bullish,
            "bos_bearish": bos_bearish,
            "choch_bullish": choch_bullish,
            "choch_bearish": choch_bearish,
            "last_swing_high": last_sh.price,
            "last_swing_low": last_sl.price,
            "swings": swings,
        }
