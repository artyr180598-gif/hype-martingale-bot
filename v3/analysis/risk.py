"""Risk management.

Order of operations (hard constraint):
  1. compute a risk score from real data;
  2. derive leverage from volatility (never from "confidence");
  3. target risk in money = deposit * RISK_PER_TRADE_PCT;
  4. position size = risk / stop-distance, capped by MAX_POSITION_PCT;
  5. if the plan violates MIN_RISK_REWARD or risk-score ceiling -> NO TRADE.
"""

from __future__ import annotations

from v3.config import SignalConfig
from v3.models import (
    DataBundle,
    DerivativesSnapshot,
    MarketContext,
    OrderFlowSnapshot,
    RegimeSnapshot,
    RiskBrief,
    TimeframeView,
    TradeLevels,
)


def risk_score(
    bundle: DataBundle,
    views: list[TimeframeView],
    derivatives: DerivativesSnapshot,
    orderflow: OrderFlowSnapshot,
    context: MarketContext,
    regime: RegimeSnapshot,
    cfg: SignalConfig,
) -> tuple[int, list[str]]:
    score = 1.0
    why: list[str] = []

    if bundle.is_demo:
        score += 1.5
        why.append("demo data +1.5")
    entry = views[0] if views else None
    if entry is not None:
        if entry.atr_pct >= cfg.ATR_PCT_EXTREME:
            score += 2.0
            why.append("extreme volatility +2.0")
        elif entry.atr_pct >= cfg.ATR_PCT_HIGH:
            score += 1.2
            why.append("high volatility +1.2")
        elif entry.atr_pct <= cfg.ATR_PCT_NORMAL_MIN:
            score += 0.6
            why.append("dead volatility +0.6")

    if orderflow.liquidity_grade in ("thin", "empty"):
        score += 1.5 if orderflow.liquidity_grade == "thin" else 2.5
        why.append(f"thin/empty liquidity +{1.5 if orderflow.liquidity_grade == 'thin' else 2.5}")
    if orderflow.spread_pct is not None and orderflow.spread_pct > cfg.MAX_SPREAD_PCT:
        score += 1.2
        why.append("wide spread +1.2")

    if derivatives.funding_trend == "overheated_long":
        score += 1.0
        why.append("overheated long funding +1.0")
    if derivatives.funding_trend == "overheated_short":
        score += 1.0
        why.append("overheated short funding +1.0")

    if regime.regime in ("HIGH_VOLATILITY", "UNCERTAIN"):
        score += 0.8
        why.append("uncertain/high-vol regime +0.8")
    if regime.conflicts:
        score += 0.8
        why.append("timeframe conflict +0.8")

    if context.btc_trend == "down" and has_long(views):
        score += 0.7
        why.append("BTC down against long +0.7")
    if context.btc_trend == "up" and has_short(views):
        score += 0.7
        why.append("BTC up against short +0.7")

    degraded_count = len(bundle.degraded)
    score += min(1.0, 0.2 * degraded_count)
    why.append(f"degraded data ({degraded_count}) +{min(1.0, 0.2 * degraded_count):.1f}")

    return int(round(min(10.0, max(1.0, score)))), why


def has_long(views: list[TimeframeView]) -> bool:
    return any(v.trend == "up" for v in views)


def has_short(views: list[TimeframeView]) -> bool:
    return any(v.trend == "down" for v in views)


def recommended_leverage(atr_pct: float, cfg: SignalConfig) -> int:
    if atr_pct <= 0:
        return 1
    raw = int(2.0 / max(atr_pct, 0.2))
    return max(1, min(cfg.MAX_LEVERAGE, raw))


def build_risk_brief(
    deposit_usd: float,
    entry_price: float,
    stop_price: float,
    risk_score_value: int,
    atr_pct: float,
    cfg: SignalConfig,
) -> RiskBrief:
    risk_pct = cfg.RISK_PER_TRADE_PCT
    if risk_score_value >= 8:
        risk_pct *= 0.25
    elif risk_score_value >= 6:
        risk_pct *= 0.5
    elif risk_score_value <= 2:
        risk_pct *= 1.2
    risk_pct = min(risk_pct, cfg.RISK_PER_TRADE_PCT * 1.5)

    leverage = recommended_leverage(atr_pct, cfg)
    distance = abs(entry_price - stop_price)
    warnings: list[str] = []

    if deposit_usd <= 0 or entry_price <= 0 or distance <= 0:
        return RiskBrief(
            risk_score=risk_score_value,
            leverage=leverage,
            max_deposit_pct=risk_pct,
            warnings=warnings,
        )

    risk_usd = deposit_usd * risk_pct / 100.0
    qty = risk_usd / distance
    notional = qty * entry_price
    max_notional = deposit_usd * cfg.MAX_POSITION_PCT / 100.0 * leverage
    if notional > max_notional:
        notional = max_notional
        qty = notional / entry_price
        risk_usd = qty * distance
        warnings.append(f"position capped at {cfg.MAX_POSITION_PCT:.0f}% of deposit")
    margin = notional / leverage

    # rough isolated-margin liquidation distance using the standard formula
    liq = None
    if leverage > 1:
        ratio = 1.0 / leverage if leverage else 0.0
        if entry_price > stop_price:  # long
            liq = entry_price * (1 - min(0.95, ratio * 0.92))
        else:
            liq = entry_price * (1 + min(0.95, ratio * 0.92))

    if risk_usd <= 0:
        warnings.append("zero risk money after capping")

    return RiskBrief(
        risk_score=risk_score_value,
        risk_usd=round(risk_usd, 2),
        position_pct=round(notional / deposit_usd * 100.0, 2) if deposit_usd else 0.0,
        position_usd=round(notional, 2),
        qty=round(qty, 8),
        margin_usd=round(margin, 2),
        leverage=leverage,
        liquidation_price=round(liq, 8) if liq is not None else None,
        warnings=warnings,
        max_deposit_pct=round(risk_pct, 2),
    )
