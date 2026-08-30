"""
DEX-провайдер: DexScreener (пулы/цены/ликвидность) + GeckoTerminal (OHLCV)
+ GoPlus (безопасность контракта: mint, blacklist, honeypot, LP-локи, холдеры).

Почему именно этот набор:
  * DexScreener — самый широкий охват пулов и уже агрегированные окна объёма
    (m5/h1/h6/h24) и числа сделок — ровно то, что нужно уровню 1 сканера;
  * GeckoTerminal — публичные OHLCV по пулу (у DexScreener свечей в API нет);
  * GoPlus — бесплатный token_security: флаги mint()/blacklist(), налоги,
    honeypot, состав холдеров и блокировки LP. Это закрывает уровень 2 без
    собственного узла и без эмуляции EVM.

Все запросы идут через AsyncHttpClient (ретраи + лимиты + предохранитель).
Любой сбой → None, уровень сканера помечает проверку как degraded.
"""

from __future__ import annotations

import time
from typing import Any

from v2.config import V2Config
from v2.core.cache import TtlCache
from v2.core.errors import ProviderUnavailable
from v2.core.logging import get_logger
from v2.core.monitor import monitor
from v2.data.provider import MarketProvider
from v2.models import (
    Candle,
    ContractRisk,
    HolderStats,
    LpLockInfo,
    OrderBookLevel,
    OrderBookSnapshot,
    TokenCandidate,
    now_ms,
)

logger = get_logger("data.dex")

DEXSCREENER = "https://api.dexscreener.com"
GECKO = "https://api.geckoterminal.com/api/v2"
GOPLUS = "https://api.gopluslabs.io/api/v1"

# chain (DexScreener chainId) → GoPlus chain_id
GOPLUS_CHAIN: dict[str, str] = {
    "ethereum": "1",
    "bsc": "56",
    "base": "8453",
    "arbitrum": "42161",
    "polygon": "137",
    "avalanche": "43114",
    "optimism": "10",
    "fantom": "250",
}
GOPLUS_TOKEN = "token_security"


def _float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if out == out else default  # отбрасываем NaN
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


class DexProvider(MarketProvider):
    name = "dex"

    def __init__(self, config: V2Config, http) -> None:
        self.config = config
        self.http = http
        self.cache = TtlCache(ttl_sec=config.SCAN_CACHE_TTL_SECONDS, name="dex")

    # ═══════════════════════════════════════════════════════════
    #  DexScreener: пулы
    # ═══════════════════════════════════════════════════════════
    def _pair_to_token(self, pair: dict[str, Any]) -> TokenCandidate | None:
        base = pair.get("baseToken") or {}
        quote = pair.get("quoteToken") or {}
        address = base.get("address")
        if not address:
            return None
        txns = pair.get("txns") or {}
        volume = pair.get("volume") or {}
        change = pair.get("priceChange") or {}
        liquidity = pair.get("liquidity") or {}

        m5 = txns.get("m5") or {}
        h1 = txns.get("h1") or {}
        h24 = txns.get("h24") or {}
        return TokenCandidate(
            chain=str(pair.get("chainId") or ""),
            address=str(address),
            symbol=str(base.get("symbol") or "?"),
            name=str(base.get("name") or ""),
            pair_address=str(pair.get("pairAddress") or ""),
            dex=str(pair.get("dexId") or ""),
            quote_symbol=str(quote.get("symbol") or ""),
            price_usd=_float(pair.get("priceUsd")),
            volume_5m_usd=_float(volume.get("m5")),
            volume_1h_usd=_float(volume.get("h1")),
            volume_24h_usd=_float(volume.get("h24")),
            tx_5m=_int(m5.get("buys")) + _int(m5.get("sells")),
            buys_5m=_int(m5.get("buys")),
            sells_5m=_int(m5.get("sells")),
            tx_1h=_int(h1.get("buys")) + _int(h1.get("sells")),
            tx_24h=_int(h24.get("buys")) + _int(h24.get("sells")),
            liquidity_usd=_float(liquidity.get("usd")),
            fdv_usd=_float(pair.get("fdv")),
            market_cap_usd=_float(pair.get("marketCap")),
            price_change_5m_pct=_float(change.get("m5")),
            price_change_1h_pct=_float(change.get("h1")),
            price_change_24h_pct=_float(change.get("h24")),
            pair_created_ms=_int(pair.get("pairCreatedAt")),
            source="dexscreener",
            extra={"url": pair.get("url") or "", "pair": pair.get("pairAddress")},
        )

    async def discover_candidates(self, limit: int = 100) -> list[TokenCandidate]:
        """
        Кандидаты = свежие профили токенов + топ-бусты DexScreener.

        Именно там появляются новые пулы до того, как они попадут в тренды,
        поэтому сканер уровня 1 видит их в первые часы жизни.
        """
        out: dict[str, TokenCandidate] = {}
        endpoints = (
            f"{DEXSCREENER}/token-profiles/latest/v1",
            f"{DEXSCREENER}/token-boosts/top/v1",
        )
        for url in endpoints:
            try:
                payload = await self.http.get_json(url, component="data.dex")
            except ProviderUnavailable as exc:
                monitor.record("data.dex.discover", exc)
                continue
            if not isinstance(payload, list):
                continue
            for profile in payload[:60]:
                address = str(profile.get("tokenAddress") or "")
                chain = str(profile.get("chainId") or "")
                if not address:
                    continue
                key = f"{chain}:{address.lower()}"
                if key in out:
                    continue
                try:
                    pairs = await self._pairs_for_token(chain, address)
                except ProviderUnavailable:
                    continue
                if pairs:
                    best = max(pairs, key=lambda t: t.liquidity_usd)
                    out[key] = best
                if len(out) >= limit:
                    break
            if len(out) >= limit:
                break
        return list(out.values())[:limit]

    async def _pairs_for_token(self, chain: str, address: str) -> list[TokenCandidate]:
        cache_key = f"pairs:{chain}:{address.lower()}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached
        payload = await self.http.get_json(
            f"{DEXSCREENER}/latest/dex/tokens/{address}", component="data.dex"
        )
        pairs = [p for p in (payload or {}).get("pairs") or [] if p.get("chainId") == chain or not chain]
        tokens = [t for t in (self._pair_to_token(p) for p in pairs) if t is not None]
        self.cache.set(cache_key, tokens)
        return tokens

    async def resolve_token(self, query: str) -> list[TokenCandidate]:
        """Поиск по адресу или символу через /latest/dex/search."""
        if query.startswith("0x") or len(query) > 25:
            payload = await self.http.get_json(
                f"{DEXSCREENER}/latest/dex/tokens/{query}", component="data.dex"
            )
            pairs = (payload or {}).get("pairs") or []
        else:
            payload = await self.http.get_json(
                f"{DEXSCREENER}/latest/dex/search", params={"q": query}, component="data.dex"
            )
            pairs = (payload or {}).get("pairs") or []
        tokens = [t for t in (self._pair_to_token(p) for p in pairs) if t is not None]
        # сортируем по ликвидности: пользователь, скорее всего, хочет основной пул
        tokens.sort(key=lambda t: t.liquidity_usd, reverse=True)
        return tokens

    # ═══════════════════════════════════════════════════════════
    #  GeckoTerminal: свечи по пулу
    # ═══════════════════════════════════════════════════════════
    async def klines(self, token: TokenCandidate, timeframe: str, limit: int = 300) -> list[Candle]:
        if not self.config.GECKOTERMINAL_ENABLED or not token.pair_address:
            return []
        timeframe = timeframe.lower()
        if timeframe not in ("m5", "m15", "h1", "h4", "day"):
            timeframe = {"1m": "m5", "5m": "m5", "15m": "m15", "1h": "h1", "4h": "h4", "1d": "day"}.get(
                timeframe, "h1"
            )
        url = (
            f"{GECKO}/networks/{token.chain}/pools/{token.pair_address}/ohlcv/{timeframe}"
        )
        payload = await self.http.get_json(
            url,
            params={"aggregate": 1, "limit": min(limit, 1000), "currency": "usd"},
            component="data.dex.klines",
        )
        rows = (((payload or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        candles = [
            Candle(
                ts_ms=_int(row[0]) * 1000,
                open=_float(row[1]),
                high=_float(row[2]),
                low=_float(row[3]),
                close=_float(row[4]),
                volume=_float(row[5]),
            )
            for row in rows
            if len(row) >= 6
        ]
        candles.sort(key=lambda c: c.ts_ms)
        return candles

    # ═══════════════════════════════════════════════════════════
    #  GoPlus: безопасность (уровень 2)
    # ═══════════════════════════════════════════════════════════
    async def _goplus(self, token: TokenCandidate) -> dict[str, Any]:
        chain_id = GOPLUS_CHAIN.get(token.chain.lower())
        if not chain_id or not self.config.GOPLUS_ENABLED:
            return {}
        key = f"goplus:{chain_id}:{token.address.lower()}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        url = f"{GOPLUS}/{GOPLUS_TOKEN}/{chain_id}"
        payload = await self.http.get_json(
            url, params={"contract_addresses": token.address}, component="data.dex.goplus"
        )
        result = ((payload or {}).get("result") or {}).get(token.address.lower()) or {}
        self.cache.set(key, result, ttl=max(120.0, self.config.SCAN_CACHE_TTL_SECONDS))
        return result

    async def contract_risk(self, token: TokenCandidate) -> ContractRisk | None:
        data = await self._goplus(token)
        if not data:
            return None

        def flag(key: str) -> bool | None:
            raw = data.get(key)
            if raw is None:
                return None
            return str(raw) == "1"

        functions: list[str] = []
        if flag("is_mintable"):
            functions.append("mint")
        if flag("is_blacklisted"):
            functions.append("blacklist")
        if flag("owner_change_balance"):
            functions.append("setBalance")
        if flag("can_take_back_ownership"):
            functions.append("takeBackOwnership")
        if flag("hidden_owner"):
            functions.append("hiddenOwner")
        if flag("selfdestruct"):
            functions.append("selfdestruct")

        return ContractRisk(
            is_mintable=flag("is_mintable"),
            has_blacklist=flag("is_blacklisted"),
            has_owner=bool(data.get("owner_address")),
            owner_can_change_balance=flag("owner_change_balance"),
            is_proxy=flag("is_proxy"),
            is_honeypot=flag("is_honeypot"),
            buy_tax_pct=_float(data.get("buy_tax"), 0.0) * 100.0 if data.get("buy_tax") else None,
            sell_tax_pct=_float(data.get("sell_tax"), 0.0) * 100.0 if data.get("sell_tax") else None,
            source_verified=flag("is_open_source"),
            is_open_source=flag("is_open_source"),
            cannot_sell_all=flag("cannot_sell_all"),
            cannot_buy=flag("cannot_buy"),
            owner_address=str(data.get("owner_address") or ""),
            functions_found=functions,
            source="goplus",
            is_stub=False,
        )

    async def holders(self, token: TokenCandidate) -> HolderStats | None:
        data = await self._goplus(token)
        if not data:
            return None
        holders = data.get("holders") or []
        lp_holders = data.get("lp_holders") or []
        percents = sorted((_float(h.get("percent")) * 100.0 for h in holders), reverse=True)
        top10 = sum(percents[:10])
        top1 = percents[0] if percents else None

        # LP-контракт почти всегда в топ-10 — исключаем его из «концентрации у команды»
        lp_addresses = {str(h.get("address", "")).lower() for h in lp_holders}
        non_lp = [
            _float(h.get("percent")) * 100.0
            for h in holders
            if str(h.get("address", "")).lower() not in lp_addresses
        ]
        non_lp.sort(reverse=True)
        return HolderStats(
            top1_pct=round(top1, 2) if top1 is not None else None,
            top10_pct=round(sum(non_lp[:10]) if non_lp else top10, 2),
            holders_count=_int(data.get("holder_count")) or None,
            deployer_pct=None,
            lp_in_top10=bool(lp_addresses),
            source="goplus",
            is_stub=False,
        )

    async def lp_lock(self, token: TokenCandidate) -> LpLockInfo | None:
        data = await self._goplus(token)
        if not data:
            return None
        lp_holders = data.get("lp_holders") or []
        if not lp_holders:
            return LpLockInfo(locked_pct=None, source="goplus", is_stub=False)

        locked_pct = 0.0
        latest_until: int | None = None
        locker = ""
        for holder in lp_holders:
            if str(holder.get("is_locked")) != "1":
                continue
            locked_pct += _float(holder.get("percent")) * 100.0
            locker = locker or str(holder.get("tag") or "")
            for detail in holder.get("locked_detail") or []:
                end_ms = _int(detail.get("end_time")) * 1000
                if end_ms and (latest_until is None or end_ms > latest_until):
                    latest_until = end_ms
        days_left = (
            (latest_until - now_ms()) / 86_400_000 if latest_until and latest_until < 4_000_000_000_000 else None
        )
        return LpLockInfo(
            locked_pct=round(locked_pct, 2),
            locked_until_ms=latest_until,
            lock_days_left=round(days_left, 1) if days_left is not None else None,
            locker=locker,
            source="goplus",
            is_stub=False,
        )

    async def orderbook(self, token: TokenCandidate, depth: int = 50) -> OrderBookSnapshot | None:
        """
        У DEX-пула настоящего стакана нет, поэтому книга эмулируется из
        ликвидности: уровень ±k·tick получает долю LP по закону убывания.

        Эмуляция честная по общему объёму (суммарная глубина ≈ доля LP) и
        позволяет оценить проскальзывание входа на $5k — ровно то, что нужно
        отчёту. В отчёте такой стакан помечается is_stub=True.
        """
        mid = token.price_usd
        if mid <= 0 or token.liquidity_usd <= 0:
            return None
        usable = token.liquidity_usd * 0.06
        levels_each = max(5, depth // 2)
        step = mid * 0.0005
        bids: list[OrderBookLevel] = []
        asks: list[OrderBookLevel] = []
        total_weight = sum(1.0 / (i + 1) for i in range(levels_each))
        for i in range(levels_each):
            weight = (1.0 / (i + 1)) / total_weight
            usd = usable * weight
            qty = usd / mid
            bids.append(OrderBookLevel(price=round(mid - step * (i + 1), 10), qty=round(qty, 8)))
            asks.append(OrderBookLevel(price=round(mid + step * (i + 1), 10), qty=round(qty, 8)))
        return OrderBookSnapshot(
            symbol=token.symbol,
            bids=bids,
            asks=asks,
            ts_ms=now_ms(),
            source="emulated-from-liquidity",
            is_stub=True,
        )

    def stats(self) -> dict[str, Any]:
        return {"provider": self.name, "cache": self.cache.stats(), "http": self.http.stats()}
