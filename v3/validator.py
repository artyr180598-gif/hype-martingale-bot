"""Deterministic signal validation before anything reaches Telegram.

AI/rule-based scoring may suggest a direction, but a signal is only publishable
if it passes this gate.  Violations force status ``NO_TRADE`` / ``WATCH``.
"""

from __future__ import annotations

from v3.config import SignalConfig
from v3.models import TradingSignal


def validate_signal(signal: TradingSignal, cfg: SignalConfig) -> list[str]:
    violations: list[str] = []
    if signal.uid == "":
        violations.append("missing uid")
    if signal.symbol == "":
        violations.append("missing symbol")
    if signal.direction not in ("LONG", "SHORT"):
        violations.append(f"direction {signal.direction} is not tradable")
    if signal.score < cfg.QUALITY_MIN:
        violations.append(f"quality {signal.score:.1f} < {cfg.QUALITY_MIN:.0f}")
    if signal.confidence < cfg.CONFIDENCE_MIN:
        violations.append(f"data confidence {signal.confidence:.2f} < {cfg.CONFIDENCE_MIN:.2f}")
    if signal.rr < cfg.MIN_RISK_REWARD:
        violations.append(f"R:R {signal.rr:.2f} < {cfg.MIN_RISK_REWARD:.1f}")
    if signal.risk_score > cfg.MAX_RISK_SCORE_TO_ENTER:
        violations.append(f"risk {signal.risk_score}/10 too high")
    if signal.price <= 0:
        violations.append("price missing/invalid")
    if signal.stop_loss <= 0:
        violations.append("stop loss missing")
    if not signal.targets or len(signal.targets) < 2:
        violations.append("need at least two targets")
    if signal.entry_zone and signal.entry_zone[1] <= 0:
        violations.append("entry zone invalid")
    # инвариант «только реальные данные»: публикуемый сигнал обязан иметь
    # биржевой timestamp (возраст данных), иначе это не реальный рынок
    if signal.data_age_seconds is None:
        violations.append("no real market data (missing exchange timestamp)")
    if signal.stale:
        violations.append("stale market data")
    elif (
        signal.data_age_seconds is not None
        and signal.data_age_seconds > cfg.MAX_DATA_AGE_SECONDS
    ):
        violations.append(f"stale market data ({signal.data_age_seconds:.0f}s old)")
    return violations


def validate_for_publish(signal: TradingSignal, cfg: SignalConfig) -> tuple[bool, list[str]]:
    violations = validate_signal(signal, cfg)
    return (len(violations) == 0), violations
