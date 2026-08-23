"""
Data Quality Engine — Verifies data completeness, detects gaps, bad ticks, and stale data.
"""
from src.config.constants import DataQualityStatus
from src.core.logging import get_logger
from src.core.time_utils import timeframe_to_ms, utc_now_ms
from src.data.models import CandleData, DataQualityReport

logger = get_logger("data.quality")


class DataQualityEngine:
    """
    Validates market data feeds before feature engineering or signal generation.
    Enforces the mission rule: NO TRADING ON DEGRADED DATA.
    """

    @staticmethod
    def validate_candles(candles: list[CandleData], timeframe: str) -> DataQualityReport:
        if not candles:
            return DataQualityReport(
                symbol="UNKNOWN",
                timeframe=timeframe,
                total_candles=0,
                missing_candles_count=0,
                duplicate_candles_count=0,
                invalid_price_count=0,
                gap_percentage=100.0,
                quality_score=0.0,
                status=DataQualityStatus.INVALID,
                is_acceptable_for_trading=False,
                issues=["Candle list is completely empty"],
            )

        symbol = candles[0].symbol
        bar_ms = timeframe_to_ms(timeframe)
        total_candles = len(candles)
        issues: list[str] = []

        # 1. Check duplicate timestamps & monotonicity
        seen_timestamps = set()
        duplicate_count = 0
        non_monotonic_count = 0
        invalid_price_count = 0

        for i, c in enumerate(candles):
            # Check price anomalies
            if c.high < c.low or c.open <= 0 or c.high <= 0 or c.low <= 0 or c.close <= 0:
                invalid_price_count += 1
            if c.open > c.high or c.open < c.low or c.close > c.high or c.close < c.low:
                invalid_price_count += 1
            if c.volume < 0:
                invalid_price_count += 1

            if c.timestamp_ms in seen_timestamps:
                duplicate_count += 1
            seen_timestamps.add(c.timestamp_ms)

            if i > 0 and c.timestamp_ms <= candles[i - 1].timestamp_ms:
                non_monotonic_count += 1

        # 2. Check for missing time gaps
        start_ts = candles[0].timestamp_ms
        end_ts = candles[-1].timestamp_ms
        expected_bars = max(1, int((end_ts - start_ts) / bar_ms) + 1)
        missing_count = max(0, expected_bars - len(seen_timestamps))
        gap_percentage = round((missing_count / expected_bars) * 100.0, 2)

        # 3. Check for staleness against current clock
        now_ms = utc_now_ms()
        time_since_last_bar_ms = now_ms - end_ts
        is_stale = time_since_last_bar_ms > (bar_ms * 3)  # More than 3 bars behind

        if duplicate_count > 0:
            issues.append(f"Found {duplicate_count} duplicate candles")
        if non_monotonic_count > 0:
            issues.append(f"Found {non_monotonic_count} out-of-order candles")
        if invalid_price_count > 0:
            issues.append(f"Found {invalid_price_count} price/volume anomaly bars")
        if gap_percentage > 5.0:
            issues.append(f"Missing {missing_count} candles ({gap_percentage}% gap)")
        if is_stale:
            issues.append(f"Data is stale: last bar was {time_since_last_bar_ms // 1000}s ago")

        # Score calculation (0.0 to 1.0)
        penalty = 0.0
        penalty += min(0.4, (gap_percentage / 100.0) * 2.0)
        if duplicate_count > 0:
            penalty += min(0.2, duplicate_count / total_candles)
        if invalid_price_count > 0:
            penalty += min(0.3, invalid_price_count / total_candles)
        if is_stale:
            penalty += 0.2

        quality_score = max(0.0, round(1.0 - penalty, 3))

        # Status assignment
        if quality_score >= 0.95 and not issues:
            status = DataQualityStatus.EXCELLENT
            acceptable = True
        elif quality_score >= 0.80 and gap_percentage <= 3.0 and not is_stale:
            status = DataQualityStatus.GOOD
            acceptable = True
        elif quality_score >= 0.50:
            status = DataQualityStatus.DEGRADED
            acceptable = False
        else:
            status = DataQualityStatus.INVALID
            acceptable = False

        return DataQualityReport(
            symbol=symbol,
            timeframe=timeframe,
            total_candles=total_candles,
            missing_candles_count=missing_count,
            duplicate_candles_count=duplicate_count,
            invalid_price_count=invalid_price_count,
            gap_percentage=gap_percentage,
            quality_score=quality_score,
            status=status,
            is_acceptable_for_trading=acceptable,
            issues=issues,
        )
