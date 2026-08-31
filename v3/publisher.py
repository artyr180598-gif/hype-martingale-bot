"""Publish-time invariant: nothing reaches Telegram/API unless it is valid.

``v3.engine`` already contains the deterministic no-trade gate; this module is
the *second* independent check that the engine (or an AI/rule-based annotation,
or a future code path) did not produce an invalid signal. It is deliberately
cheap and side-effect free except for downgrading invalid signals.
"""

from __future__ import annotations

from v3.config import SignalConfig
from v3.models import TradingSignal
from v3.validator import validate_for_publish


def sanitize_for_publish(signal: TradingSignal, cfg: SignalConfig) -> tuple[TradingSignal, list[str]]:
    """Validate ``signal``; downgrade to NO_TRADE when it fails.

    Returns (signal, violations). A failed signal keeps its analysis payload
    (reasons/risks/features) but becomes ``NO_TRADE`` so it can never be
    presented as an actionable setup.
    """
    ok, violations = validate_for_publish(signal, cfg)
    if ok:
        return signal, []
    if signal.direction in ("LONG", "SHORT"):
        signal.direction = "NO_TRADE"
        signal.status = "NO_TRADE"
        signal.no_trade_reasons = list(dict.fromkeys(signal.no_trade_reasons + violations))[:10]
    return signal, violations
