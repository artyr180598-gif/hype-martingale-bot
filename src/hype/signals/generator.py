"""Signal generator — converts analysis dict to Signal model."""

from __future__ import annotations

from ..config import Settings
from .models import Signal


def analysis_to_signal(analysis: dict, cfg: Settings) -> Signal | None:
    if not analysis:
        return None

    symbol = analysis.get("symbol", "UNKNOWN")
    exchange = analysis.get("exchange", cfg.PRIMARY_EXCHANGE)
    status = analysis.get("status", "NO_DATA")

    if status in ("NO_DATA", "NO_CANDLES"):
        return None

    direction = analysis.get("direction", "LONG")
    if direction not in ("LONG", "SHORT"):
        # If NO_TRADE, still try to keep direction if available
        direction = "LONG"

    ticker = analysis.get("ticker")
    entry = analysis.get("entry") or (ticker.last if ticker else 0)

    risk_plan = analysis.get("risk_plan")
    quality = analysis.get("quality", {})
    confidence = analysis.get("confidence", {})
    expected = analysis.get("expected_move", {})
    regime = analysis.get("regime", {})
    confluence = analysis.get("confluence", {})
    early = analysis.get("early_impulse", {})
    orderbook = analysis.get("orderbook", {})

    if not risk_plan:
        return None

    signal = Signal(
        symbol=symbol,
        exchange=exchange,
        direction=direction,
        status=status,
        entry=entry,
        entry_zone_low=risk_plan.entry_zone_low,
        entry_zone_high=risk_plan.entry_zone_high,
        stop_loss=risk_plan.stop_loss,
        take_profits=risk_plan.take_profits,
        tp_pcts=risk_plan.take_profit_pcts,
        risk_reward=risk_plan.risk_reward,
        risk_score=analysis.get("risk_score", 5),
        quality_score=quality.get("score", 0),
        quality_grade=quality.get("grade", "D"),
        confidence_pct=confidence.get("confidence", 0),
        confidence_label=confidence.get("label", "низкая"),
        expected_move_pct=expected.get("pct", 0),
        expected_target=expected.get("target_price", entry),
        regime=regime.get("regime", "UNCERTAIN"),
        timeframe=cfg.ENTRY_TF,
        exchange_source=exchange,
        reasons=quality.get("reasons", []) + confluence.get("votes", [])[:2],
        confluence_bias=confluence.get("bias", ""),
        signals_detected=analysis.get("signals", []),
        orderbook_imbalance=orderbook.get("imbalance", 0.5),
        early_phase=early.get("phase", "WATCH"),
        heat=early.get("heat", 0),
        data_completeness=analysis.get("data_completeness", 0),
        no_trade_reasons=analysis.get("no_trade_reasons", []),
        raw_analysis=analysis,
    )

    return signal
