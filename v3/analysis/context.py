"""Market-wide context (BTC, dominance, global risk sentiment).

Alts are never analysed in isolation: a strong BTC move or fear/dominance
shift can invalidate an otherwise good local setup.  Context is a *penalty /
confidence modifier*, by design it can never generate a signal on its own.
"""

from __future__ import annotations

from v3.config import SignalConfig
from v3.models import DataBundle, MarketContext, TimeframeView


def build_context(bundle: DataBundle, btc_view: TimeframeView | None, cfg: SignalConfig) -> MarketContext:
    if btc_view is None or bundle.btc_price_24h_pct is None:
        return MarketContext(degraded=["BTC context unavailable"])

    btc_score = 50.0
    if btc_view.trend == "up":
        btc_score += 18.0
    elif btc_view.trend == "down":
        btc_score -= 18.0
    else:
        btc_score -= 5.0
    if btc_view.adx >= cfg.ADX_TREND_MIN:
        btc_score += 8.0
    if abs(bundle.btc_price_24h_pct) > 4.0:
        btc_score -= 8.0  # fast move = risk for alts
    btc_score = max(0.0, min(100.0, btc_score))

    direction = (
        "up" if btc_view.trend == "up"
        else "down" if btc_view.trend == "down"
        else "flat"
    )
    degraded: list[str] = []
    if bundle.btc_dominance is None:
        degraded.append("dominance unavailable")
    if bundle.global_change_pct is None:
        degraded.append("global market change unavailable")

    return MarketContext(
        btc_trend=direction,
        btc_adx=round(btc_view.adx, 1),
        btc_volatility=round(btc_view.atr_pct, 3),
        btc_score=round(btc_score, 1),
        dominance=bundle.btc_dominance,
        global_change_pct=bundle.global_change_pct,
        sentiment=bundle.news_sentiment,
        degraded=degraded,
    )
