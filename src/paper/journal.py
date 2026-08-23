"""
Signal Journal and Post-Trade Maximum Favorable/Adverse Excursion (MFE/MAE) Tracker.
"""
from dataclasses import dataclass
from typing import Any

from src.core.time_utils import utc_now_ms
from src.signals.models import SignalSetup


@dataclass
class JournaledSignal:
    signal: SignalSetup
    status: str              # "ACTIVE", "WIN", "LOSS", "EXPIRED"
    max_favorable_pct: float # Peak price move in signal direction
    max_adverse_pct: float   # Maximum drawdown move against signal
    closed_at_ms: int | None = None
    outcome_r: float | None = None


class SignalJournal:
    """
    Logs all generated signals to evaluate empirical accuracy over time.
    """

    def __init__(self):
        self._journal: dict[str, JournaledSignal] = {}

    def record_signal(self, signal: SignalSetup) -> None:
        if signal.signal_id not in self._journal:
            self._journal[signal.signal_id] = JournaledSignal(
                signal=signal,
                status="ACTIVE",
                max_favorable_pct=0.0,
                max_adverse_pct=0.0,
            )

    def update_price(self, symbol: str, current_price: float) -> None:
        for entry in self._journal.values():
            if entry.signal.symbol == symbol and entry.status == "ACTIVE":
                sig = entry.signal
                entry_p = sig.entry_price

                if sig.direction.value == "LONG":
                    favorable = ((current_price - entry_p) / entry_p) * 100.0
                    adverse = ((entry_p - current_price) / entry_p) * 100.0
                else:
                    favorable = ((entry_p - current_price) / entry_p) * 100.0
                    adverse = ((current_price - entry_p) / entry_p) * 100.0

                if favorable > entry.max_favorable_pct:
                    entry.max_favorable_pct = round(favorable, 2)
                if adverse > entry.max_adverse_pct:
                    entry.max_adverse_pct = round(adverse, 2)

                # Check completion
                if sig.direction.value == "LONG":
                    if current_price >= sig.take_profit_1:
                        entry.status = "WIN"
                        entry.outcome_r = 1.5
                        entry.closed_at_ms = utc_now_ms()
                    elif current_price <= sig.stop_loss:
                        entry.status = "LOSS"
                        entry.outcome_r = -1.0
                        entry.closed_at_ms = utc_now_ms()
                elif sig.direction.value == "SHORT":
                    if current_price <= sig.take_profit_1:
                        entry.status = "WIN"
                        entry.outcome_r = 1.5
                        entry.closed_at_ms = utc_now_ms()
                    elif current_price >= sig.stop_loss:
                        entry.status = "LOSS"
                        entry.outcome_r = -1.0
                        entry.closed_at_ms = utc_now_ms()

    def get_summary(self) -> dict[str, Any]:
        total = len(self._journal)
        wins = sum(1 for e in self._journal.values() if e.status == "WIN")
        losses = sum(1 for e in self._journal.values() if e.status == "LOSS")
        active = sum(1 for e in self._journal.values() if e.status == "ACTIVE")
        completed = wins + losses
        win_rate = (wins / max(1, completed)) * 100.0

        return {
            "total_signals": total,
            "active_signals": active,
            "completed_signals": completed,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(win_rate, 1),
        }
