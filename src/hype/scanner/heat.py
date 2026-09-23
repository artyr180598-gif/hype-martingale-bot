"""Heat scoring — ranks coins by early impulse readiness (not past pump)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import Settings


def compute_heat_score(df_1h: pd.DataFrame, ticker, cfg: Settings) -> dict:
    """
    Compute heat score 0-100 for a symbol.
    Based on: RVOL, squeeze release, consolidation, range position, OI, BTC correlation.
    Ported from v3 emergence + CryptoScanBot heat.
    """
    if df_1h.empty or len(df_1h) < cfg.SCAN_EMERGENCE_BARS:
        return {"heat": 0, "components": {}, "phase": "NO_DATA"}

    try:
        last = df_1h.iloc[-1]
        prev = df_1h.iloc[-2] if len(df_1h) > 1 else last

        score = 0.0
        components = {}

        # RVOL (20 window)
        rvol = float(last.get("rvol", 1.0))
        if rvol >= cfg.EMERGENCE_RVOL_MIN:
            rvol_score = min(25, (rvol - 1) * 15)
        else:
            rvol_score = max(0, (rvol - 0.5) * 10)
        components["rvol"] = rvol_score
        score += rvol_score

        # Squeeze release
        squeeze_release = bool(last.get("squeeze_release", False))
        squeeze_on = bool(last.get("squeeze_on", False))
        if squeeze_release:
            components["squeeze"] = 20
            score += 20
        elif squeeze_on:
            # Compression building
            compression = float(last.get("volatility_compression", 1.0))
            if compression < cfg.EMERGENCE_COMPRESSION_ATR_RATIO:
                components["squeeze"] = 12
                score += 12
            else:
                components["squeeze"] = 5
                score += 5
        else:
            components["squeeze"] = 0

        # Consolidation (ATR low)
        atr = float(last.get("atr", 0))
        atr_50 = float(last.get("atr_50", atr or 1))
        if atr_50 > 0:
            comp_ratio = atr / atr_50
            if comp_ratio < 0.8:
                cons_score = 15 * (1 - comp_ratio)
                components["consolidation"] = min(15, cons_score)
                score += components["consolidation"]
            else:
                components["consolidation"] = 0
        else:
            components["consolidation"] = 0

        # Range position — room to move
        try:
            high_24 = df_1h["high"].tail(24).max()
            low_24 = df_1h["low"].tail(24).min()
            close = float(last["close"])
            if high_24 > low_24:
                range_pct = (high_24 - low_24) / low_24 * 100
                # Distance to edge
                dist_to_high = (high_24 - close) / (high_24 - low_24) if high_24 != low_24 else 0.5
                dist_to_low = (close - low_24) / (high_24 - low_24) if high_24 != low_24 else 0.5
                # We want not at extreme (room)
                room = min(dist_to_high, dist_to_low)
                if room >= cfg.EMERGENCE_MIN_ROOM_PCT:
                    room_score = 10 * room
                    components["room"] = min(10, room_score)
                    score += components["room"]
                else:
                    components["room"] = 0
            else:
                components["room"] = 0
        except Exception:
            components["room"] = 0

        # Volume Z
        try:
            vol_z = float(last.get("volume_z", 0))
            if vol_z > 1.0:
                vz_score = min(10, vol_z * 3)
                components["volume_z"] = vz_score
                score += vz_score
            else:
                components["volume_z"] = 0
        except Exception:
            components["volume_z"] = 0

        # ATR expansion vs compression
        try:
            atr_pct = float(last.get("atr_pct", 0))
            if 0.3 <= atr_pct <= 4.0:
                components["atr_pct"] = 5
                score += 5
            else:
                components["atr_pct"] = 0
        except Exception:
            components["atr_pct"] = 0

        # RSI not overbought/oversold extreme for early
        try:
            rsi = float(last.get("rsi", 50))
            if 35 <= rsi <= 65:
                components["rsi_mid"] = 8
                score += 8
            else:
                components["rsi_mid"] = 0
        except Exception:
            components["rsi_mid"] = 0

        # MFI confirmation
        try:
            mfi = float(last.get("mfi", 50))
            if 45 <= mfi <= 65:
                components["mfi"] = 5
                score += 5
            else:
                components["mfi"] = 0
        except Exception:
            components["mfi"] = 0

        # ADX trend building
        try:
            adx = float(last.get("adx", 0))
            if adx > 18:
                components["adx"] = min(7, (adx - 18) * 0.5)
                score += components["adx"]
            else:
                components["adx"] = 0
        except Exception:
            components["adx"] = 0

        # Final heat 0-100
        heat = min(100, max(0, score))

        # Phase detection
        # Check if already exhausted (big recent move)
        try:
            recent_move_atr = abs(float(last["close"]) - float(df_1h["close"].iloc[-20])) / (atr or 1)
            if recent_move_atr > cfg.EMERGENCE_MAX_RECENT_MOVE_ATR:
                phase = "EXHAUSTED"
            elif squeeze_release and rvol > cfg.EMERGENCE_RVOL_MIN and heat > 55:
                phase = "TRIGGERED"
            elif heat > 45:
                phase = "EARLY"
            else:
                phase = "WATCH"
        except Exception:
            phase = "WATCH"

        return {"heat": heat, "components": components, "phase": phase}

    except Exception as e:
        return {"heat": 0, "components": {"error": str(e)}, "phase": "ERROR"}
