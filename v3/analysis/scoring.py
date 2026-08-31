"""Interpretable multi-factor signal scoring.

Scores are features, not opinions.  Each factor is mapped to ``[0,1]``
deterministically from market data.  The weighted sum is the signal quality;
the breakdown is stored with every signal so an admin can answer "why 82/100".
"""

from __future__ import annotations

from v3.config import SignalConfig
from v3.models import (
    DataBundle,
    DerivativesSnapshot,
    FactorScore,
    MarketContext,
    OrderFlowSnapshot,
    RegimeSnapshot,
    ScoreBreakdown,
    TimeframeView,
    TradeLevels,
)


def _clip(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def score_signal(
    bundle: DataBundle,
    views: list[TimeframeView],
    derivatives: DerivativesSnapshot,
    orderflow: OrderFlowSnapshot,
    context: MarketContext,
    regime: RegimeSnapshot,
    levels: TradeLevels | None,
    risk_score: int,
    direction: str,
    cfg: SignalConfig,
) -> ScoreBreakdown:
    weights = {
        "Trend Alignment": 15.0,
        "Market Structure": 15.0,
        "Momentum": 15.0,
        "Volume": 12.0,
        "Volatility": 10.0,
        "Order Flow": 10.0,
        "Derivatives": 10.0,
        "Liquidity": 6.0,
        "Market Context": 7.0,
    }

    factors: list[FactorScore] = []

    # ── Trend alignment ─────────────────────────────────────────
    scores = []
    for i, v in enumerate(views):
        weight = 1.0 + i * 0.35
        if direction == "LONG":
            scores.append(1.0 if v.trend == "up" else 0.45 if v.trend == "range" else 0.0 if v.adx < cfg.ADX_TREND_MIN else 0.15)
        elif direction == "SHORT":
            scores.append(1.0 if v.trend == "down" else 0.45 if v.trend == "range" else 0.0 if v.adx < cfg.ADX_TREND_MIN else 0.15)
        else:
            scores.append(1.0 if v.trend == "range" else 0.35)
    trend_alignment = _avg(scores)
    factors.append(FactorScore("Trend Alignment", _avg(scores), 1.0, weights["Trend Alignment"], weights["Trend Alignment"] * trend_alignment))

    # ── Market structure ────────────────────────────────────────
    structure_raw = 0.5
    macro = views[-1] if views else None
    if macro is not None:
        if direction == "LONG" and macro.trend == "up":
            structure_raw = 0.85
        elif direction == "SHORT" and macro.trend == "down":
            structure_raw = 0.85
        elif macro.trend == "range":
            structure_raw = 0.55
    count_bos = sum(1 for v in views if v.structure_signal in ("BOS_UP", "CHoCH_UP") if direction == "LONG")
    count_bos_down = sum(1 for v in views if v.structure_signal in ("BOS_DOWN", "CHoCH_DOWN") if direction == "SHORT")
    if (direction == "LONG" and count_bos >= 2) or (direction == "SHORT" and count_bos_down >= 2):
        structure_raw += 0.1
    structure_raw = _clip(structure_raw)
    factors.append(FactorScore("Market Structure", structure_raw, 1.0, weights["Market Structure"], weights["Market Structure"] * structure_raw))

    # ── Momentum ────────────────────────────────────────────────
    entry = views[0] if views else None
    mom = 0.5
    if entry is not None:
        rsi_ok = (cfg.RSI_OVERSOLD < entry.rsi < cfg.RSI_OVERBOUGHT)
        if direction == "LONG":
            mom = 0.35 * (1 if entry.macd_hist > 0 else 0.2) + 0.35 * (1 if entry.supertrend > 0 else 0.2) + 0.3 * (1 if rsi_ok and entry.rsi >= 50 else 0.25)
        elif direction == "SHORT":
            mom = 0.35 * (1 if entry.macd_hist < 0 else 0.2) + 0.35 * (1 if entry.supertrend < 0 else 0.2) + 0.3 * (1 if rsi_ok and entry.rsi <= 50 else 0.25)
    mom = _clip(mom)
    factors.append(FactorScore("Momentum", mom, 1.0, weights["Momentum"], weights["Momentum"] * mom))

    # ── Volume ──────────────────────────────────────────────────
    vol_raw = 0.5
    if entry is not None:
        if direction == "LONG":
            vol_raw = _clip((entry.vol_z - -0.5) / 3.5 if entry.vol_z >= 0 else 0.35)
        elif direction == "SHORT":
            vol_raw = _clip((-entry.vol_z - -0.5) / 3.5 if entry.vol_z <= 0 else 0.35)
        else:
            vol_raw = 0.5
    vol_raw = max(vol_raw, 0.25)
    factors.append(FactorScore("Volume", vol_raw, 1.0, weights["Volume"], weights["Volume"] * vol_raw))

    # ── Volatility ──────────────────────────────────────────────
    vol_raw = 0.5
    if entry is not None:
        if entry.atr_pct >= cfg.ATR_PCT_EXTREME:
            vol_raw = 0.25
        elif entry.atr_pct >= cfg.ATR_PCT_HIGH:
            vol_raw = 0.45
        elif entry.atr_pct <= cfg.ATR_PCT_NORMAL_MIN:
            vol_raw = 0.3
        else:
            vol_raw = 0.8 if entry.squeeze else 0.6
    factors.append(FactorScore("Volatility", vol_raw, 1.0, weights["Volatility"], weights["Volatility"] * vol_raw))

    # ── Order flow / liquidity ──────────────────────────────────
    of_raw = orderflow.score / 100.0
    factors.append(FactorScore("Order Flow", of_raw, 1.0, weights["Order Flow"], weights["Order Flow"] * of_raw))

    # ── Derivatives ─────────────────────────────────────────────
    der_raw = derivatives.score / 100.0
    factors.append(FactorScore("Derivatives", der_raw, 1.0, weights["Derivatives"], weights["Derivatives"] * der_raw))

    # ── Liquidity ───────────────────────────────────────────────
    liq_raw = 0.5
    if orderflow.liquidity_grade == "excellent":
        liq_raw = 1.0
    elif orderflow.liquidity_grade == "ok":
        liq_raw = 0.8
    elif orderflow.liquidity_grade == "thin":
        liq_raw = 0.4
    else:
        liq_raw = 0.15
    if bundle.turnover_24h < cfg.SCAN_MIN_TURNOVER_USD:
        liq_raw -= 0.25
    liq_raw = _clip(liq_raw)
    factors.append(FactorScore("Liquidity", liq_raw, 1.0, weights["Liquidity"], weights["Liquidity"] * liq_raw))

    # ── Market context (BTC + ETH) ───────────────────────────────
    btc_raw = _clip(context.btc_score / 100.0)
    if direction == "LONG" and context.btc_trend == "down":
        btc_raw = _clip(btc_raw - 0.25)
    if direction == "SHORT" and context.btc_trend == "up":
        btc_raw = _clip(btc_raw - 0.25)
    eth_raw = _clip(context.eth_score / 100.0)
    if context.eth_24h_pct is None:
        ctx_raw = btc_raw
    else:
        ctx_raw = _clip(0.7 * btc_raw + 0.3 * eth_raw)
    factors.append(FactorScore("Market Context", ctx_raw, 1.0, weights["Market Context"], weights["Market Context"] * ctx_raw))

    total = sum(f.value for f in factors)

    # ── Risk penalties ──────────────────────────────────────────
    penalties: dict[str, float] = {}
    if risk_score >= cfg.MAX_RISK_SCORE_TO_ENTER:
        penalties["risk_score"] = 8.0
    if regime.regime in ("UNCERTAIN", "HIGH_VOLATILITY"):
        penalties["uncertain_regime"] = 4.0
    if regime.conflicts:
        penalties["timeframe_conflict"] = 5.0
    if levels is None or levels.rr < cfg.MIN_RISK_REWARD:
        penalties["poor_risk_reward"] = 6.0
    if orderflow.spread_pct is not None and orderflow.spread_pct > cfg.MAX_SPREAD_PCT:
        penalties["wide_spread"] = 5.0
    if derivatives.funding_trend == "overheated_long" and direction == "LONG":
        penalties["overheated_funding"] = 5.0
    if derivatives.funding_trend == "overheated_short" and direction == "SHORT":
        penalties["overheated_funding"] = 5.0
    if bundle.is_demo:
        penalties["demo_data"] = 10.0
    if context.btc_trend == "flat" and direction != "NO_TRADE":
        penalties["btc_no_trend"] = 2.0
    penalty_total = sum(penalties.values())

    notes = []
    if regime.conflicts:
        notes.append("timeframe conflict detected")
    if derivatives.funding_trend == "overheated_long":
        notes.append("funding is overheated long")

    return ScoreBreakdown(
        total=round(min(100.0, max(0.0, total - penalty_total)), 1),
        factors=[FactorScore(f.name, round(f.raw, 3), f.max, f.weight, round(f.value, 2)) for f in factors],
        penalties=penalties,
        notes=notes,
    )


def tier_from_quality(quality: float, cfg: SignalConfig) -> str:
    if quality >= cfg.S_TIER_MIN:
        return "S"
    if quality >= cfg.A_TIER_MIN:
        return "A"
    if quality >= cfg.B_TIER_MIN:
        return "B"
    if quality >= cfg.C_TIER_MIN:
        return "C"
    return "NONE"
