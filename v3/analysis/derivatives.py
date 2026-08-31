"""Perpetual-futures derivatives analysis.

The signal engine treats derivatives as *evidence*, never as a standalone
trigger.  This module turns funding, open interest and liquidation data into a
``DerivativesSnapshot`` that the scorer can weigh.
"""

from __future__ import annotations

from v3.config import SignalConfig
from v3.models import DataBundle, DerivativesSnapshot


def analyze_derivatives(bundle: DataBundle, cfg: SignalConfig) -> DerivativesSnapshot:
    funding = bundle.funding_rate
    hist = [float(x) for x in bundle.funding_history if x is not None]
    funding_trend = classify_funding(funding, hist, cfg)

    buy_liq = 0.0
    sell_liq = 0.0
    liq_count = 0
    for item in bundle.liquidations:
        size = float(item.get("size", 0) or 0)
        side = str(item.get("side", "")).lower()
        if size > 0:
            liq_count += 1
        if side in ("buy",):  # liquidated longs -> sell pressure
            buy_liq += size
        elif side in ("sell", "short"):
            sell_liq += size

    total_liq = buy_liq + sell_liq
    imbalance = (sell_liq - buy_liq) / total_liq if total_liq > 0 else 0.0

    score = 50.0
    if funding is not None:
        if funding > cfg.FUNDING_OVERHEATED:
            score -= 20.0
        elif funding < cfg.FUNDING_OVERBURDENED_SHORT:
            score += 15.0
        elif -cfg.FUNDING_OVERBURDENED_SHORT * 0.5 <= funding <= cfg.FUNDING_OVERHEATED * 0.5:
            score += 10.0
    if hist:
        avg = sum(hist) / len(hist)
        if funding is not None and funding > avg:
            score += 5.0
        elif funding is not None and funding < avg:
            score -= 5.0
    if total_liq > 0:
        if imbalance > 0.4 and funding is not None and funding < 0:
            score += 10.0
        elif imbalance < -0.4:
            score -= 10.0

    note_bits: list[str] = []
    if funding is not None:
        note_bits.append(f"funding {funding * 100:.3f}%/{funding_trend}")
    if bundle.open_interest_usd:
        note_bits.append(f"OI ${bundle.open_interest_usd / 1e6:.1f}M")
    if total_liq > 0:
        note_bits.append(f"liqs ${total_liq / 1e6:.1f}M (imbalance {imbalance:+.2f})")

    return DerivativesSnapshot(
        funding_rate=funding,
        funding_trend=funding_trend,
        funding_history=hist[-12:],
        open_interest_usd=bundle.open_interest_usd,
        oi_change_24h_pct=bundle.open_interest_history[-1][1] if bundle.open_interest_history else None,
        liq_buy_usd=buy_liq,
        liq_sell_usd=sell_liq,
        liq_imbalance=round(imbalance, 3),
        liq_count=liq_count,
        score=round(min(100.0, max(0.0, score)), 1),
        note=" | ".join(note_bits),
    )


def classify_funding(rate: float | None, history: list[float], cfg: SignalConfig) -> str:
    if rate is None:
        return "unknown"
    if rate > cfg.FUNDING_OVERHEATED:
        return "overheated_long"
    if rate < cfg.FUNDING_OVERBURDENED_SHORT:
        return "overheated_short"
    if history and len(history) >= 3:
        avg = sum(history[-3:]) / len(history[-3:])
        if rate > avg + 0.0002:
            return "rising"
        if rate < avg - 0.0002:
            return "falling"
    return "neutral"
