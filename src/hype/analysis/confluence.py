"""Confluence engine — 11 weighted strategies voting (ported from Confluence Futures Terminal)."""

from __future__ import annotations

import pandas as pd

from ..config import Settings


def evaluate_strategies(df: pd.DataFrame, cfg: Settings) -> dict:
    """
    11 strategies vote on each coin:
    1. EMA crossover (9/21)
    2. 200 EMA trend filter
    3. RSI momentum
    4. MACD
    5. Bollinger Bands position
    6. Donchian breakout (ADX gated)
    7. RSI-2 mean reversion
    8. Stochastic
    9. VWAP
    10. Consecutive candle reversion
    11. RSI/price divergence
    Returns long_score, short_score, details.
    """
    if df.empty or len(df) < 50:
        return {"long_score": 50, "short_score": 50, "bias": "NEUTRAL", "votes": []}

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else last

    votes = []

    def add_vote(name: str, long_pts: float, short_pts: float, reason: str):
        votes.append({"name": name, "long": long_pts, "short": short_pts, "reason": reason})

    try:
        # 1. EMA crossover
        ema9 = float(last.get("ema9", 0))
        ema21 = float(last.get("ema21", 0))
        ema9_prev = float(prev.get("ema9", 0))
        ema21_prev = float(prev.get("ema21", 0))
        if ema9 > ema21 and ema9_prev <= ema21_prev:
            add_vote("EMA crossover", 15, 0, f"EMA9 {ema9:.2f} crossed above EMA21 {ema21:.2f}")
        elif ema9 < ema21 and ema9_prev >= ema21_prev:
            add_vote("EMA crossover", 0, 15, f"EMA9 {ema9:.2f} crossed below EMA21 {ema21:.2f}")
        elif ema9 > ema21:
            add_vote("EMA crossover", 8, 0, f"EMA9 above EMA21 — uptrend")
        elif ema9 < ema21:
            add_vote("EMA crossover", 0, 8, f"EMA9 below EMA21 — downtrend")
        else:
            add_vote("EMA crossover", 5, 5, "EMA flat")

        # 2. 200 EMA trend filter
        ema200 = float(last.get("ema200", 0))
        close = float(last["close"])
        if close > ema200:
            add_vote("200 EMA filter", 12, 0, f"Price {close:.2f} above EMA200 {ema200:.2f} — long bias")
        else:
            add_vote("200 EMA filter", 0, 12, f"Price {close:.2f} below EMA200 {ema200:.2f} — short bias")

        # 3. RSI
        rsi = float(last.get("rsi", 50))
        if rsi < 30:
            add_vote("RSI", 14, 0, f"RSI {rsi:.1f} oversold — long reversal")
        elif rsi > 70:
            add_vote("RSI", 0, 14, f"RSI {rsi:.1f} overbought — short reversal")
        elif 40 <= rsi <= 60:
            add_vote("RSI", 6, 6, f"RSI {rsi:.1f} neutral")
        elif rsi > 55:
            add_vote("RSI", 8, 2, f"RSI {rsi:.1f} bullish momentum")
        else:
            add_vote("RSI", 2, 8, f"RSI {rsi:.1f} bearish momentum")

        # 4. MACD
        macd = float(last.get("macd", 0))
        macd_sig = float(last.get("macd_signal", 0))
        macd_hist = float(last.get("macd_hist", 0))
        macd_hist_prev = float(prev.get("macd_hist", 0))
        if macd_hist > 0 and macd_hist_prev <= 0:
            add_vote("MACD", 12, 0, f"MACD hist crossed bullish {macd_hist:.4f}")
        elif macd_hist < 0 and macd_hist_prev >= 0:
            add_vote("MACD", 0, 12, f"MACD hist crossed bearish {macd_hist:.4f}")
        elif macd_hist > 0:
            add_vote("MACD", 7, 0, f"MACD bullish {macd_hist:.4f}")
        else:
            add_vote("MACD", 0, 7, f"MACD bearish {macd_hist:.4f}")

        # 5. Bollinger
        bb_pct = float(last.get("bb_pct", 0.5))
        if bb_pct < 0.15:
            add_vote("Bollinger", 13, 0, f"BB %B {bb_pct:.2f} near lower band — long")
        elif bb_pct > 0.85:
            add_vote("Bollinger", 0, 13, f"BB %B {bb_pct:.2f} near upper band — short")
        elif bb_pct < 0.3:
            add_vote("Bollinger", 6, 0, f"BB %B {bb_pct:.2f} lower half")
        elif bb_pct > 0.7:
            add_vote("Bollinger", 0, 6, f"BB %B {bb_pct:.2f} upper half")
        else:
            add_vote("Bollinger", 4, 4, f"BB %B {bb_pct:.2f} middle")

        # 6. Donchian breakout (ADX gated)
        adx = float(last.get("adx", 15))
        don_upper = float(last.get("donchian_upper_20", close))
        don_lower = float(last.get("donchian_lower_20", close))
        if close > don_upper and adx > cfg.ADX_TREND_MIN:
            add_vote("Donchian breakout", 14, 0, f"Breakout above Donchian {don_upper:.2f} ADX {adx:.1f}")
        elif close < don_lower and adx > cfg.ADX_TREND_MIN:
            add_vote("Donchian breakout", 0, 14, f"Breakdown below Donchian {don_lower:.2f} ADX {adx:.1f}")
        else:
            add_vote("Donchian breakout", 3, 3, f"No breakout ADX {adx:.1f}")

        # 7. RSI-2 mean reversion (short-term)
        rsi_7 = float(last.get("rsi_7", 50))
        if rsi_7 < 10:
            add_vote("RSI-2 MR", 12, 0, f"RSI-7 {rsi_7:.1f} extreme oversold — mean reversion long")
        elif rsi_7 > 90:
            add_vote("RSI-2 MR", 0, 12, f"RSI-7 {rsi_7:.1f} extreme overbought — mean reversion short")
        else:
            add_vote("RSI-2 MR", 4, 4, f"RSI-7 {rsi_7:.1f} neutral")

        # 8. Stochastic
        stoch_k = float(last.get("stoch_k", 50))
        stoch_d = float(last.get("stoch_d", 50))
        if stoch_k < 20 and stoch_d < 20 and stoch_k > stoch_d:
            add_vote("Stochastic", 10, 0, f"Stoch K {stoch_k:.1f} D {stoch_d:.1f} oversold cross up")
        elif stoch_k > 80 and stoch_d > 80 and stoch_k < stoch_d:
            add_vote("Stochastic", 0, 10, f"Stoch K {stoch_k:.1f} D {stoch_d:.1f} overbought cross down")
        else:
            add_vote("Stochastic", 5, 5, f"Stoch K {stoch_k:.1f} D {stoch_d:.1f}")

        # 9. VWAP
        vwap = float(last.get("vwap", close))
        price_vs_vwap = float(last.get("price_vs_vwap", 0))
        if close > vwap and price_vs_vwap < 1.5:
            add_vote("VWAP", 8, 0, f"Price above VWAP {vwap:.2f} by {price_vs_vwap:.2f}% — bullish")
        elif close < vwap and price_vs_vwap > -1.5:
            add_vote("VWAP", 0, 8, f"Price below VWAP {vwap:.2f} by {price_vs_vwap:.2f}% — bearish")
        elif close > vwap:
            add_vote("VWAP", 4, 0, f"Price extended above VWAP — caution")
        else:
            add_vote("VWAP", 0, 4, f"Price extended below VWAP — caution")

        # 10. Consecutive candle reversion
        try:
            closes = df["close"].tail(5).values
            if len(closes) >= 4:
                # 4 red in a row -> long reversion, 4 green -> short
                if all(closes[i] < closes[i - 1] for i in range(1, 4)):
                    add_vote("Consecutive", 9, 0, f"{len(closes)} consecutive red candles — reversion long")
                elif all(closes[i] > closes[i - 1] for i in range(1, 4)):
                    add_vote("Consecutive", 0, 9, f"{len(closes)} consecutive green — reversion short")
                else:
                    add_vote("Consecutive", 4, 4, "No consecutive streak")
            else:
                add_vote("Consecutive", 4, 4, "Insufficient candles")
        except Exception:
            add_vote("Consecutive", 4, 4, "Error")

        # 11. RSI/price divergence
        try:
            bull_div = bool(last.get("rsi_div_bull", False))
            bear_div = bool(last.get("rsi_div_bear", False))
            if bull_div:
                add_vote("Divergence", 11, 0, "Bullish RSI divergence — price lower low, RSI higher low")
            elif bear_div:
                add_vote("Divergence", 0, 11, "Bearish RSI divergence — price higher high, RSI lower high")
            else:
                add_vote("Divergence", 4, 4, "No divergence")
        except Exception:
            add_vote("Divergence", 4, 4, "No divergence data")

    except Exception as e:
        # Fallback
        pass

    long_total = sum(v["long"] for v in votes)
    short_total = sum(v["short"] for v in votes)
    total = long_total + short_total
    if total == 0:
        long_score = 50
        short_score = 50
    else:
        long_score = long_total / total * 100
        short_score = short_total / total * 100

    if long_score > short_score + 12:
        bias = "LONG"
    elif short_score > long_score + 12:
        bias = "SHORT"
    else:
        bias = "NEUTRAL"

    return {
        "long_score": long_score,
        "short_score": short_score,
        "bias": bias,
        "bias_margin": abs(long_score - short_score),
        "votes": votes,
        "total_long": long_total,
        "total_short": short_total,
    }
