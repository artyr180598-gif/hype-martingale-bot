"""STOBB / SBM / JUMP signal detection — ported from CryptoScanBot (CryptoMarius)."""

from __future__ import annotations

import pandas as pd

from ..config import Settings


def detect_stobb(df: pd.DataFrame, cfg: Settings, direction: str = "long") -> dict | None:
    """
    STOBB = Stochastic + Bollinger Bands oversold/overbought.
    Original: https://github.com/CryptoMarius/CryptoScanBot
    - Long: Stoch K < 25, D < 25, price near lower BB (bb_pct < 0.15)
    - Short: Stoch K > 75, D > 75, price near upper BB (bb_pct > 0.85)
    """
    if df.empty or len(df) < 20:
        return None
    last = df.iloc[-1]

    try:
        stoch_k = float(last.get("stoch_k", 50))
        stoch_d = float(last.get("stoch_d", 50))
        bb_pct = float(last.get("bb_pct", 0.5))
        rsi = float(last.get("rsi", 50))
        close = float(last["close"])
        bb_lower = float(last.get("bb_lower", close * 0.98))
        bb_upper = float(last.get("bb_upper", close * 1.02))

        if direction == "long":
            # Oversold
            if (
                stoch_k <= cfg.STOBB_STOCH_K_MAX
                and stoch_d <= cfg.STOBB_STOCH_D_MAX
                and bb_pct <= cfg.STOBB_BB_POS_MAX
                and rsi < 40
            ):
                return {
                    "type": "STOBB",
                    "direction": "LONG",
                    "strength": 100 - (stoch_k + stoch_d) / 2,
                    "reason": f"Stoch K={stoch_k:.1f} D={stoch_d:.1f} BB%={bb_pct:.2f} RSI={rsi:.1f} — oversold near lower BB",
                    "indicators": {"stoch_k": stoch_k, "stoch_d": stoch_d, "bb_pct": bb_pct, "rsi": rsi},
                }
        else:
            # Overbought (short)
            if (
                stoch_k >= 100 - cfg.STOBB_STOCH_K_MAX
                and stoch_d >= 100 - cfg.STOBB_STOCH_D_MAX
                and bb_pct >= 1 - cfg.STOBB_BB_POS_MAX
                and rsi > 60
            ):
                return {
                    "type": "STOBB",
                    "direction": "SHORT",
                    "strength": (stoch_k + stoch_d) / 2,
                    "reason": f"Stoch K={stoch_k:.1f} D={stoch_d:.1f} BB%={bb_pct:.2f} RSI={rsi:.1f} — overbought near upper BB",
                    "indicators": {"stoch_k": stoch_k, "stoch_d": stoch_d, "bb_pct": bb_pct, "rsi": rsi},
                }
    except Exception:
        pass
    return None


def detect_sbm(df: pd.DataFrame, cfg: Settings, direction: str = "long") -> dict | None:
    """
    SBM = STOBB + 3 MA lines in right order + PSAR.
    Original CryptoScanBot SBM:
    - For LONG: STOBB condition + EMA20 > EMA50 > EMA200 + PSAR below price + price above EMA20
    - For SHORT: inverse
    """
    stobb = detect_stobb(df, cfg, direction=direction)
    if not stobb:
        return None

    if df.empty or len(df) < 200:
        return None

    last = df.iloc[-1]
    try:
        ema20 = float(last.get("ema20", 0))
        ema50 = float(last.get("ema50", 0))
        ema200 = float(last.get("ema200", 0))
        close = float(last["close"])
        psar = float(last.get("psar", close))
        psar_dir = int(last.get("psar_dir", 1))

        if direction == "long":
            ma_aligned = ema20 > ema50 > ema200
            price_above = close > ema20
            psar_bull = psar < close and psar_dir == 1
            if ma_aligned and price_above and (psar_bull or not cfg.SBM_PSAR_ENABLED):
                return {
                    "type": "SBM",
                    "direction": "LONG",
                    "strength": min(95, stobb["strength"] + 15),
                    "reason": f"{stobb['reason']} + EMA20>{ema20:.2f} > EMA50>{ema50:.2f} > EMA200>{ema200:.2f} + PSAR bullish",
                    "indicators": {**stobb["indicators"], "ema20": ema20, "ema50": ema50, "ema200": ema200, "psar": psar},
                }
        else:
            ma_aligned = ema20 < ema50 < ema200
            price_below = close < ema20
            psar_bear = psar > close and psar_dir == -1
            if ma_aligned and price_below and (psar_bear or not cfg.SBM_PSAR_ENABLED):
                return {
                    "type": "SBM",
                    "direction": "SHORT",
                    "strength": min(95, stobb["strength"] + 15),
                    "reason": f"{stobb['reason']} + EMA20<{ema20:.2f} < EMA50<{ema50:.2f} < EMA200<{ema200:.2f} + PSAR bearish",
                    "indicators": {**stobb["indicators"], "ema20": ema20, "ema50": ema50, "ema200": ema200, "psar": psar},
                }
    except Exception:
        pass
    return None


def detect_jump(df: pd.DataFrame, cfg: Settings) -> dict | None:
    """
    JUMP = sudden price + volume spike.
    Original CryptoScanBot JUMP: information on sudden increasing/decreasing prices.
    """
    if df.empty or len(df) < cfg.JUMP_LOOKBACK_BARS + 2:
        return None

    try:
        last = df.iloc[-1]
        lookback = df.iloc[-cfg.JUMP_LOOKBACK_BARS - 1 : -1]

        close_now = float(last["close"])
        close_avg = float(lookback["close"].mean())
        price_change_pct = (close_now - close_avg) / close_avg * 100 if close_avg else 0

        vol_now = float(last["volume"])
        vol_avg = float(lookback["volume"].mean()) if len(lookback) > 0 else vol_now
        vol_mult = vol_now / vol_avg if vol_avg > 0 else 1.0

        rvol = float(last.get("rvol", 1.0))
        volume_z = float(last.get("volume_z", 0))

        # Jump up
        if price_change_pct >= cfg.JUMP_PRICE_PCT_MIN and vol_mult >= cfg.JUMP_VOLUME_MULT_MIN:
            direction = "LONG" if price_change_pct > 0 else "SHORT"
            return {
                "type": "JUMP",
                "direction": direction,
                "strength": min(90, abs(price_change_pct) * 10 + vol_mult * 5),
                "reason": f"JUMP {price_change_pct:+.2f}% price in {cfg.JUMP_LOOKBACK_BARS} bars + {vol_mult:.1f}x volume (RVOL {rvol:.1f})",
                "indicators": {
                    "price_change_pct": price_change_pct,
                    "vol_mult": vol_mult,
                    "rvol": rvol,
                    "volume_z": volume_z,
                },
            }
        # Jump down
        if price_change_pct <= -cfg.JUMP_PRICE_PCT_MIN and vol_mult >= cfg.JUMP_VOLUME_MULT_MIN:
            return {
                "type": "JUMP",
                "direction": "SHORT",
                "strength": min(90, abs(price_change_pct) * 10 + vol_mult * 5),
                "reason": f"JUMP {price_change_pct:+.2f}% price in {cfg.JUMP_LOOKBACK_BARS} bars + {vol_mult:.1f}x volume (RVOL {rvol:.1f})",
                "indicators": {
                    "price_change_pct": price_change_pct,
                    "vol_mult": vol_mult,
                    "rvol": rvol,
                    "volume_z": volume_z,
                },
            }
    except Exception:
        pass
    return None


def detect_all_signals(df: pd.DataFrame, cfg: Settings) -> list[dict]:
    """Run all detectors and return list."""
    signals = []
    for direction in ("long", "short"):
        stobb = detect_stobb(df, cfg, direction=direction)
        if stobb:
            signals.append(stobb)
        sbm = detect_sbm(df, cfg, direction=direction)
        if sbm:
            signals.append(sbm)
    jump = detect_jump(df, cfg)
    if jump:
        signals.append(jump)
    return signals
