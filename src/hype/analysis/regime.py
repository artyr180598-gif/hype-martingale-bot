"""Market regime detection + BTC context."""

from __future__ import annotations

import pandas as pd

from ..config import Settings


def detect_regime(df: pd.DataFrame, cfg: Settings) -> dict:
    if df.empty or len(df) < 50:
        return {"regime": "UNCERTAIN", "strength": 0, "description": "Недостаточно данных"}

    last = df.iloc[-1]
    try:
        adx = float(last.get("adx", 0))
        atr_pct = float(last.get("atr_pct", 0))
        bb_width = float(last.get("bb_width", 0))
        ema20 = float(last.get("ema20", 0))
        ema50 = float(last.get("ema50", 0))
        ema200 = float(last.get("ema200", 0))
        close = float(last["close"])

        # Volatility regimes
        if atr_pct > cfg.ATR_PCT_HIGH:
            vol_regime = "HIGH_VOLATILITY"
        elif atr_pct < cfg.ATR_PCT_NORMAL_MIN:
            vol_regime = "LOW_VOLATILITY"
        else:
            vol_regime = "NORMAL"

        # Trend
        if ema20 > ema50 > ema200 and close > ema20 and adx > cfg.ADX_TREND_MIN:
            trend = "TRENDING_UP"
        elif ema20 < ema50 < ema200 and close < ema20 and adx > cfg.ADX_TREND_MIN:
            trend = "TRENDING_DOWN"
        elif bb_width < 2.0:
            trend = "RANGING"
        else:
            trend = "UNCERTAIN"

        # Breakout / breakdown
        don_upper = float(last.get("donchian_upper_20", close))
        don_lower = float(last.get("donchian_lower_20", close))
        if close > don_upper and adx > 20:
            regime = "BREAKOUT"
        elif close < don_lower and adx > 20:
            regime = "BREAKDOWN"
        elif vol_regime == "LOW_VOLATILITY" and trend == "RANGING":
            regime = "ACCUMULATION"
        elif vol_regime == "HIGH_VOLATILITY" and trend in ("TRENDING_UP", "TRENDING_DOWN"):
            regime = vol_regime
        else:
            regime = trend

        descriptions = {
            "TRENDING_UP": "Устойчивый восходящий тренд — EMA20>50>200, ADX подтверждает",
            "TRENDING_DOWN": "Устойчивый нисходящий тренд — EMA20<50<200",
            "RANGING": "Боковик — низкая направленность, торгуем от границ",
            "HIGH_VOLATILITY": "Высокая волатильность — широкие стопы, уменьшаем размер",
            "LOW_VOLATILITY": "Низкая волатильность — сжатие, ждем пробоя",
            "BREAKOUT": "Пробой вверх с объемом — импульс",
            "BREAKDOWN": "Пробой вниз — импульс",
            "ACCUMULATION": "Накопление — узкий диапазон, возможен скорый импульс",
            "UNCERTAIN": "Неопределенность — конфликт сигналов",
        }

        return {
            "regime": regime,
            "trend": trend,
            "volatility": vol_regime,
            "adx": adx,
            "atr_pct": atr_pct,
            "bb_width": bb_width,
            "strength": min(100, adx * 2),
            "description": descriptions.get(regime, regime),
        }
    except Exception as e:
        return {"regime": "UNCERTAIN", "strength": 0, "description": f"Ошибка режима: {e}"}


def btc_context_filter(btc_df: pd.DataFrame, alt_bias: str, cfg: Settings) -> dict:
    """
    BTC regime filter: if BTC in strong downtrend, penalize LONG alts, etc.
    Ported from multi-coin scanner BTC filter.
    """
    if btc_df.empty:
        return {"penalty": 0, "reason": "Нет данных BTC"}

    try:
        last = btc_df.iloc[-1]
        btc_trend = "UP" if float(last.get("ema20", 0)) > float(last.get("ema50", 0)) else "DOWN"
        btc_adx = float(last.get("adx", 0))
        btc_rsi = float(last.get("rsi", 50))

        penalty = 0
        reasons = []

        if alt_bias == "LONG" and btc_trend == "DOWN" and btc_adx > 25:
            penalty = 12
            reasons.append(f"BTC downtrend ADX {btc_adx:.1f} — штраф LONG альтов")
        elif alt_bias == "SHORT" and btc_trend == "UP" and btc_adx > 25:
            penalty = 12
            reasons.append(f"BTC uptrend ADX {btc_adx:.1f} — штраф SHORT альтов")

        if btc_rsi > 75 or btc_rsi < 25:
            penalty += 5
            reasons.append(f"BTC RSI extreme {btc_rsi:.1f} — повышенный риск")

        return {"penalty": penalty, "btc_trend": btc_trend, "btc_adx": btc_adx, "btc_rsi": btc_rsi, "reasons": reasons}
    except Exception:
        return {"penalty": 0, "reason": "Ошибка BTC контекста"}
