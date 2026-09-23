"""Signal models — final signal representation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal


@dataclass
class Signal:
    symbol: str
    exchange: str
    direction: Literal["LONG", "SHORT"]
    status: str  # SIGNAL / NO_TRADE
    entry: float
    entry_zone_low: float
    entry_zone_high: float
    stop_loss: float
    take_profits: list[float]
    tp_pcts: list[float]
    risk_reward: float
    risk_score: int
    quality_score: float
    quality_grade: str
    confidence_pct: float
    confidence_label: str
    expected_move_pct: float
    expected_target: float
    regime: str
    timeframe: str
    exchange_source: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reasons: list[str] = field(default_factory=list)
    confluence_bias: str = ""
    signals_detected: list[dict] = field(default_factory=list)
    orderbook_imbalance: float = 0.5
    early_phase: str = "WATCH"
    heat: float = 0
    data_completeness: float = 0
    no_trade_reasons: list[str] = field(default_factory=list)
    raw_analysis: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "direction": self.direction,
            "status": self.status,
            "entry": self.entry,
            "entry_zone": [self.entry_zone_low, self.entry_zone_high],
            "stop_loss": self.stop_loss,
            "take_profits": self.take_profits,
            "tp_pcts": self.tp_pcts,
            "risk_reward": self.risk_reward,
            "risk_score": self.risk_score,
            "quality_score": self.quality_score,
            "quality_grade": self.quality_grade,
            "confidence_pct": self.confidence_pct,
            "confidence_label": self.confidence_label,
            "expected_move_pct": self.expected_move_pct,
            "expected_target": self.expected_target,
            "regime": self.regime,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp.isoformat(),
            "reasons": self.reasons,
            "signals_detected": self.signals_detected,
            "early_phase": self.early_phase,
            "heat": self.heat,
        }
