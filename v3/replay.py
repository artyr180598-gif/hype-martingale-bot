"""Офлайн-прогон движка на РЕАЛЬНЫХ рыночных данных, снятых с биржи.

Зачем это нужно
---------------
Юнит-тесты проверяют логику, но на синтетических сигналах. Живой запуск
(``python -m v3 signal BTCUSDT``) требует доступ к бирже. Реплей закрывает
середину: настоящие свечи, тикер, ставка финансирования, открытый интерес и
стакан с биржи прогоняются через ТЕ ЖЕ кодовые пути, что и прод:

    SnapshotSource → FuturesDataService → FuturesSignalEngine.analyze
        → render_signal (карточка) + assess_confidence + evaluate_alert

— без единого HTTP-запроса и без синтетических данных.

Форматы файла
-------------
* ``kind: "okx_capture_v1"`` — дословные ответы публичного REST OKX v5
  (пример: ``v3/tests/fixtures/okx_btcusdt_swap_capture.json``);
* ``kind: "snapshot_v1"`` — нормализованный снапшот; его пишет
  ``python -m v3 record BTCUSDT --out data/replay/btcusdt.json`` через
  прод-сервис ``FuturesDataService`` (нужен доступ к бирже);
* ``kind: "okx_candles_v1"`` — длинная серия свечей одного таймфрейма для
  бэктеста (``--backtest``); тикера и стакана в ней нет и не нужно.

Про время
---------
Часы реплея фиксируются на моменте съёма снапшота. Иначе данные, которые были
свежими на момент съёма, выглядели бы устаревшими и бот честно ответил бы
«нет данных». Цены, объёмы и таймстемпы свечей при этом не изменяются.

Команды
-------
    python -m v3 replay <файл> [--mode pro] [--walk 10] [--step 3] [--json]
    python -m v3 replay <файл> --backtest [--warmup 120] [--json]
    python -m v3 record BTCUSDT [--out data/replay/btcusdt.json]

Третья строка — прогон той же серии реальных свечей через прод-бэктестер
(``v3/backtest.py``): метрики (win rate, profit factor, просадка) считаются на
реальных барах, а не на синтетике. Пример файла: фикстура
``v3/tests/fixtures/okx_btcusdt_15m_300.json`` (300 свечей 15m BTC-USDT-SWAP).
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

import pandas as pd

from src.core.errors import DataSourceError, UnknownSymbol
from src.core.logging import get_logger
from src.data.collector import MarketDataSource
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
    base_of,
    normalize_symbol,
)
from v3.alerts import evaluate_alert
from v3.analysis.confidence import assess_confidence
from v3.config import SignalConfig
from v3.data import FuturesDataService
from v3.engine import FuturesSignalEngine
from v3.report import render_signal

logger = get_logger("v3.replay")

TF_MS: dict[str, int] = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "12h": 43_200_000, "1d": 86_400_000,
}
# bar-коды OKX для наших таймфреймов (нужны только для подписи источника)
OKX_BAR: dict[str, str] = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1H",
    "2h": "2H", "4h": "4H", "6h": "6H", "12h": "12H", "1d": "1D",
}


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default  # NaN != NaN


# ════════════════════════════════════════════════════════════════
#  ФОРМАТЫ: OKX capture → нормализованный снапшот
# ════════════════════════════════════════════════════════════════
def parse_okx_candles(rows: list[Any]) -> list[list[float]]:
    """Нативные строки OKX → ``[ts_ms, open, high, low, close, volume]``.

    Строка OKX: ``[ts, open, high, low, close, vol(контракты), volCcy(базовый
    актив), volCcyQuote(оборот в USDT), confirm]``. В прод-коннекторе Bybit
    ``volume = r[5]`` — объём в базовом активе, поэтому берём ``volCcy``
    (индекс 6), а не оборот: иначе RVOL/OBV считались бы по другой величине,
    чем в live.
    """
    out: list[list[float]] = []
    for row in rows:
        ts = _num(row[0])
        o, h, low, c = (_num(row[i]) for i in (1, 2, 3, 4))
        if None in (ts, o, h, low, c):
            continue
        vol = _num(row[6]) if len(row) > 6 else None
        if vol is None:
            vol = _num(row[5], 0.0)
        out.append([int(ts), o, h, low, c, vol or 0.0])  # type: ignore[arg-type]
    out.sort(key=lambda r: r[0])
    return out


def _okx_symbol(capture: dict[str, Any]) -> str:
    """``BTC-USDT-SWAP`` → ``BTCUSDT``.

    ``normalize_symbol`` не знает про суффикс ``-SWAP`` у OKX и сделал бы
    ``BTCUSDTSWAPUSDT``, поэтому торговую приставку снимаем здесь.
    """
    explicit = str(capture.get("symbol") or "").strip()
    if explicit:
        return normalize_symbol(explicit)
    inst = str(capture.get("inst_id") or "").strip().upper()
    parts = [p for p in inst.split("-") if p and p not in ("SWAP", "FUTURES", "PERP")]
    return normalize_symbol("".join(parts))


def okx_capture_to_snapshot(capture: dict[str, Any]) -> dict[str, Any]:
    """Дословный ответ OKX v5 → нормализованный ``snapshot_v1``."""
    ticker_raw = (capture.get("ticker") or [{}])[0]
    funding_raw = (capture.get("funding_rate") or [{}])[0]
    oi_raw = (capture.get("open_interest") or [{}])[0]
    book_raw = (capture.get("books") or [{}])[0]

    last = _num(ticker_raw.get("last"), 0.0) or 0.0
    open_24h = _num(ticker_raw.get("open24h"), 0.0) or 0.0
    volume_24h = _num(ticker_raw.get("volCcy24h"), 0.0) or 0.0  # базовый актив (BTC)
    # Биржа в тикере отдаёт объём в base; оборот в USDT считаем по последней
    # цене — тот же порядок величины, что turnover24h у Bybit/Binance.
    turnover_24h = _num(ticker_raw.get("volCcyQuote24h")) or round(volume_24h * last, 2)
    ts_ms = int(_num(ticker_raw.get("ts"), 0) or 0)

    klines: dict[str, list[list[float]]] = {}
    for tf, rows in (capture.get("candles") or {}).items():
        parsed = parse_okx_candles(rows or [])
        if parsed:
            klines[str(tf)] = parsed

    funding_rates = [
        r for r in (
            _num(funding_raw.get("settFundingRate")),
            _num(funding_raw.get("fundingRate")),
        ) if r is not None
    ]

    # Уровень стакана OKX: [цена, размер, ликвидированный объём, число ордеров] —
    # нужны только первые два поля.
    bids = [[_num(lvl[0]) or 0.0, _num(lvl[1]) or 0.0] for lvl in (book_raw.get("bids") or []) if len(lvl) >= 2]
    asks = [[_num(lvl[0]) or 0.0, _num(lvl[1]) or 0.0] for lvl in (book_raw.get("asks") or []) if len(lvl) >= 2]

    return {
        "kind": "snapshot_v1",
        "symbol": _okx_symbol(capture),
        "source": capture.get("source") or "okx",
        "captured_at_ms": int(
            _num(capture.get("captured_at_ms"))
            or _num(book_raw.get("ts"))
            or ts_ms
            or 0
        ),
        "klines": klines,
        "ticker": {
            "last": last,
            "bid": _num(ticker_raw.get("bidPx"), 0.0) or 0.0,
            "ask": _num(ticker_raw.get("askPx"), 0.0) or 0.0,
            "open_24h": open_24h,
            "high_24h": _num(ticker_raw.get("high24h"), 0.0) or 0.0,
            "low_24h": _num(ticker_raw.get("low24h"), 0.0) or 0.0,
            "volume_24h": volume_24h,
            "turnover_24h": turnover_24h,
            "funding_rate": _num(funding_raw.get("fundingRate")),
            "next_funding_ms": int(_num(funding_raw.get("nextFundingTime"), 0) or 0),
            "open_interest_usd": _num(oi_raw.get("oiUsd")),
            "ts_ms": ts_ms,
        },
        "funding_history": funding_rates,
        "orderbook": {
            "bids": bids,
            "asks": asks,
            "ts_ms": int(_num(book_raw.get("ts"), 0) or 0),
        },
        "provenance": {
            "kind": capture.get("kind", "okx_capture_v1"),
            "endpoints": capture.get("endpoints", {}),
            "note": capture.get("note", ""),
        },
    }


def load_snapshot(path: str | Path) -> dict[str, Any]:
    """Прочитать снапшот с диска (авто-определение формата)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("kind") == "snapshot_v1":
        return raw
    if raw.get("kind") == "okx_capture_v1" or "candles" in raw and "ticker" in raw:
        return okx_capture_to_snapshot(raw)
    raise ValueError(f"{path}: неизвестный формат снапшота (нужен okx_capture_v1 или snapshot_v1)")


def save_snapshot(snapshot: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return out


def trim_snapshot(snapshot: dict[str, Any], as_of_ms: int) -> dict[str, Any]:
    """Срез снапшота «как его видел бот в момент ``as_of_ms``».

    Оставляем только ЗАКРЫТЫЕ свечи (``ts + длительность <= as_of``) — ровно то,
    что делал бы ``FuturesDataService._closed_bars`` в live. Используется для
    прохода по истории (--walk): никаких заглядываний в будущее.
    """
    klines: dict[str, list[list[float]]] = {}
    for tf, rows in (snapshot.get("klines") or {}).items():
        step = TF_MS.get(str(tf), 0)
        kept = [r for r in rows if int(r[0]) + step <= int(as_of_ms)]
        if kept:
            klines[str(tf)] = kept
    out = dict(snapshot)
    out["klines"] = klines
    return out


# ════════════════════════════════════════════════════════════════
#  ИСТОЧНИК ДАННЫХ: снапшот вместо биржи
# ════════════════════════════════════════════════════════════════
class SnapshotSource(MarketDataSource):
    """``MarketDataSource`` поверх снятого снапшота.

    Реализует тот же контракт, что Bybit/Binance/MEXC, поэтому
    ``FuturesDataService`` и движок не знают, что биржи нет. Всё, чего в
    снапшоте нет (новости, спот-муверы, глобальный контекст, ликвидации),
    честно отдаётся ошибкой/пустым ответом — так же, как живой источник при
    недоступном эндпоинте, и бот показывает «н/д» вместо выдуманных цифр.
    """

    name = "replay"
    mode = "replay"

    def __init__(self, snapshot: dict[str, Any]) -> None:
        self._s = snapshot
        self.symbol = normalize_symbol(snapshot.get("symbol") or "")
        self._frames: dict[str, pd.DataFrame] = {}

    # ── вспомогательное ─────────────────────────────────────────
    def _check_symbol(self, symbol: str) -> None:
        if normalize_symbol(symbol) != self.symbol:
            raise UnknownSymbol(f"{symbol}: в снапшоте есть только {self.symbol}")

    def frame(self, timeframe: str) -> pd.DataFrame:
        if timeframe not in self._frames:
            rows = (self._s.get("klines") or {}).get(timeframe) or []
            self._frames[timeframe] = pd.DataFrame(
                [dict(zip(("ts", "open", "high", "low", "close", "volume"), r)) for r in rows]
            )
        return self._frames[timeframe]

    def _ticker(self) -> Ticker:
        t = dict(self._s.get("ticker") or {})
        last = float(t.get("last") or 0.0)
        open_24h = float(t.get("open_24h") or 0.0)
        pct = ((last / open_24h - 1.0) * 100.0) if open_24h else 0.0
        return Ticker(
            symbol=self.symbol,
            last=last,
            price_24h_pct=round(pct, 4),
            turnover_24h=float(t.get("turnover_24h") or 0.0),
            volume_24h=float(t.get("volume_24h") or 0.0),
            high_24h=float(t.get("high_24h") or 0.0),
            low_24h=float(t.get("low_24h") or 0.0),
            open_24h=open_24h,
            bid=float(t.get("bid") or 0.0),
            ask=float(t.get("ask") or 0.0),
            funding_rate=t.get("funding_rate"),
            next_funding_ms=int(t.get("next_funding_ms") or 0) or None,
            open_interest_usd=t.get("open_interest_usd"),
            ts_ms=int(t.get("ts_ms") or 0),
        )

    # ── контракт MarketDataSource ───────────────────────────────
    async def probe(self) -> str:
        if not (self._s.get("klines") or {}):
            raise DataSourceError("snapshot: нет свечей")
        return self.mode

    async def discover_instruments(self, category: str = "linear") -> list[Instrument]:
        return [Instrument(symbol=self.symbol, base=base_of(self.symbol), category="linear")]

    def get_instrument(self, symbol: str) -> Instrument | None:
        return Instrument(symbol=self.symbol, base=base_of(self.symbol)) if normalize_symbol(symbol) == self.symbol else None

    async def get_klines(self, symbol: str, timeframe: str = "15m", limit: int = 300) -> pd.DataFrame:
        self._check_symbol(symbol)
        df = self.frame(timeframe)
        if df.empty:
            raise DataSourceError(f"snapshot: нет свечей {timeframe}")
        return df.tail(int(limit)).reset_index(drop=True)

    async def get_history(self, symbol: str, timeframe: str = "1h", bars: int = 1000, max_requests: int = 40) -> pd.DataFrame:
        return await self.get_klines(symbol, timeframe, bars)

    async def get_tickers(self, symbols: list[str] | None = None) -> list[Ticker]:
        if symbols is not None:
            wanted = {normalize_symbol(s) for s in symbols}
            if self.symbol not in wanted:
                return []
        return [self._ticker()]

    async def get_funding(self, symbol: str, limit: int = 12) -> list[FundingEntry]:
        self._check_symbol(symbol)
        rates = self._s.get("funding_history") or []
        ts = int((self._s.get("ticker") or {}).get("ts_ms") or 0)
        return [FundingEntry(ts_ms=ts, rate=float(r), symbol=self.symbol) for r in rates[: int(limit)]]

    async def get_recent_liquidations(self, limit: int = 200) -> list[Liquidation]:
        return []  # честно пусто: в снапшоте ликвидаций нет

    async def get_orderbook(self, symbol: str, depth: int = 25) -> OrderBook:
        self._check_symbol(symbol)
        book = self._s.get("orderbook") or {}
        bids = [(float(p), float(q)) for p, q in (book.get("bids") or [])]
        asks = [(float(p), float(q)) for p, q in (book.get("asks") or [])]
        if not bids or not asks:
            raise DataSourceError("snapshot: стакан не снимался")
        return OrderBook(symbol=self.symbol, bids=bids[:depth], asks=asks[:depth], ts_ms=int(book.get("ts_ms") or 0))

    async def get_spot_movers(self, limit: int = 25) -> list[CoinMover]:
        raise DataSourceError("snapshot: спот-муверы не снимались")

    async def get_trending(self, limit: int = 12) -> list[CoinMover]:
        raise DataSourceError("snapshot: тренды не снимались")

    async def get_fear_greed(self) -> FearGreed:
        raise DataSourceError("snapshot: fear&greed не снимался")

    async def get_global_stats(self) -> GlobalStats:
        raise DataSourceError("snapshot: глобальный контекст не снимался")

    async def get_news(self, limit: int = 20) -> list[NewsItem]:
        raise DataSourceError("snapshot: новости не снимались")

    async def get_account_ratio(self, symbol: str) -> float | None:
        return None


def snapshot_availability(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Что в снапшоте РЕАЛЬНОЕ, а чего нет — таблица честности для отчёта."""
    rows: list[dict[str, Any]] = []
    klines = snapshot.get("klines") or {}
    for tf, frame_rows in klines.items():
        rows.append({
            "field": f"свечи {tf}",
            "real": bool(frame_rows),
            "detail": f"{len(frame_rows)} шт., последняя {int(frame_rows[-1][0])}" if frame_rows else "нет",
        })
    t = snapshot.get("ticker") or {}
    rows.append({"field": "тикер (цена/объём 24ч)", "real": bool(t.get("last")), "detail": f"last={t.get('last')}"})
    rates = snapshot.get("funding_history") or []
    rows.append({"field": "ставка финансирования", "real": bool(rates), "detail": f"{len(rates)} значения" if rates else "нет"})
    oi = t.get("open_interest_usd")
    rows.append({"field": "открытый интерес, $", "real": oi is not None, "detail": f"{float(oi):,.0f}" if oi else "нет"})
    book = snapshot.get("orderbook") or {}
    levels = min(len(book.get("bids") or []), len(book.get("asks") or []))
    rows.append({"field": "стакан", "real": levels > 0, "detail": f"{levels} уровней" if levels else "нет"})
    for label in ("ликвидации", "новости/сентимент", "глобальный контекст (CoinGecko)", "long/short ratio"):
        rows.append({"field": label, "real": False, "detail": "не снималось → «н/д»"})
    return rows


# ════════════════════════════════════════════════════════════════
#  ЧАСЫ РЕПЛЕЯ
# ════════════════════════════════════════════════════════════════
@contextmanager
def frozen_clock(as_of_ms: int):
    """``time.time()`` = момент съёма снапшота (+ реальное течение прогона).

    Без этого проверка свежести (``MAX_DATA_AGE_SECONDS``, «stale klines»)
    посчитала бы настоящие, но снятые раньше данные устаревшими. Сами данные
    не меняются — сдвигается только «сейчас».
    """
    base = float(as_of_ms) / 1000.0
    start = time.monotonic()

    def _now() -> float:
        return base + (time.monotonic() - start)

    with mock.patch.object(time, "time", _now):
        yield


# ════════════════════════════════════════════════════════════════
#  ПРОГОН
# ════════════════════════════════════════════════════════════════
@dataclass
class ReplayResult:
    symbol: str
    as_of_ms: int
    signal: Any
    confidence: Any
    alert: Any
    card: str
    availability: list[dict[str, Any]] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        sig = self.signal
        return {
            "symbol": self.symbol,
            "as_of_ms": self.as_of_ms,
            "direction": sig.direction,
            "status": sig.status,
            "quality": round(float(sig.quality or 0.0), 1),
            "tier": sig.tier,
            "risk_score": sig.risk_score,
            "rr": round(float(sig.rr or 0.0), 2),
            "data_completeness": round(float(sig.confidence or 0.0), 3),
            "bot_confidence": self.confidence.to_dict(),
            "alert": self.alert.to_dict(),
            "degraded": list(self.degraded),
            "availability": self.availability,
        }


class _RecordingDataService(FuturesDataService):
    """``FuturesDataService``, который запоминает bundle, отданный движку.

    Список деградаций живёт в ``DataBundle`` и дополняется уже внутри
    ``engine.analyze`` («stale klines», пропуски свечей) — в сигнале его нет.
    Держим ссылку на тот же объект, чтобы отчёт показывал ровно то, что видел
    движок, без повторного запроса данных.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.last_bundle: Any = None

    async def build_bundle(self, symbol: str, deep: bool = True) -> Any:
        bundle = await super().build_bundle(symbol, deep=deep)
        self.last_bundle = bundle
        return bundle


async def replay_once(
    snapshot: dict[str, Any],
    cfg: SignalConfig | None = None,
    mode: str = "beginner",
    as_of_ms: int | None = None,
) -> ReplayResult:
    """Один прогон снапшота через прод-путь (без сети)."""
    cfg = cfg or SignalConfig()
    as_of = int(as_of_ms or snapshot.get("captured_at_ms") or 0)
    if not as_of:
        raise ValueError("в снапшоте нет captured_at_ms — не на что зафиксировать часы")
    snap = trim_snapshot(snapshot, as_of)
    source = SnapshotSource(snap)
    data = _RecordingDataService(source=source, cfg=cfg)
    engine = FuturesSignalEngine(data, cfg)
    try:
        with frozen_clock(as_of):
            await data.probe()
            signal = await engine.analyze(source.symbol, refresh=True)
            report = assess_confidence(signal, cfg)
            alert = evaluate_alert(signal, cfg)
            card = render_signal(signal, mode)
            degraded = list(getattr(data.last_bundle, "degraded", []) or [])
    finally:
        await data.close()
    return ReplayResult(
        symbol=source.symbol,
        as_of_ms=as_of,
        signal=signal,
        confidence=report,
        alert=alert,
        card=card,
        availability=snapshot_availability(snapshot),
        degraded=degraded,
    )


def walk_points(snapshot: dict[str, Any], steps: int, step: int, entry_tf: str) -> list[int]:
    """Моменты для прохода по истории: закрытия свечей входного ТФ.

    Точки позже момента съёма отбрасываются: тикер и стакан сняты один раз, и
    «будущий» момент сделал бы их устаревшими. Так проход остаётся строго
    внутри снятых данных.
    """
    rows = (snapshot.get("klines") or {}).get(entry_tf) or []
    tf_ms = TF_MS.get(entry_tf, 900_000)
    captured = int(snapshot.get("captured_at_ms") or 0)
    closes = [int(r[0]) + tf_ms for r in rows]
    if captured:
        closes = [c for c in closes if c <= captured]
    out: list[int] = []
    idx = len(closes) - 1
    while len(out) < steps and idx >= 0:
        out.append(closes[idx])
        idx -= max(1, step)
    return list(reversed(out))


# ════════════════════════════════════════════════════════════════
#  КЛИЕНТСКАЯ ЧАСТЬ (печатает человекочитаемый отчёт)
# ════════════════════════════════════════════════════════════════
def _utc(ts_ms: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts_ms / 1000.0))


def _print_availability(rows: list[dict[str, Any]]) -> None:
    print("\n📦 ЧТО В СНАПШОТЕ РЕАЛЬНОЕ")
    for row in rows:
        mark = "✅" if row["real"] else "➖"
        print(f"  {mark} {row['field']:<32} {row['detail']}")


def _print_summary(res: ReplayResult, cfg: SignalConfig) -> None:
    sig = res.signal
    print("\n🔎 РЕЗУЛЬТАТ ДВИЖКА НА РЕАЛЬНЫХ ДАННЫХ")
    print(f"  Символ:            {res.symbol}")
    print(f"  Момент съёма:      {_utc(res.as_of_ms)}")
    print(f"  Направление:       {sig.direction} ({sig.status})")
    print(f"  Оценка сетапа:     {float(sig.quality or 0):.0f}/100 — тир {sig.tier}")
    print(f"  Уверенность бота:  {res.confidence.percent:.1f}% — {res.confidence.label}")
    print(f"  Полнота данных:    {float(sig.confidence or 0) * 100:.0f}%")
    print(f"  Риск:              {sig.risk_score}/10 · R:R {float(sig.rr or 0):.2f}")
    print(f"  Режим рынка:       {sig.regime}")
    print("\n  Из чего сложилась уверенность:")
    for part in res.confidence.parts:
        print(f"    · {part.title:<34} {part.score:>5.1f} × {part.weight:.2f}   {part.note}")
    if sig.no_trade_reasons:
        print("\n  Почему нет входа:")
        for reason in sig.no_trade_reasons[:6]:
            print(f"    — {reason}")
    print("\n🔔 АВТО-СИГНАЛ")
    if res.alert.ok:
        print(f"  ✅ Отправил бы пользователю (уверенность {res.alert.percent:.1f}%, порог {cfg.ALERT_MIN_BOT_CONFIDENCE:.0f}%)")
    else:
        print("  ⛔ Промолчал бы — сетап не прошёл пороги авто-сигнала:")
        for reason in res.alert.reasons:
            print(f"    — {reason}")
    if res.degraded:
        print("\n⚠️ Деградации данных (честно, без подмены):")
        for item in res.degraded:
            print(f"    — {item}")


def run_replay(
    path: str,
    mode: str = "beginner",
    walk: int = 0,
    step: int = 1,
    as_json: bool = False,
    cfg: SignalConfig | None = None,
) -> int:
    """``python -m v3 replay <файл>`` — прогон реальных данных без сети."""
    cfg = cfg or SignalConfig()
    try:
        snapshot = load_snapshot(path)
    except Exception as exc:  # noqa: BLE001 — CLI должен объяснить, а не упасть трейсом
        print(f"❌ Не удалось прочитать снапшот {path}: {exc}")
        return 2
    symbol = snapshot.get("symbol") or "?"
    src = snapshot.get("source") or "?"
    print("=" * 68)
    print(f"РЕПЛЕЙ НА РЕАЛЬНЫХ ДАННЫХ · {symbol} · источник {src}")
    print(f"Файл: {path}")
    print(f"Часы зафиксированы на {_utc(int(snapshot.get('captured_at_ms') or 0))} (момент съёма)")
    print("=" * 68)
    _print_availability(snapshot_availability(snapshot))

    res = asyncio.run(replay_once(snapshot, cfg=cfg, mode=mode))
    if walk > 0:
        entry_tf = cfg.ENTRY_TF
        points = walk_points(snapshot, walk, step, entry_tf)
        print(f"\n🚶 ПРОХОД ПО ИСТОРИИ ({entry_tf}, {len(points)} точек, часы = момент решения)")
        print(f"  {'момент (UTC)':<21}{'напр.':<7}{'кач.':<6}{'увер.':<8}{'R:R':<6}сигнал")
        for as_of in points:
            item = asyncio.run(replay_once(snapshot, cfg=cfg, mode="pro", as_of_ms=as_of))
            sig = item.signal
            print(
                f"  {_utc(as_of):<21}{sig.direction:<7}{float(sig.quality or 0):<6.0f}"
                f"{item.confidence.percent:<8.1f}{float(sig.rr or 0):<6.2f}"
                f"{'🔔 ДА' if item.alert.ok else '—'}"
            )

    if as_json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0
    _print_summary(res, cfg)
    print("\n" + "─" * 68)
    print(f"КАРТОЧКА ДЛЯ ПОЛЬЗОВАТЕЛЯ (режим {mode})")
    print("─" * 68)
    print(res.card)
    print("\n" + "=" * 68)
    print("Источник данных: снятый снапшот биржи (офлайн, без сети и без синтетики)")
    return 0


# ════════════════════════════════════════════════════════════════
#  БЭКТЕСТ НА РЕАЛЬНЫХ СВЕЧАХ (без сети)
# ════════════════════════════════════════════════════════════════
def load_candles_series(path: str | Path) -> dict[str, Any]:
    """Серия РЕАЛЬНЫХ свечей одного таймфрейма → история для ``run_backtest``.

    Формат ``okx_candles_v1`` — дословные строки биржи (фикстура
    ``v3/tests/fixtures/okx_btcusdt_15m_300.json``). Свеча с ``confirm == "0"``
    ещё не закрыта, поэтому отбрасывается: бэктестер не должен видеть
    незаконченный бар, иначе это заглядывание в будущее.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if raw.get("kind") != "okx_candles_v1":
        got = raw.get("kind") or "не указан"
        raise ValueError(f"нужен формат okx_candles_v1 (серия свечей), в файле {got!r}")
    rows = raw.get("rows") or []
    closed_rows = [r for r in rows if len(r) > 8 and str(r[8]) == "1"]
    parsed = parse_okx_candles(closed_rows)
    df = pd.DataFrame(parsed, columns=["ts", "open", "high", "low", "close", "volume"])
    return {
        "symbol": _okx_symbol(raw),
        "tf": str(raw.get("bar") or "15m"),
        "df": df,
        "rows": len(rows),
        "closed": len(parsed),
        "forming_dropped": len(rows) - len(closed_rows),
        "source": raw.get("source") or "okx",
    }


def _bot_confidence(trade: Any) -> float:
    """Уверенность бота (%) из разбора, приложенного к сигналу.

    ``signal.confidence`` — это ДРУГАЯ величина (полнота реальных данных 0..1),
    поэтому в отчёте показываем именно сводный процент из ``features``.
    """
    try:
        report = (trade.signal or {}).get("features", {}).get("bot_confidence") or {}
        return float(report.get("percent") or 0.0)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _print_live_gates(trades: list[Any], cfg: SignalConfig) -> None:
    """Сравнить сетапы бэктеста с порогами, по которым их судил бы прод.

    Бэктестер собирает бандл сам и в нём честно нет финансирования, стакана и
    глобального контекста → ``data_confidence`` там всегда ≈0.30 (артефакт
    синтетического бандла, а не рынка). Сравниваем поэтому только те пороги,
    которые зависят от рынка: качество, R:R, риск и число целей.
    """
    def _num(sig: dict[str, Any], key: str) -> float:
        try:
            return float(sig.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    pass_publish = 0
    pass_alert = 0
    for trade in trades:
        sig = trade.signal or {}
        score, rr = _num(sig, "score"), _num(sig, "rr")
        risk = _num(sig, "risk_score")
        targets = len(sig.get("targets") or [])
        bot_conf = _bot_confidence(trade)
        if (
            score >= cfg.QUALITY_MIN
            and rr >= cfg.MIN_RISK_REWARD
            and risk <= cfg.MAX_RISK_SCORE_TO_ENTER
            and targets >= 2
        ):
            pass_publish += 1
        if (
            score >= cfg.ALERT_MIN_QUALITY
            and bot_conf >= cfg.ALERT_MIN_BOT_CONFIDENCE
            and rr >= cfg.ALERT_MIN_RR
            and risk <= cfg.ALERT_MAX_RISK_SCORE
        ):
            pass_alert += 1

    total = len(trades)
    print("\nПроверка сетапов живыми порогами (рыночные критерии):")
    print(
        f"  публикация (качество ≥{cfg.QUALITY_MIN:.0f}, R:R ≥{cfg.MIN_RISK_REWARD:.1f}, "
        f"риск ≤{cfg.MAX_RISK_SCORE_TO_ENTER}, целей ≥2): {pass_publish} из {total}"
    )
    print(
        f"  авто-сигнал (качество ≥{cfg.ALERT_MIN_QUALITY:.0f}, уверенность ≥{cfg.ALERT_MIN_BOT_CONFIDENCE:.0f}%, "
        f"R:R ≥{cfg.ALERT_MIN_RR:.1f}, риск ≤{cfg.ALERT_MAX_RISK_SCORE}): {pass_alert} из {total}"
    )
    print(
        "  ℹ️ Полнота данных в бэктесте всегда ≈30% (нет funding/стакана/глобального контекста) — "
        f"порог публикации {cfg.CONFIDENCE_MIN:.2f} к ней не применяется."
    )


def run_replay_backtest(
    path: str,
    warmup: int = 120,
    as_json: bool = False,
    cfg: SignalConfig | None = None,
) -> int:
    """``python -m v3 replay <файл> --backtest`` — бэктест на реальных свечах.

    Это тот же ``v3.backtest.run_backtest``, что и в команде ``backtest``
    (та же симуляция: комиссия, слиппедж, стоп проверяется пессимистично,
    частичные выходы), но вместо запросов к бирже история берётся из
    дословно снятого файла. Сети нет — результат воспроизводим.
    """
    from v3.backtest import TF_MS, _resample, run_backtest

    cfg = cfg or SignalConfig()
    try:
        series = load_candles_series(path)
    except Exception as exc:  # noqa: BLE001 — CLI должен объяснить, а не упасть трейсом
        print(f"❌ Не удалось прочитать серию свечей {path}: {exc}")
        return 2

    symbol, tf, df = series["symbol"], series["tf"], series["df"]
    if df.empty:
        print(f"❌ В {path} нет закрытых свечей — бэктест невозможен")
        return 2
    medium_tf = {"1m": "15m", "5m": "15m", "15m": "1h", "30m": "2h", "1h": "4h", "4h": "1d"}.get(tf, "1h")
    macro_tf = {"1m": "4h", "5m": "4h", "15m": "4h", "30m": "4h", "1h": "1d", "4h": "1d"}.get(tf, "4h")

    # Движку нужен сервис данных по контракту, но run_backtest его не дёргает:
    # бандлы он собирает сам из истории. Источник — тот же SnapshotSource.
    snapshot = {
        "kind": "snapshot_v1",
        "symbol": symbol,
        "source": series["source"],
        "captured_at_ms": int(df["ts"].iloc[-1]) + TF_MS.get(tf, 900_000),
        "klines": {tf: df[["ts", "open", "high", "low", "close", "volume"]].values.tolist()},
    }
    data = FuturesDataService(source=SnapshotSource(snapshot), cfg=cfg)
    engine = FuturesSignalEngine(data, cfg)

    # Честно показываем, сколько старших ТФ реально доступно: бэктестер
    # пересобирает их из этой же истории и отбрасывает ТФ короче 40 баров.
    def _bars_of(target: str) -> int:
        frame = _resample(df, target)
        return 0 if frame is None else int(len(frame))

    med_bars = _bars_of(medium_tf)
    macro_bars = _bars_of(macro_tf)
    avail = [f"{tf}: {len(df)}"]
    avail.append(f"{medium_tf}: {med_bars}" + ("" if med_bars >= 40 else " (мало, <40)"))
    avail.append(f"{macro_tf}: {macro_bars}" + ("" if macro_bars >= 40 else " (мало, <40)"))

    res = run_backtest(
        engine,
        symbol,
        df,
        entry_tf=tf,
        medium_tf=medium_tf,
        macro_tf=macro_tf,
        warmup=warmup,
        cfg=cfg,
    )

    if as_json:
        print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print("=" * 68)
    print(f"БЭКТЕСТ НА РЕАЛЬНЫХ СВЕЧАХ · {symbol} · источник {series['source']}")
    print(f"Файл: {path}")
    print(f"Свечей в файле: {series['rows']} → закрытых использовано: {series['closed']}"
          f" (недоформированных отброшено: {series['forming_dropped']})")
    print(f"Период: {_utc(int(df['ts'].iloc[0]))} → {_utc(int(df['ts'].iloc[-1]))}")
    print(f"Таймфреймы движка: {' · '.join(avail)}")
    print("=" * 68)
    if res.metrics.get("error"):
        print(f"❌ {res.metrics['error']} — нужно больше истории (минимум {warmup + 30} свечей)")
        return 1
    print(f"Баров: {res.bars} · точек решения: {res.signals} · исполняемых сетапов: {len(res.trades)}")
    print("Метрики:")
    for key, value in res.metrics.items():
        print(f"  {key}: {value}")
    if res.trades:
        print("\nСделки:")
        for t in res.trades:
            print(
                f"  {_utc(t.entry_ts)} {t.direction:<5} вход {t.entry_price:.1f} → выход {t.exit_price:.1f}"
                f" · {t.exit_reason} · {t.r_multiple:+.2f}R ({t.pnl_pct:+.2f}%) · кач. {t.score:.0f}"
                f" · увер. {_bot_confidence(t):.1f}% · данные {t.confidence * 100:.0f}% · баров {t.bars_held}"
            )
        _print_live_gates(res.trades, cfg)
    else:
        print("\nСделок нет: ни одна точка не прошла фильтры качества/подтверждения.")
    print("\n" + "─" * 68)
    print("Тест на прошлых данных не гарантирует будущих результатов.")
    print("Источник данных: дословно снятые свечи биржи (офлайн, без сети и без синтетики).")
    return 0


async def record_symbol(symbol: str, out: str, cfg: SignalConfig | None = None) -> int:
    """``python -m v3 record BTCUSDT`` — снять реальный снапшот с биржи.

    Использует прод-сервис ``FuturesDataService``: те же запросы и та же
    валидация, что в live, поэтому снапшот можно прогнать потом без сети.
    """
    cfg = cfg or SignalConfig()
    symbol = symbol.upper()
    data = FuturesDataService(cfg=cfg)
    try:
        try:
            mode = await data.probe()
        except Exception as exc:  # noqa: BLE001 — без сети объясняем, а не роняем трейс
            print(f"⚠️ Нет реальных данных — снапшот не снять: {exc}")
            print("Снятие снапшота требует доступ к бирже. Готовый реальный снапшот можно")
            print("прогнать так: python -m v3 replay v3/tests/fixtures/okx_btcusdt_swap_capture.json")
            return 1
        klines: dict[str, list[list[float]]] = {}
        for tf in cfg.timeframes:
            df = await data.klines(symbol, tf, cfg.ANALYSIS_BARS)
            klines[tf] = [
                [int(r.ts), float(r.open), float(r.high), float(r.low), float(r.close), float(r.volume)]
                for r in df.itertuples()
            ]
        tickers = await data.tickers([symbol])
        t = tickers.get(symbol)
        if t is None:
            print(f"⚠️ Нет реального тикера {symbol} — снапшот не сохранён")
            return 1
        book = await data.orderbook(symbol)
        snapshot = {
            "kind": "snapshot_v1",
            "symbol": symbol,
            "source": mode,
            "captured_at_ms": int(time.time() * 1000),
            "klines": klines,
            "ticker": {
                "last": float(t.last),
                "bid": float(t.bid or 0.0),
                "ask": float(t.ask or 0.0),
                "open_24h": float(t.open_24h or 0.0),
                "high_24h": float(t.high_24h or 0.0),
                "low_24h": float(t.low_24h or 0.0),
                "volume_24h": float(t.volume_24h or 0.0),
                "turnover_24h": float(t.turnover_24h or 0.0),
                "funding_rate": t.funding_rate,
                "next_funding_ms": t.next_funding_ms,
                "open_interest_usd": t.open_interest_usd,
                "ts_ms": int(t.ts_ms or 0),
            },
            "funding_history": [float(r) for r in await data.funding_history(symbol)],
            "orderbook": {
                "bids": [[float(p), float(q)] for p, q in ((book or {}).get("bids") or [])],
                "asks": [[float(p), float(q)] for p, q in ((book or {}).get("asks") or [])],
                "ts_ms": int((book or {}).get("ts_ms") or 0),
            },
            "provenance": {"kind": "record_v1", "source": mode, "timeframes": cfg.timeframes},
        }
    finally:
        await data.close()
    saved = save_snapshot(snapshot, out)
    bars = {tf: len(rows) for tf, rows in klines.items()}
    print(f"✅ Снапшот {symbol} сохранён: {saved}")
    print(f"   источник {mode} · свечи: {bars}")
    print(f"   прогон: python -m v3 replay {saved}")
    return 0
