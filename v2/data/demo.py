"""
Демо-провайдер: полностью синтетический рынок для работы офлайн.

Зачем он нужен (а не «заглушка на отвяжись»):
  * сканер, риск-менеджер и отчёт можно гонять без сети и без ключей — в CI и
    при первом знакомстве с ботом;
  * все данные детерминированы (сид = хеш адреса), поэтому тесты воспроизводимы;
  * во вселенной намеренно есть токены каждого типа: чистые, тонкие по
    ликвидности, с mint(), с разблокированной LP, с концентрацией у топ-10,
    с только что созданным деплоером. Это позволяет проверить, что каждый
    фильтр трёхуровневого сканера действительно срабатывает.

Данные помечаются ``is_stub=True``/``source="demo"`` и попадают в отчёт — бот
никогда не выдаёт синтетику за живой рынок.
"""

from __future__ import annotations

import hashlib
import math
import random
import time
from typing import Any

from v2.config import V2Config
from v2.core.logging import get_logger
from v2.models import (
    Candle,
    ContractRisk,
    DeployerInfo,
    HolderStats,
    LpLockInfo,
    OrderBookLevel,
    OrderBookSnapshot,
    SocialReport,
    TokenCandidate,
    now_ms,
)

logger = get_logger("data.demo")

# ═══════════════════════════════════════════════════════════════
#  ВСЕЛЕННАЯ ДЕМО-РЫНКА
#  profile: solid | mid | thin | fresh | scam_mint | scam_lp | scam_holders |
#           scam_dev | honeypot
# ═══════════════════════════════════════════════════════════════
UNIVERSE: list[dict[str, Any]] = [
    {
        "symbol": "AURORA", "name": "Aurora Protocol", "chain": "ethereum", "profile": "solid",
        "address": "0xA11CE00000000000000000000000000000000001", "price": 1.84,
        "liquidity": 4_200_000, "mcap": 62_000_000, "volume_24h": 215_000_000, "age_days": 240,
    },
    {
        "symbol": "KELP", "name": "Kelp DAO Restaked", "chain": "ethereum", "profile": "solid",
        "address": "0xB0B0000000000000000000000000000000000002", "price": 0.412,
        "liquidity": 2_600_000, "mcap": 41_000_000, "volume_24h": 168_000_000, "age_days": 180,
    },
    {
        "symbol": "NEURON", "name": "Neuron Inference", "chain": "base", "profile": "mid",
        "address": "0xC0FFEE0000000000000000000000000000000003", "price": 0.0731,
        "liquidity": 780_000, "mcap": 9_400_000, "volume_24h": 192_000_000, "age_days": 62,
    },
    {
        "symbol": "PIPES", "name": "Pipeline Fi", "chain": "bsc", "profile": "mid",
        "address": "0xD1CE000000000000000000000000000000000004", "price": 2.31,
        "liquidity": 1_150_000, "mcap": 21_000_000, "volume_24h": 176_000_000, "age_days": 130,
        "cex_symbol": "PIPEUSDT",
    },
    {
        "symbol": "GROVE", "name": "Grove Yield", "chain": "ethereum", "profile": "thin",
        "address": "0xE2EE000000000000000000000000000000000005", "price": 0.0092,
        "liquidity": 96_000, "mcap": 1_900_000, "volume_24h": 29_000_000, "age_days": 40,
    },
    {
        "symbol": "EMBER", "name": "Ember Launch", "chain": "base", "profile": "fresh",
        "address": "0xF00D000000000000000000000000000000000006", "price": 0.021,
        "liquidity": 340_000, "mcap": 3_600_000, "volume_24h": 96_000_000, "age_days": 2,
    },
    {
        "symbol": "MOONX", "name": "MoonX To The Moon", "chain": "bsc", "profile": "scam_mint",
        "address": "0x5C41000000000000000000000000000000000007", "price": 0.00042,
        "liquidity": 210_000, "mcap": 2_400_000, "volume_24h": 264_000_000, "age_days": 9,
    },
    {
        "symbol": "SAFER", "name": "SafeRocket", "chain": "bsc", "profile": "scam_lp",
        "address": "0x5AFE000000000000000000000000000000000008", "price": 0.0118,
        "liquidity": 150_000, "mcap": 1_200_000, "volume_24h": 151_000_000, "age_days": 21,
    },
    {
        "symbol": "WHALE", "name": "Whale Club", "chain": "ethereum", "profile": "scam_holders",
        "address": "0x9A1E000000000000000000000000000000000009", "price": 0.335,
        "liquidity": 620_000, "mcap": 7_300_000, "volume_24h": 173_000_000, "age_days": 95,
    },
    {
        "symbol": "REKT", "name": "Rekt Finance", "chain": "bsc", "profile": "scam_dev",
        "address": "0x0BAD00000000000000000000000000000000000A", "price": 0.0017,
        "liquidity": 260_000, "mcap": 2_100_000, "volume_24h": 228_000_000, "age_days": 34,
    },
    {
        "symbol": "HONNY", "name": "Honey Pot Gold", "chain": "bsc", "profile": "honeypot",
        "address": "0x0H0N00000000000000000000000000000000000B", "price": 0.0089,
        "liquidity": 410_000, "mcap": 4_900_000, "volume_24h": 244_000_000, "age_days": 16,
    },
    {
        "symbol": "SLEEP", "name": "Sleepy Capybara", "chain": "solana", "profile": "thin",
        "address": "SLPmint111111111111111111111111111111111111", "price": 0.000031,
        "liquidity": 42_000, "mcap": 410_000, "volume_24h": 11_500_000, "age_days": 70,
    },
    {
        "symbol": "TITAN", "name": "Titan Layer2", "chain": "ethereum", "profile": "solid",
        "address": "0x717A00000000000000000000000000000000000C", "price": 3.72,
        "liquidity": 5_900_000, "mcap": 88_000_000, "volume_24h": 335_000_000, "age_days": 410,
        "cex_symbol": "TITANUSDT",
    },
    {
        "symbol": "ZEPH", "name": "Zephyr Cloud", "chain": "base", "profile": "mid",
        "address": "0x2E9H00000000000000000000000000000000000D", "price": 0.612,
        "liquidity": 980_000, "mcap": 15_500_000, "volume_24h": 240_000_000, "age_days": 110,
    },
]

# Профиль → параметры безопасности (холдеры, LP, контракт, деплоер)
_PROFILES: dict[str, dict[str, Any]] = {
    "solid": dict(
        top10=22.0, top1=6.5, holders=14_500, lp_locked=100.0, lp_days=1095,
        mint=False, blacklist=False, honeypot=False, buy_tax=0.0, sell_tax=0.0,
        verified=True, dev_age=900, dev_tokens=2, dev_tx=3400, sold=False,
        hype=58, sentiment=0.35, mentions=180,
    ),
    "mid": dict(
        top10=31.0, top1=11.0, holders=4200, lp_locked=92.0, lp_days=365,
        mint=False, blacklist=False, honeypot=False, buy_tax=1.0, sell_tax=1.5,
        verified=True, dev_age=420, dev_tokens=4, dev_tx=900, sold=False,
        hype=44, sentiment=0.18, mentions=64,
    ),
    "thin": dict(
        top10=38.0, top1=17.0, holders=420, lp_locked=85.0, lp_days=180,
        mint=False, blacklist=False, honeypot=False, buy_tax=2.0, sell_tax=2.0,
        verified=True, dev_age=210, dev_tokens=6, dev_tx=210, sold=False,
        hype=22, sentiment=0.05, mentions=11,
    ),
    "fresh": dict(
        # намеренно проходит L2 (холдеры/LP/контракт в норме), но падает на L3:
        # кошельку деплоера 2 дня при пороге 7 — так проверяется ончейн-уровень
        top10=35.0, top1=14.0, holders=310, lp_locked=100.0, lp_days=730,
        mint=False, blacklist=False, honeypot=False, buy_tax=0.5, sell_tax=0.5,
        verified=True, dev_age=2, dev_tokens=1, dev_tx=24, sold=False,
        hype=81, sentiment=0.5, mentions=420,
    ),
    "scam_mint": dict(
        top10=44.0, top1=21.0, holders=880, lp_locked=90.0, lp_days=365,
        mint=True, blacklist=False, honeypot=False, buy_tax=5.0, sell_tax=7.0,
        verified=False, dev_age=45, dev_tokens=9, dev_tx=150, sold=False,
        hype=72, sentiment=0.4, mentions=310,
    ),
    "scam_lp": dict(
        top10=36.0, top1=18.0, holders=610, lp_locked=0.0, lp_days=0,
        mint=False, blacklist=False, honeypot=False, buy_tax=3.0, sell_tax=4.0,
        verified=False, dev_age=120, dev_tokens=7, dev_tx=300, sold=False,
        hype=51, sentiment=0.2, mentions=95,
    ),
    "scam_holders": dict(
        top10=63.0, top1=34.0, holders=240, lp_locked=100.0, lp_days=540,
        mint=False, blacklist=False, honeypot=False, buy_tax=2.0, sell_tax=2.0,
        verified=True, dev_age=300, dev_tokens=3, dev_tx=520, sold=False,
        hype=37, sentiment=0.1, mentions=48,
    ),
    "scam_dev": dict(
        top10=29.0, top1=9.0, holders=1800, lp_locked=95.0, lp_days=365,
        mint=False, blacklist=True, honeypot=False, buy_tax=6.0, sell_tax=9.0,
        verified=False, dev_age=3, dev_tokens=34, dev_tx=9, sold=True,
        hype=66, sentiment=0.3, mentions=240,
    ),
    "honeypot": dict(
        top10=41.0, top1=20.0, holders=1300, lp_locked=88.0, lp_days=365,
        mint=False, blacklist=True, honeypot=True, buy_tax=0.0, sell_tax=99.0,
        verified=False, dev_age=20, dev_tokens=12, dev_tx=60, sold=False,
        hype=69, sentiment=0.45, mentions=290,
    ),
}


def _rng(*parts: Any) -> random.Random:
    seed = hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]
    return random.Random(int(seed, 16))


class DemoProvider:
    """Синтетический провайдер. Реализует весь контракт MarketProvider."""

    name = "demo"
    is_demo = True

    def __init__(self, config: V2Config) -> None:
        self.config = config
        self._extra: list[dict[str, Any]] = []   # токены, созданные по неизвестному адресу
        self._tokens = self._build_tokens()

    # ── вселенная ────────────────────────────────────────────────
    def _build_tokens(self) -> list[TokenCandidate]:
        now = now_ms()
        out: list[TokenCandidate] = []
        for item in [*UNIVERSE, *self._extra]:
            profile = _PROFILES[item["profile"]]
            rng = _rng(item["address"], "token")
            volume_24h = item["volume_24h"]
            # 5 минут ≈ 1/288 суток, но у «хайповых» профилей активность выше средней
            burst = {"fresh": 6.0, "scam_mint": 4.5, "honeypot": 4.0, "scam_dev": 3.5}.get(item["profile"], 1.0)
            volume_5m = volume_24h / 288.0 * burst * rng.uniform(0.6, 1.5)
            tx_5m = int(volume_5m / max(item["price"], 1e-9) / rng.uniform(400, 2500))
            tx_5m = max(5, min(tx_5m, 4000))
            buys = int(tx_5m * rng.uniform(0.35, 0.68))
            token = TokenCandidate(
                chain=item["chain"],
                address=item["address"],
                symbol=item["symbol"],
                name=item["name"],
                pair_address="0x" + hashlib.sha256(item["address"].encode()).hexdigest()[:38],
                dex={"ethereum": "uniswap", "bsc": "pancakeswap", "base": "aerodrome", "solana": "raydium"}[
                    item["chain"]
                ],
                quote_symbol="USDC" if item["chain"] != "solana" else "WSOL",
                price_usd=item["price"],
                volume_5m_usd=round(volume_5m, 2),
                volume_1h_usd=round(volume_5m * rng.uniform(8, 14), 2),
                volume_24h_usd=volume_24h,
                tx_5m=tx_5m,
                buys_5m=buys,
                sells_5m=tx_5m - buys,
                tx_1h=int(tx_5m * rng.uniform(9, 13)),
                tx_24h=int(tx_5m * rng.uniform(200, 300)),
                liquidity_usd=item["liquidity"],
                market_cap_usd=item["mcap"],
                fdv_usd=item["mcap"] * rng.uniform(1.0, 1.4),
                price_change_5m_pct=round(rng.uniform(-2.5, 3.5), 2),
                price_change_1h_pct=round(rng.uniform(-8, 12), 2),
                price_change_24h_pct=round(rng.uniform(-15, 45), 2),
                pair_created_ms=int(now - item["age_days"] * 86_400_000),
                cex_symbol=item.get("cex_symbol", ""),
                source="demo",
                extra={"profile": item["profile"]},
            )
            out.append(token)
        return out

    # ── MarketProvider ───────────────────────────────────────────
    async def discover_candidates(self, limit: int = 100) -> list[TokenCandidate]:
        return list(self._tokens[:limit])

    async def resolve_token(self, query: str) -> list[TokenCandidate]:
        q = query.strip().lower().replace("usdt", "").replace("usdc", "")
        found = [
            t
            for t in self._tokens
            if t.address.lower() == query.strip().lower()
            or t.symbol.lower() == q
            or t.symbol.lower() == query.strip().lower()
            or (t.cex_symbol and t.cex_symbol.lower() == query.strip().lower())
        ]
        if found:
            return found
        # неизвестный адрес в демо-режиме тоже анализируем: создаём «типичный» токен
        if query.startswith("0x") or len(query) >= 32:
            rng = _rng(query, "unknown")
            profile_name = rng.choice(["mid", "thin", "solid"])
            item = {
                "symbol": f"T{query[2:6].upper()}",
                "name": f"Unknown Token {query[:8]}",
                "chain": "ethereum",
                "profile": profile_name,
                "address": query,
                "price": round(rng.uniform(0.001, 5.0), 6),
                "liquidity": rng.uniform(120_000, 3_000_000),
                "mcap": rng.uniform(1_000_000, 40_000_000),
                "volume_24h": rng.uniform(400_000, 12_000_000),
                "age_days": rng.randint(5, 400),
            }
            self._extra.append(item)
            self._tokens = self._build_tokens()
            return [t for t in self._tokens if t.address.lower() == query.lower()]
        return []

    async def klines(self, token: TokenCandidate, timeframe: str, limit: int = 300) -> list[Candle]:
        """Регим-переключающееся случайное блуждание (тренд → флэт → гэп)."""
        tf_sec = _tf_seconds(timeframe)
        rng = _rng(token.address, timeframe, limit)
        profile = token.extra.get("profile", "mid")
        base_vol = {"solid": 0.006, "mid": 0.011, "thin": 0.026, "fresh": 0.035}.get(profile, 0.018)
        if profile.startswith("scam") or profile == "honeypot":
            base_vol = 0.03

        candles: list[Candle] = []
        price = token.price_usd * (1 / (1 + token.price_change_24h_pct / 100)) ** (limit / 96)
        price = max(price, token.price_usd * 0.35)
        now = now_ms()
        start = now - limit * tf_sec * 1000

        drift = 0.0
        segment_left = 0
        for i in range(limit):
            if segment_left <= 0:
                segment_left = rng.randint(12, 40)
                drift = rng.choice([-1.0, 0.0, 0.6, 1.0]) * rng.uniform(0.0, base_vol * 0.55)
            shock = rng.gauss(0, base_vol)
            if rng.random() < 0.012:  # «новостной» гэп
                shock += rng.choice([-1, 1]) * rng.uniform(0.03, 0.09)
            open_ = price
            close = max(open_ * (1 + drift + shock), open_ * 0.75)
            high = max(open_, close) * (1 + abs(rng.gauss(0, base_vol * 0.6)))
            low = min(open_, close) * (1 - abs(rng.gauss(0, base_vol * 0.6)))
            base_volume = token.volume_24h_usd / 96.0 / max(open_, 1e-12)
            volume = max(1.0, base_volume * rng.uniform(0.35, 2.4) * (1 + abs(shock) * 12))
            candles.append(
                Candle(
                    ts_ms=int(start + i * tf_sec * 1000),
                    open=round(open_, 10),
                    high=round(high, 10),
                    low=round(low, 10),
                    close=round(close, 10),
                    volume=round(volume, 4),
                )
            )
            price = close
            segment_left -= 1

        # подтягиваем последнюю цену к текущей, чтобы отчёт был согласован
        if candles and token.price_usd > 0:
            scale = token.price_usd / candles[-1].close
            for c in candles:
                c.open *= scale
                c.high *= scale
                c.low *= scale
                c.close *= scale
        return candles

    async def orderbook(self, token: TokenCandidate, depth: int = 50) -> OrderBookSnapshot:
        """
        Стакан из ликвидности пула: чем меньше LP, тем тоньше книга.

        Для «тонких» профилей глубины намеренно не хватает на вход $5k — так
        проверяется расчёт проскальзывания и вердикт «Не входить».
        """
        rng = _rng(token.address, "book", depth)
        mid = token.price_usd
        if mid <= 0:
            return None
        # доступная глубина ≈ 8% ликвидности пула (остальное «спрятано» дальше ±1%)
        usable = token.liquidity_usd * 0.08
        profile = token.extra.get("profile", "mid")
        usable *= {"thin": 0.12, "fresh": 0.4}.get(profile, 1.0)

        levels_each = max(5, depth // 2)
        bids: list[OrderBookLevel] = []
        asks: list[OrderBookLevel] = []
        step = max(mid * 0.0004, 1e-12)
        remaining = usable
        for i in range(levels_each):
            share = rng.uniform(0.4, 1.6) / levels_each
            usd = max(remaining * share, 10.0)
            qty = usd / mid
            bids.append(OrderBookLevel(price=round(mid - step * (i + 1), 10), qty=round(qty, 6)))
            asks.append(OrderBookLevel(price=round(mid + step * (i + 1), 10), qty=round(qty * rng.uniform(0.7, 1.3), 6)))
            remaining -= usd

        # стена: у «тонких» токенов крупный аск близко к цене — честный риск
        if profile in ("thin", "fresh") and asks:
            asks[2] = OrderBookLevel(price=asks[2].price, qty=asks[2].qty * 0.15)
        return OrderBookSnapshot(
            symbol=token.symbol, bids=bids, asks=asks, ts_ms=now_ms(), source="demo", is_stub=True
        )

    async def holders(self, token: TokenCandidate) -> HolderStats:
        p = _PROFILES[token.extra.get("profile", "mid")]
        rng = _rng(token.address, "holders")
        return HolderStats(
            top1_pct=round(p["top1"] * rng.uniform(0.9, 1.1), 2),
            top10_pct=round(p["top10"] * rng.uniform(0.95, 1.05), 2),
            holders_count=int(p["holders"] * rng.uniform(0.9, 1.15)),
            deployer_pct=round(p["top1"] * 0.35, 2),
            lp_in_top10=True,
            source="demo",
            is_stub=True,
        )

    async def lp_lock(self, token: TokenCandidate) -> LpLockInfo:
        p = _PROFILES[token.extra.get("profile", "mid")]
        locked_until = None if p["lp_days"] <= 0 else int(now_ms() + p["lp_days"] * 86_400_000)
        return LpLockInfo(
            locked_pct=p["lp_locked"],
            locked_until_ms=locked_until,
            lock_days_left=float(p["lp_days"]),
            locker="unicrypt" if p["lp_locked"] >= 90 else "team.finance" if p["lp_locked"] > 0 else "",
            source="demo",
            is_stub=True,
        )

    async def contract_risk(self, token: TokenCandidate) -> ContractRisk:
        p = _PROFILES[token.extra.get("profile", "mid")]
        functions: list[str] = ["transfer", "approve"]
        if p["mint"]:
            functions += ["mint", "increaseSupply"]
        if p["blacklist"]:
            functions += ["blacklist", "setBlacklist"]
        return ContractRisk(
            is_mintable=p["mint"],
            has_blacklist=p["blacklist"],
            has_owner=True,
            owner_can_change_balance=p["honeypot"],
            is_proxy=False,
            is_honeypot=p["honeypot"],
            buy_tax_pct=p["buy_tax"],
            sell_tax_pct=p["sell_tax"],
            source_verified=p["verified"],
            is_open_source=p["verified"],
            cannot_sell_all=p["honeypot"],
            cannot_buy=False,
            owner_address="0x" + hashlib.sha256((token.address + "owner").encode()).hexdigest()[:38],
            functions_found=functions,
            source="demo",
            is_stub=True,
        )

    async def deployer(self, token: TokenCandidate) -> DeployerInfo:
        p = _PROFILES[token.extra.get("profile", "mid")]
        rng = _rng(token.address, "deployer")
        address = "0x" + hashlib.sha256((token.address + "dev").encode()).hexdigest()[:38]
        return DeployerInfo(
            address=address,
            age_days=float(p["dev_age"]),
            first_tx_ms=int(now_ms() - p["dev_age"] * 86_400_000),
            tx_count=int(p["dev_tx"]),
            tokens_deployed=int(p["dev_tokens"]),
            funded_by="0x" + hashlib.sha256((token.address + "funder").encode()).hexdigest()[:38],
            funded_by_age_hours=float(p["dev_age"]) * 24 * rng.uniform(0.5, 1.5),
            balance_native=round(rng.uniform(0.2, 40.0), 4),
            sold_out=p["sold"],
            flagged=p["dev_tokens"] > 25,
            prior_projects=[f"PROJ{i}" for i in range(min(3, p["dev_tokens"]))] if p["dev_tokens"] > 5 else [],
            source="demo",
            is_stub=True,
        )

    async def close(self) -> None:
        """Синтетическому провайдеру закрывать нечего — метод нужен для контракта."""
        return None

    async def social(self, token: TokenCandidate, window_hours: int = 2) -> SocialReport:
        p = _PROFILES[token.extra.get("profile", "mid")]
        rng = _rng(token.address, "social", window_hours)
        mentions = int(p["mentions"] * rng.uniform(0.7, 1.3))
        scale = math.sqrt(window_hours / 2.0)
        mentions = int(mentions * scale)
        return SocialReport(
            window_hours=window_hours,
            mentions=mentions,
            unique_authors=int(mentions * rng.uniform(0.55, 0.85)),
            hype_score=round(min(100.0, p["hype"] * rng.uniform(0.85, 1.15)), 1),
            sentiment=round(p["sentiment"] * rng.uniform(0.7, 1.2), 2),
            top_posts=[
                f"${token.symbol} пробил локальный хай, объёмы x{rng.uniform(1.5, 6):.1f}",
                f"Команда {token.symbol} выкатила апдейт роадмапа",
                f"Кит докупил {token.symbol} на ${rng.uniform(20, 400):.0f}k",
            ][:3],
            keywords=[token.symbol.lower(), "airdrop", "listing"],
            source="demo",
            is_stub=True,
        )


def _tf_seconds(timeframe: str) -> int:
    table = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14_400, "1d": 86_400}
    return table.get(timeframe.lower(), 3600)
