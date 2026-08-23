"""
Historical Data Downloader and Local Dataset Manager.
"""
import asyncio

from src.core.logging import get_logger
from src.core.time_utils import timeframe_to_ms, utc_now_ms
from src.data.adapters.base import BaseExchangeAdapter
from src.data.adapters.binance import BinanceFuturesAdapter
from src.data.models import CandleData
from src.data.quality import DataQualityEngine
from src.database.connection import get_db_session
from src.database.repositories import CandleRepository

logger = get_logger("data.downloader")


class HistoricalDataDownloader:
    """
    Downloads, validates, deduplicates, and caches historical futures data.
    """

    def __init__(self, adapter: BaseExchangeAdapter | None = None):
        self.adapter = adapter or BinanceFuturesAdapter()

    async def download_range(
        self,
        symbol: str,
        timeframe: str,
        start_time_ms: int,
        end_time_ms: int,
        batch_limit: int = 1000,
    ) -> list[CandleData]:
        """
        Download historical candles across a large time window by paginating through batches.
        """
        bar_ms = timeframe_to_ms(timeframe)
        current_start = start_time_ms
        all_candles: list[CandleData] = []
        seen_ts = set()

        logger.info(
            "Starting historical data download",
            symbol=symbol,
            timeframe=timeframe,
            start_ms=start_time_ms,
            end_ms=end_time_ms,
        )

        while current_start < end_time_ms:
            batch = await self.adapter.fetch_klines(
                symbol=symbol,
                timeframe=timeframe,
                limit=batch_limit,
                start_time_ms=current_start,
                end_time_ms=end_time_ms,
            )

            if not batch:
                break

            new_bars = 0
            for bar in batch:
                if bar.timestamp_ms not in seen_ts and bar.timestamp_ms <= end_time_ms:
                    seen_ts.add(bar.timestamp_ms)
                    all_candles.append(bar)
                    new_bars += 1

            if new_bars == 0:
                break

            last_ts = batch[-1].timestamp_ms
            if last_ts <= current_start:
                break
            current_start = last_ts + bar_ms
            await asyncio.sleep(0.05)  # Soft rate limiting between historical batches

        all_candles.sort(key=lambda x: x.timestamp_ms)

        # Validate quality
        report = DataQualityEngine.validate_candles(all_candles, timeframe)
        logger.info(
            "Download completed",
            symbol=symbol,
            timeframe=timeframe,
            total_bars=len(all_candles),
            quality_score=report.quality_score,
            status=report.status.value,
        )
        return all_candles

    async def get_or_download_candles(
        self,
        symbol: str,
        timeframe: str,
        lookback_bars: int = 500,
        end_time_ms: int | None = None,
    ) -> list[CandleData]:
        """
        Retrieve candles from database if available; download from exchange if missing.
        """
        end_ms = end_time_ms or utc_now_ms()
        bar_ms = timeframe_to_ms(timeframe)
        start_ms = end_ms - (lookback_bars * bar_ms)

        # Try to load from database
        try:
            async for session in get_db_session():
                repo = CandleRepository(session)
                db_candles = await repo.get_candles(symbol, timeframe, limit=lookback_bars, end_time_ms=end_ms)
                if len(db_candles) >= lookback_bars * 0.9:
                    return [
                        CandleData(
                            symbol=str(c.symbol),
                            timeframe=str(c.timeframe),
                            timestamp_ms=int(c.timestamp_ms),
                            open=float(c.open),
                            high=float(c.high),
                            low=float(c.low),
                            close=float(c.close),
                            volume=float(c.volume),
                            quote_volume=float(c.quote_volume),
                            trades_count=int(c.trades_count),
                            taker_buy_volume=float(c.taker_buy_volume),
                        )
                        for c in db_candles
                    ]
        except Exception as e:
            logger.debug("Database read bypassed", error=str(e))

        # Download from exchange
        candles = await self.download_range(symbol, timeframe, start_ms, end_ms)

        # Persist to database in background
        if candles:
            try:
                async for session in get_db_session():
                    repo = CandleRepository(session)
                    candles_dicts = [c.model_dump() for c in candles]
                    await repo.save_candles(candles_dicts)
            except Exception as e:
                logger.debug("Database persistence skipped", error=str(e))

        return candles
