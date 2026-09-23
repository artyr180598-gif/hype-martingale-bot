"""Universe builder — multi-exchange ticker aggregation (ported from CryptoScanBot + freqtrade)."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone

from loguru import logger

from ..config import Settings
from ..data.exchanges import ExchangeManager
from ..data.models import Ticker


async def build_universe(
    ex_manager: ExchangeManager,
    cfg: Settings,
    max_symbols: int = 300,
) -> list[Ticker]:
    """
    Build liquid universe from primary exchange + fallbacks.
    Aggregates tickers, deduplicates by symbol, sorts by turnover.
    """
    all_tickers: dict[str, Ticker] = {}

    # Fetch from primary first
    exchanges_to_scan = [cfg.PRIMARY_EXCHANGE] + cfg.secondary_exchanges_list
    # Add extra exchanges if enabled
    for exch in cfg.exchanges_list:
        if exch not in exchanges_to_scan:
            exchanges_to_scan.append(exch)

    tasks = [ex_manager.fetch_universe_tickers(exch) for exch in exchanges_to_scan[:4]]  # limit to 4 to avoid rate limit
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for exch, res in zip(exchanges_to_scan[:4], results):
        if isinstance(res, Exception):
            logger.warning(f"Universe fetch {exch} failed: {res}")
            continue
        for ticker in res:
            sym = ticker.symbol.upper().replace("/", "").replace("-", "")
            # Normalize: BTCUSDT
            if not sym.endswith("USDT"):
                continue
            # Keep highest turnover
            if sym not in all_tickers or ticker.turnover_24h > all_tickers[sym].turnover_24h:
                all_tickers[sym] = ticker

    # Filter by min turnover/volume
    filtered = [
        t for t in all_tickers.values()
        if t.turnover_24h >= cfg.SCAN_MIN_TURNOVER_USD and t.last > 0
    ]

    # Sort by turnover descending
    filtered.sort(key=lambda x: x.turnover_24h, reverse=True)

    # Deduplicate clusters (e.g., BTCUSDT, BTCUSDT.P etc already normalized)
    # Diversity: limit per cluster (e.g., meme cluster)
    # Simple: keep top N
    universe = filtered[:max_symbols]

    logger.info(f"Universe built: {len(universe)} symbols from {len(all_tickers)} raw (min turnover ${cfg.SCAN_MIN_TURNOVER_USD:,.0f})")
    return universe


def filter_watchlist_tickers(tickers: list[Ticker], watchlist: list[str]) -> list[Ticker]:
    wl_set = {s.upper().replace("/", "") for s in watchlist}
    return [t for t in tickers if t.symbol.upper() in wl_set]
