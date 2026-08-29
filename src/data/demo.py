"""
Демо-источник рыночных данных: детерминированный синтетический рынок.

Нужен для двух вещей (подход из Hummingbot/OctoBot — симуляция до живых денег):
  1. Бот работает и тестируется без доступа к биржам.
  2. Пользователь видит реалистичную аналитику до подключения API-ключей.

Рынок генерируется регим-переключающимся случайным блужданием: сегменты
тренда/флэта, кластеры волатильности, редкие «новостные» гэпы. Часть монет
намеренно получает импульс за последние 24ч — чтобы сканер находил «скрытые»
монеты. Всё детерминировано: сид = хеш(символ, таймфрейм, эпоха).
"""

from __future__ import annotations

import hashlib
import math

import numpy as np
import pandas as pd

from src.config.settings import Settings
from src.core.errors import NotEnoughData, UnknownSymbol
from src.core.logging import get_logger
from src.core.timeutil import now_ms, tf_ms
from src.data.models import (
    CoinMover,
    FearGreed,
    FundingEntry,
    GlobalStats,
    Instrument,
    Liquidation,
    NewsItem,
    OrderBook,
    Ticker,
)

logger = get_logger("data.demo")

# ── Вселенная демо-рынка: символ → (базовая цена, оборот 24ч в USDT) ──
UNIVERSE: dict[str, tuple[float, float]] = {
    "BTCUSDT": (67_500.0, 24e9),
    "ETHUSDT": (3_480.0, 12e9),
    "SOLUSDT": (172.0, 4.1e9),
    "BNBUSDT": (598.0, 1.9e9),
    "XRPUSDT": (0.61, 1.6e9),
    "DOGEUSDT": (0.158, 1.3e9),
    "ADAUSDT": (0.448, 0.62e9),
    "AVAXUSDT": (36.4, 0.51e9),
    "LINKUSDT": (17.2, 0.66e9),
    "TONUSDT": (7.15, 0.34e9),
    "DOTUSDT": (6.85, 0.29e9),
    "TRXUSDT": (0.128, 0.41e9),
    "MATICUSDT": (0.71, 0.33e9),
    "LTCUSDT": (83.5, 0.42e9),
    "NEARUSDT": (6.42, 0.38e9),
    "APTUSDT": (9.05, 0.24e9),
    "SUIUSDT": (1.34, 0.72e9),
    "TIAUSDT": (8.75, 0.31e9),
    "INJUSDT": (26.8, 0.22e9),
    "SEIUSDT": (0.46, 0.19e9),
    "OPUSDT": (2.28, 0.27e9),
    "ARBUSDT": (1.12, 0.36e9),
    "ATOMUSDT": (8.62, 0.18e9),
    "FILUSDT": (5.94, 0.21e9),
    "AAVEUSDT": (104.5, 0.26e9),
    "UNIUSDT": (9.85, 0.23e9),
    "MKRUSDT": (2_640.0, 0.11e9),
    "LDOUSDT": (2.14, 0.13e9),
    "RNDRUSDT": (7.42, 0.17e9),
    "FETUSDT": (2.31, 0.25e9),
    "IMXUSDT": (1.94, 0.12e9),
    "GRTUSDT": (0.245, 0.11e9),
    "ALGOUSDT": (0.238, 0.10e9),
    "XLMUSDT": (0.112, 0.15e9),
    "ETCUSDT": (27.6, 0.19e9),
    "ICPUSDT": (11.4, 0.12e9),
    "HBARUSDT": (0.092, 0.14e9),
    "VETUSDT": (0.031, 0.11e9),
    "SANDUSDT": (0.442, 0.13e9),
    "MANAUSDT": (0.461, 0.10e9),
    "GALAUSDT": (0.0285, 0.12e9),
    "AXSUSDT": (7.24, 0.10e9),
    "CHZUSDT": (0.0985, 0.11e9),
    "1000SHIBUSDT": (0.0245, 0.42e9),
    "1000PEPEUSDT": (0.0118, 0.98e9),
    "PEPEUSDT": (0.0000118, 0.98e9),
    "WIFUSDT": (2.42, 0.31e9),
    "BONKUSDT": (0.0000265, 0.28e9),
    "FLOKIUSDT": (0.000198, 0.17e9),
    "MEMEUSDT": (0.0221, 0.13e9),
    "ORDIUSDT": (43.8, 0.21e9),
    "JUPUSDT": (1.08, 0.24e9),
    "PYTHUSDT": (0.412, 0.15e9),
    "JTOUSDT": (3.05, 0.12e9),
    "WUSDT": (0.585, 0.10e9),
    "ENAUSDT": (0.86, 0.33e9),
    "ETHFIUSDT": (3.14, 0.16e9),
    "ONDOUSDT": (1.02, 0.19e9),
    "STXUSDT": (2.06, 0.10e9),
    "RUNEUSDT": (5.18, 0.14e9),
    "PENDLEUSDT": (5.42, 0.13e9),
    "NOVAUSDT": (0.385, 0.062e9),
    "ZKUSDT": (0.185, 0.071e9),
    "MYROUSDT": (0.142, 0.048e9),
    "AEROUSDT": (1.24, 0.055e9),
    "VELOUSDT": (0.0211, 0.031e9),
    "SLERFUSDT": (0.275, 0.029e9),
}

# Сценарии для «новостного» фона демо-рынка
NEWS_TEMPLATES: list[tuple[str, float, list[str]]] = [
    ("{base} запускает основную сеть и объявляет о листинге на новых биржах", 0.62, []),
    ("Крупный фонд докупил {base}: приток средств в фонд за неделю вырос", 0.48, []),
    ("{base} объявляет о партнёрстве с платёжным провайдером", 0.41, []),
    ("Команда {base} раскрывает дорожную карту: стейкинг и L2-масштабирование", 0.33, []),
    ("Объём торгов {base} на фьючерсах обновил месячный максимум", 0.22, []),
    ("Разблокировка токенов {base}: давление продаж в ближайшие дни", -0.46, []),
    ("Регулятор запросил документы у эмитента {base}", -0.52, []),
    ("Киты выводят {base} на биржи: возможен откат", -0.31, []),
    ("BTC удерживает ключевой уровень, альткоины растут", 0.28, ["BTC"]),
    ("Индекс страха и жадности показывает перегрев рынка", -0.24, ["BTC", "ETH"]),
    ("Спотовые ETF фиксируют рекордный приток за сутки", 0.55, ["BTC", "ETH"]),
    ("Ставки финансирования по фьючерсам ушли в отрицательную зону", -0.18, []),
]

_EPOCH = now_ms()  # фиксируем в момент импорта → рынок стабилен в рамках процесса


def _seed(*parts) -> int:
    raw = "|".join(str(p) for p in parts).encode()
    return int.from_bytes(hashlib.blake2b(raw, digest_size=8).digest(), "big")


def _price_scales(price: float) -> tuple[int, float, int, float]:
    """(price_scale, tick_size, qty_scale, qty_step) по порядку величины цены."""
    if price >= 1000:
        return 2, 0.01, 5, 0.00001
    if price >= 100:
        return 2, 0.01, 4, 0.0001
    if price >= 1:
        return 4, 0.0001, 2, 0.01
    if price >= 0.01:
        return 5, 0.00001, 1, 0.1
    return 8, 0.00000001, 0, 1.0


class DemoMarketSource:
    """Синтетический рынок с полным набором эндпоинтов живого источника."""

    name = "demo"
    is_demo = True

    def __init__(self, settings: Settings):
        self.settings = settings
        self._klines_cache: dict[tuple[str, str, int], pd.DataFrame] = {}
        self._instruments = self._build_instruments()
        self._epoch = _EPOCH

    # ── инструменты ──
    @staticmethod
    def _build_instruments() -> list[Instrument]:
        out: list[Instrument] = []
        for symbol, (base_price, turnover) in UNIVERSE.items():
            base = symbol[:-4]
            if base.startswith("1000"):
                base = base[4:]
            p_scale, tick, q_scale, qstep = _price_scales(base_price)
            min_notional = 5.0 if turnover > 0.1e9 else 1.0
            out.append(
                Instrument(
                    symbol=symbol,
                    base=base,
                    quote="USDT",
                    category="linear",
                    status="Trading",
                    price_scale=p_scale,
                    qty_scale=q_scale,
                    tick_size=tick,
                    qty_step=qstep,
                    min_qty=qstep,
                    min_notional=min_notional,
                    max_leverage=100 if turnover > 1e9 else (50 if turnover > 0.1e9 else 25),
                    turnover_24h=turnover,
                )
            )
        return out

    async def discover_instruments(self, category: str = "linear") -> list[Instrument]:
        return list(self._instruments)

    def get_instrument(self, symbol: str) -> Instrument | None:
        symbol = symbol.upper()
        for i in self._instruments:
            if i.symbol == symbol:
                return i
        return None

    # ── свечи ──
    async def get_klines(self, symbol: str, timeframe: str = "15m", limit: int = 300) -> pd.DataFrame:
        symbol = symbol.upper()
        if symbol not in UNIVERSE:
            raise UnknownSymbol(f"{symbol}: инструмент отсутствует в демо-вселенной")
        step = tf_ms(timeframe)
        end = (self._epoch // step) * step
        key = (symbol, timeframe, end)
        cached = self._klines_cache.get(key)
        if cached is not None and len(cached) >= limit:
            return cached.tail(limit).reset_index(drop=True).copy()

        df = self._generate(symbol, timeframe, limit, end)
        if len(self._klines_cache) > 256:  # не раздуваем память
            self._klines_cache.clear()
        self._klines_cache[key] = df
        return df.tail(limit).reset_index(drop=True).copy()

    def _generate(self, symbol: str, timeframe: str, limit: int, end_ms: int) -> pd.DataFrame:
        step = tf_ms(timeframe)
        base_price, base_turnover = UNIVERSE[symbol]
        rng = np.random.default_rng(_seed(symbol, timeframe, self._epoch // (step * 96)))

        n = max(limit, 60)
        bars_24h = max(1, int(86_400_000 / step))

        # «скрытые» монеты с малым оборотом — чаще дают импульсы
        mover = (_seed(symbol, "mover") % 5) == 0
        sigma = rng.uniform(0.0035, 0.0075) if symbol in ("BTCUSDT", "ETHUSDT") else rng.uniform(0.006, 0.016)
        if mover:
            sigma *= 1.45

        # 1. Режим-переключающееся блуждание в лог-пространстве
        log_path = np.zeros(n)
        mu, seg_left = 0.0, 0
        for i in range(1, n):
            if seg_left <= 0:
                seg_left = int(rng.integers(18, 70))
                mu = rng.normal(0.0, sigma * 0.35)
            log_path[i] = log_path[i - 1] + mu + sigma * rng.standard_normal()
            seg_left -= 1

        # кластеризация волатильности (GARCH-подобный множитель)
        vol_mult = np.ones(n)
        for _ in range(rng.integers(2, 5)):
            start = int(rng.integers(0, n))
            span = int(rng.integers(10, 60))
            vol_mult[start : start + span] *= rng.uniform(1.4, 2.6)
        noise = sigma * rng.standard_normal(n) * vol_mult * 0.45
        log_path = log_path + np.cumsum(noise * 0.12)

        # 2. Новостные гэпы
        for _ in range(int(rng.integers(0, 4))):
            at = int(rng.integers(n // 4, n))
            log_path[at:] += rng.normal(0.0, sigma * 2.2)

        # 3. Точное попадание в целевое изменение за 24ч
        target = (
            rng.uniform(9.0, 23.0) * rng.choice([-1.0, 1.0])
            if mover
            else rng.uniform(-3.2, 3.2)
        )
        i24 = max(0, n - bars_24h)
        current = log_path[-1] - log_path[i24]
        delta = math.log1p(target / 100.0) - current
        ramp = np.zeros(n)
        ramp[i24:] = np.linspace(0.0, 1.0, n - i24)
        log_path = log_path + delta * ramp

        # 4. Абсолютные цены
        close = base_price * np.exp(log_path - log_path[-1])
        open_ = np.concatenate([[close[0]], close[:-1]])
        body_hi = np.maximum(open_, close)
        body_lo = np.minimum(open_, close)
        wick = np.abs(rng.normal(0.0, 0.0022, n)) * (0.6 + vol_mult * 0.25)
        high = body_hi * (1.0 + wick)
        low = body_lo * (1.0 - wick)
        high = np.maximum(high, body_hi)
        low = np.minimum(low, body_lo)

        # 5. Объём: база + всплески на импульсах
        v_base = base_turnover / max(base_price, 1e-9) / 96.0
        vol = v_base * np.abs(rng.lognormal(0.0, 0.42, n)) * (0.55 + vol_mult * 0.35)
        vol[-3:] *= rng.uniform(1.1, 1.9)

        ts = end_ms - step * (n - 1 - np.arange(n))
        return pd.DataFrame(
            {
                "ts": ts.astype("int64"),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": vol,
            }
        )

    # ── тикеры ──
    async def get_tickers(self, symbols: list[str] | None = None) -> list[Ticker]:
        wanted = [s.upper() for s in symbols] if symbols else list(UNIVERSE)
        out: list[Ticker] = []
        for symbol in wanted:
            if symbol not in UNIVERSE:
                continue
            df = self._generate(symbol, "15m", 120, (self._epoch // 900_000) * 900_000)
            close = float(df["close"].iloc[-1])
            bars_24h = min(len(df) - 1, 96)
            prev = float(df["close"].iloc[-1 - bars_24h])
            pct = (close - prev) / prev * 100.0
            base_price, base_turnover = UNIVERSE[symbol]
            turnover = base_turnover * float(np.random.default_rng(_seed(symbol, "to", self._epoch)).uniform(0.75, 1.3))
            rng = np.random.default_rng(_seed(symbol, "tick", self._epoch))
            funding = float(np.clip(rng.normal(0.0001, 0.0006), -0.0035, 0.0035))
            if pct > 8:
                funding = abs(funding) + 0.0004
            elif pct < -8:
                funding = -abs(funding) - 0.0003
            spread = close * rng.uniform(0.00005, 0.0006)
            out.append(
                Ticker(
                    symbol=symbol,
                    last=close,
                    price_24h_pct=round(pct, 2),
                    turnover_24h=turnover,
                    volume_24h=turnover / max(close, 1e-9),
                    high_24h=float(df["high"].iloc[-bars_24h:].max()),
                    low_24h=float(df["low"].iloc[-bars_24h:].min()),
                    open_24h=prev,
                    bid=close - spread / 2,
                    ask=close + spread / 2,
                    funding_rate=funding,
                    open_interest=turnover * rng.uniform(0.25, 0.9) / max(close, 1e-9),
                    open_interest_usd=turnover * rng.uniform(0.25, 0.9),
                    ts_ms=self._epoch,
                )
            )
        return out

    # ── деривативы ──
    async def get_funding(self, symbol: str, limit: int = 12) -> list[FundingEntry]:
        symbol = symbol.upper()
        if symbol not in UNIVERSE:
            raise UnknownSymbol(symbol)
        rng = np.random.default_rng(_seed(symbol, "funding", self._epoch // 3_600_000))
        base = float(np.clip(rng.normal(0.0001, 0.0005), -0.003, 0.003))
        drift = rng.normal(0.0, 0.00012)
        out: list[FundingEntry] = []
        for i in range(limit):
            rate = base + drift * i + rng.normal(0.0, 0.00008)
            out.append(FundingEntry(ts_ms=self._epoch - (limit - 1 - i) * 28_800_000, rate=float(rate), symbol=symbol))
        return out

    async def get_recent_liquidations(self, limit: int = 200) -> list[Liquidation]:
        rng = np.random.default_rng(_seed("liq", self._epoch // 900_000))
        symbols = list(UNIVERSE)
        out: list[Liquidation] = []
        for i in range(limit):
            symbol = symbols[int(rng.integers(0, len(symbols)))]
            base_price = UNIVERSE[symbol][0]
            side = "Sell" if rng.random() < 0.5 else "Buy"
            size = float(np.exp(rng.uniform(10.5, 14.0)))
            out.append(
                Liquidation(
                    symbol=symbol,
                    side=side,
                    size=size,
                    qty=size / base_price,
                    price=base_price * rng.uniform(0.97, 1.03),
                    ts_ms=self._epoch - int(rng.integers(0, 3_600_000)),
                )
            )
        out.sort(key=lambda x: x.ts_ms, reverse=True)
        return out

    async def get_orderbook(self, symbol: str, depth: int = 25) -> OrderBook:
        symbol = symbol.upper()
        if symbol not in UNIVERSE:
            raise UnknownSymbol(symbol)
        rng = np.random.default_rng(_seed(symbol, "ob", self._epoch // 900_000))
        df = self._generate(symbol, "15m", 40, (self._epoch // 900_000) * 900_000)
        mid = float(df["close"].iloc[-1])
        inst = self.get_instrument(symbol)
        tick = inst.tick_size if inst else 0.0001
        bias = float(rng.uniform(-0.55, 0.55))
        # глубина одного уровня в USD привязана к обороту монеты
        base_turnover = UNIVERSE[symbol][1]
        usd_per_level = max(2_000.0, base_turnover / 4_000.0)
        bids, asks = [], []
        for i in range(depth):
            p_b = mid - tick * (i + 1) * rng.uniform(0.8, 2.2)
            p_a = mid + tick * (i + 1) * rng.uniform(0.8, 2.2)
            decay = math.exp(-i / 9.0)  # дальше от цены — тоньше
            usd_b = usd_per_level * decay * rng.uniform(0.5, 1.7) * (1.0 + bias)
            usd_a = usd_per_level * decay * rng.uniform(0.5, 1.7) * (1.0 - bias)
            if p_b > 0:
                bids.append((p_b, max(usd_b, 0.0) / p_b))
            if p_a > 0:
                asks.append((p_a, max(usd_a, 0.0) / p_a))
        return OrderBook(symbol=symbol, bids=bids, asks=asks, ts_ms=self._epoch)

    # ── спот-источники (CoinGecko-подобные) ──
    async def get_spot_movers(self, limit: int = 25) -> list[CoinMover]:
        tickers = await self.get_tickers()
        tickers.sort(key=lambda t: abs(t.price_24h_pct), reverse=True)
        out: list[CoinMover] = []
        for rank, t in enumerate(tickers[:limit], 1):
            out.append(
                CoinMover(
                    symbol=t.symbol,
                    name=t.symbol.replace("USDT", ""),
                    rank=rank + 20,
                    price=t.last,
                    price_24h_pct=t.price_24h_pct,
                    volume_24h=t.turnover_24h,
                    market_cap=t.turnover_24h * float(
                        np.random.default_rng(_seed(t.symbol, "cap")).uniform(6.0, 60.0)
                    ),
                )
            )
        return out

    async def get_trending(self, limit: int = 12) -> list[CoinMover]:
        tickers = await self.get_tickers()
        # «тренд» = необычный объём, а не только движение
        tickers.sort(key=lambda t: t.turnover_24h / max(UNIVERSE[t.symbol][1], 1.0), reverse=True)
        return [
            CoinMover(
                symbol=t.symbol,
                name=t.symbol.replace("USDT", ""),
                rank=100 + i,
                price=t.last,
                price_24h_pct=t.price_24h_pct,
                volume_24h=t.turnover_24h,
                market_cap=None,
            )
            for i, t in enumerate(tickers[:limit])
        ]

    # ── рынок в целом ──
    async def get_fear_greed(self) -> FearGreed:
        tickers = await self.get_tickers()
        weighted = [t.price_24h_pct for t in tickers if t.turnover_24h > 1e8] or [0.0]
        avg = float(np.mean(weighted))
        value = int(np.clip(50.0 + avg * 9.0, 0, 100))
        return FearGreed(value=value, classification=_fear_label(value), ts_ms=self._epoch)

    async def get_global_stats(self) -> GlobalStats:
        fg = await self.get_fear_greed()
        tickers = await self.get_tickers()
        total_vol = sum(t.turnover_24h for t in tickers)
        return GlobalStats(
            total_market_cap_usd=2.42e12,
            total_volume_24h_usd=total_vol,
            btc_dominance=52.4,
            eth_dominance=17.1,
            market_cap_change_24h_pct=float(np.mean([t.price_24h_pct for t in tickers])),
            fear_greed=fg,
            ts_ms=self._epoch,
        )

    # ── новости ──
    async def get_news(self, limit: int = 20) -> list[NewsItem]:
        rng = np.random.default_rng(_seed("news", self._epoch // 3_600_000))
        symbols = [s for s in UNIVERSE if not s.startswith("1000")]
        out: list[NewsItem] = []
        for i in range(limit):
            template, sentiment, fixed = NEWS_TEMPLATES[int(rng.integers(0, len(NEWS_TEMPLATES)))]
            sym = symbols[int(rng.integers(0, len(symbols)))]
            base = sym.replace("USDT", "")
            title = template.format(base=base)
            syms = fixed or [base]
            out.append(
                NewsItem(
                    id=f"demo-{self._epoch // 3_600_000}-{i}",
                    ts_ms=self._epoch - int(rng.integers(0, 6 * 3_600_000)),
                    source="demo-feed",
                    title=title,
                    url="",
                    symbols=syms,
                    sentiment=float(np.clip(sentiment + rng.normal(0.0, 0.08), -1.0, 1.0)),
                )
            )
        out.sort(key=lambda n: n.ts_ms, reverse=True)
        return out[:limit]

    async def close(self) -> None:
        self._klines_cache.clear()


def _fear_label(value: int) -> str:
    if value <= 24:
        return "Extreme Fear"
    if value <= 44:
        return "Fear"
    if value <= 55:
        return "Neutral"
    if value <= 75:
        return "Greed"
    return "Extreme Greed"
