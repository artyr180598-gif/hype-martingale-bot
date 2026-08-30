"""
Провайдеры данных v2: контракт, живые источники, демо-режим и композитор.

MarketProvider — единый интерфейс. Сканер и движок анализа не знают, откуда
пришли данные: DexScreener, Etherscan или синтетика. Это позволяет:
  * работать офлайн (DATA_MODE=demo) — тесты, CI, знакомство с ботом;
  * деградировать по частям: упал GoPlus → проверка контракта помечается
    как degraded, но скан продолжается, а не падает;
  * подменять любой слой в тестах.
"""

from __future__ import annotations

import abc
from typing import Any

from v2.core.logging import get_logger
from v2.core.monitor import monitor
from v2.models import (
    Candle,
    ContractRisk,
    DeployerInfo,
    HolderStats,
    LpLockInfo,
    OrderBookSnapshot,
    SocialReport,
    TokenCandidate,
)

logger = get_logger("data.provider")


class MarketProvider(abc.ABC):
    """Контракт источника данных. Каждый метод — «лучший результат или None»."""

    name: str = "base"

    # ── рынок ────────────────────────────────────────────────────
    async def discover_candidates(self, limit: int = 100) -> list[TokenCandidate]:
        return []

    async def resolve_token(self, query: str) -> list[TokenCandidate]:
        """Поиск по адресу (0x.../mint) или символу. Возвращает пулы-кандидаты."""
        return []

    async def klines(self, token: TokenCandidate, timeframe: str, limit: int = 300) -> list[Candle]:
        return []

    async def orderbook(self, token: TokenCandidate, depth: int = 50) -> OrderBookSnapshot | None:
        return None

    # ── безопасность ─────────────────────────────────────────────
    async def holders(self, token: TokenCandidate) -> HolderStats | None:
        return None

    async def lp_lock(self, token: TokenCandidate) -> LpLockInfo | None:
        return None

    async def contract_risk(self, token: TokenCandidate) -> ContractRisk | None:
        return None

    async def deployer(self, token: TokenCandidate) -> DeployerInfo | None:
        return None

    # ── соцфон ───────────────────────────────────────────────────
    async def social(self, token: TokenCandidate, window_hours: int = 2) -> SocialReport | None:
        return None

    async def close(self) -> None:
        return None

    def stats(self) -> dict[str, Any]:
        return {"provider": self.name}


class CompositeProvider(MarketProvider):
    """
    Композитор: ходит по списку провайдеров и берёт первый успешный ответ.

    Порядок в ``chain`` = приоритет. Последним обычно стоит DemoProvider: в
    режиме auto он подменяет только те данные, которых не удалось получить, и
    помечает их ``is_stub``/degraded, чтобы отчёт не врал пользователю.
    """

    name = "composite"

    def __init__(self, chain: list[MarketProvider], *, allow_demo_fallback: bool = True) -> None:
        self.chain = [p for p in chain if p is not None]
        self.allow_demo_fallback = allow_demo_fallback
        self.demo = next((p for p in self.chain if p.name == "demo"), None)
        self.calls = 0
        self.fallbacks = 0

    async def _first(self, method: str, *args, **kwargs) -> Any:
        self.calls += 1
        for provider in self.chain:
            fn = getattr(provider, method)
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 — деградируем, не падаем
                monitor.record(f"data.{provider.name}.{method}", exc)
                continue
            if result:
                return result
            if provider.name == "demo":
                break  # дальше демо идти некуда
        return None

    async def discover_candidates(self, limit: int = 100) -> list[TokenCandidate]:
        out: list[TokenCandidate] = await self._first("discover_candidates", limit) or []
        if not out and self.demo and self.allow_demo_fallback:
            out = await self.demo.discover_candidates(limit)
            self.fallbacks += 1
        return out

    async def resolve_token(self, query: str) -> list[TokenCandidate]:
        for provider in self.chain:
            try:
                found = await provider.resolve_token(query)
            except Exception as exc:  # noqa: BLE001
                monitor.record(f"data.{provider.name}.resolve", exc)
                continue
            if found:
                return found
        return []

    async def klines(self, token: TokenCandidate, timeframe: str, limit: int = 300) -> list[Candle]:
        candles = await self._first("klines", token, timeframe, limit) or []
        if not candles and self.demo and self.allow_demo_fallback:
            candles = await self.demo.klines(token, timeframe, limit)
            self.fallbacks += 1
        return candles

    async def orderbook(self, token: TokenCandidate, depth: int = 50) -> OrderBookSnapshot | None:
        book = await self._first("orderbook", token, depth)
        if book is None and self.demo and self.allow_demo_fallback:
            book = await self.demo.orderbook(token, depth)
            self.fallbacks += 1
        return book

    async def holders(self, token: TokenCandidate) -> HolderStats | None:
        return await self._first("holders", token)

    async def lp_lock(self, token: TokenCandidate) -> LpLockInfo | None:
        return await self._first("lp_lock", token)

    async def contract_risk(self, token: TokenCandidate) -> ContractRisk | None:
        return await self._first("contract_risk", token)

    async def deployer(self, token: TokenCandidate) -> DeployerInfo | None:
        return await self._first("deployer", token)

    async def social(self, token: TokenCandidate, window_hours: int = 2) -> SocialReport | None:
        return await self._first("social", token, window_hours)

    async def close(self) -> None:
        for provider in self.chain:
            try:
                await provider.close()
            except Exception as exc:  # noqa: BLE001
                monitor.record(f"data.{provider.name}.close", exc)

    def stats(self) -> dict[str, Any]:
        return {
            "providers": [p.name for p in self.chain],
            "calls": self.calls,
            "demo_fallbacks": self.fallbacks,
        }


def build_provider(config, *, http=None) -> MarketProvider:
    """
    Фабрика провайдера по конфигу.

    DATA_MODE=demo  → только синтетика (полностью офлайн);
    DATA_MODE=live  → только живые источники (без подстраховки);
    DATA_MODE=auto  → живые + демо-подстраховка (поведение по умолчанию).
    """
    from v2.data.cex import CexProvider
    from v2.data.chain import ExplorerProvider
    from v2.data.demo import DemoProvider
    from v2.data.dex import DexProvider
    from v2.data.http_client import AsyncHttpClient
    from v2.data.social import SocialProvider

    if config.DATA_MODE == "demo":
        logger.info("DATA_MODE=demo: работаем на синтетическом рынке")
        return DemoProvider(config)

    http = http or AsyncHttpClient(config, name="http")
    chain: list[MarketProvider] = []
    if config.DEXSCREENER_ENABLED or config.GECKOTERMINAL_ENABLED:
        chain.append(DexProvider(config, http))
    if config.CEX_ENABLED:
        chain.append(CexProvider(config, http))
    if config.ETHERSCAN_API_KEY or config.BSCSCAN_API_KEY or config.MORALIS_API_KEY:
        chain.append(ExplorerProvider(config, http))
    chain.append(SocialProvider(config, http))

    if config.DATA_MODE == "auto":
        chain.append(DemoProvider(config))
        logger.info("DATA_MODE=auto: живые провайдеры %s + демо-подстраховка",
                    [p.name for p in chain[:-1]])
    else:
        logger.info("DATA_MODE=live: провайдеры %s (без подстраховки)", [p.name for p in chain])
    return CompositeProvider(chain, allow_demo_fallback=config.DATA_MODE == "auto")
