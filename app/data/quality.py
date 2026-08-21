from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class DataQualityResult:
    valid: bool
    reasons: tuple[str, ...]


class DataQualityEngine:
    """Reject obviously invalid or stale market events before analysis."""

    def validate_price(self, price: Decimal) -> DataQualityResult:
        if price <= 0:
            return DataQualityResult(False, ("non_positive_price",))
        return DataQualityResult(True, ())

    def validate_trade(self, price: Decimal, quantity: Decimal) -> DataQualityResult:
        reasons: list[str] = []
        if price <= 0:
            reasons.append("non_positive_price")
        if quantity <= 0:
            reasons.append("non_positive_quantity")
        return DataQualityResult(not reasons, tuple(reasons))

    def validate_timestamp(
        self, event_time: datetime, received_at: datetime, max_future_seconds: int = 5
    ) -> DataQualityResult:
        delta = (event_time - received_at).total_seconds()
        if delta > max_future_seconds:
            return DataQualityResult(False, ("event_timestamp_in_future",))
        return DataQualityResult(True, ())
