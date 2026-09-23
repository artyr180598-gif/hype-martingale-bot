"""Early impulse / emergence detection — ported from v3 emergence.py but enhanced."""

from __future__ import annotations

import pandas as pd

from ..config import Settings
from .heat import compute_heat_score


def detect_early_impulse(df_1h: pd.DataFrame, cfg: Settings) -> dict:
    """
    Detect early impulse phases: EARLY, TRIGGERED, EXHAUSTED, WATCH.
    Uses heat + squeeze + RVOL + breakout pressure.
    """
    if df_1h.empty or len(df_1h) < 30:
        return {"phase": "NO_DATA", "heat": 0, "ignition": 0, "ready": False, "details": {}}

    heat_info = compute_heat_score(df_1h, None, cfg)
    heat = heat_info["heat"]
    phase = heat_info["phase"]

    try:
        last = df_1h.iloc[-1]
        atr = float(last.get("atr", last["close"] * 0.01))
        close = float(last["close"])

        # Breakout pressure: close near high/low of range
        lookback = cfg.EMERGENCE_BREAKOUT_LOOKBACK
        recent_high = df_1h["high"].tail(lookback).max()
        recent_low = df_1h["low"].tail(lookback).min()
        range_size = recent_high - recent_low if recent_high > recent_low else atr

        # Pressure 0..1
        if range_size > 0:
            dist_to_high = (recent_high - close) / range_size
            dist_to_low = (close - recent_low) / range_size
            breakout_pressure = max(1 - dist_to_high, 1 - dist_to_low)  # near edge = high pressure
        else:
            breakout_pressure = 0

        # Ignition score
        rvol = float(last.get("rvol", 1.0))
        volume_z = float(last.get("volume_z", 0))
        squeeze_release = bool(last.get("squeeze_release", False))
        adx = float(last.get("adx", 15))

        ignition = 0
        ignition += min(30, rvol * 15)
        ignition += min(20, max(0, volume_z) * 5)
        ignition += 20 if squeeze_release else 0
        ignition += min(15, max(0, adx - 15))
        ignition += min(15, breakout_pressure * 15)

        ready = (
            heat >= 45
            and ignition >= cfg.EMERGENCE_IGNITION_MIN
            and breakout_pressure >= cfg.EMERGENCE_MIN_BREAKOUT_PRESSURE
            and phase in ("EARLY", "TRIGGERED")
        )

        # Exhaustion check
        if phase == "EXHAUSTED":
            ready = False

        return {
            "phase": phase,
            "heat": heat,
            "ignition": ignition,
            "ready": ready,
            "breakout_pressure": breakout_pressure,
            "details": heat_info["components"],
        }
    except Exception as e:
        return {"phase": "ERROR", "heat": heat, "ignition": 0, "ready": False, "details": {"error": str(e)}}
