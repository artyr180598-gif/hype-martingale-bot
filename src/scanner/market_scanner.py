"""
Continuous Multi-Asset Market Scanner and Opportunity Ranker.
"""
import asyncio

from src.config.settings import settings
from src.core.logging import get_logger
from src.data.adapters.binance import BinanceFuturesAdapter
from src.data.downloader import HistoricalDataDownloader
from src.features.pipeline import FeaturePipeline
from src.signals.generator import SignalGenerator
from src.signals.models import SignalSetup

logger = get_logger("scanner.market")


class MarketScanner:
    """
    Scans the entire futures market, identifies emerging opportunities, and ranks them by score.
    """

    def __init__(self, symbols: list[str] | None = None):
        self.symbols = symbols or list(settings.TRACKED_SYMBOLS)
        self.downloader = HistoricalDataDownloader(BinanceFuturesAdapter())
        self.pipeline = FeaturePipeline()

    async def scan_symbol(self, symbol: str) -> SignalSetup | None:
        try:
            # Fetch candles for entry (15m) and macro (4h)
            entry_candles = await self.downloader.get_or_download_candles(symbol, "15m", lookback_bars=200)
            macro_candles = await self.downloader.get_or_download_candles(symbol, "4h", lookback_bars=100)
            medium_candles = await self.downloader.get_or_download_candles(symbol, "1h", lookback_bars=100)

            if len(entry_candles) < 35:
                return None

            entry_feat = self.pipeline.compute_feature_matrix(entry_candles)
            macro_feat = self.pipeline.compute_feature_matrix(macro_candles) if macro_candles else None
            medium_feat = self.pipeline.compute_feature_matrix(medium_candles) if medium_candles else None

            setup = SignalGenerator.generate_setup(
                entry_features=entry_feat,
                macro_features=macro_feat,
                medium_features=medium_feat,
            )
            return setup
        except Exception as e:
            logger.debug("Symbol scan failed", symbol=symbol, error=str(e))
            return None

    async def scan_market(self) -> list[SignalSetup]:
        tasks = [self.scan_symbol(sym) for sym in self.symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid_setups: list[SignalSetup] = []
        for res in results:
            if isinstance(res, SignalSetup):
                valid_setups.append(res)

        # Sort by opportunity score descending
        valid_setups.sort(key=lambda s: s.score, reverse=True)
        return valid_setups
