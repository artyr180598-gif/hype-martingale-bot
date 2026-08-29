"""
Полный спектральный анализ монеты.

Идея взята из OctoBot (взвешенные «эвалюаторы») и Freqtrade/FreqAI
(мультитаймфреймовые признаки): вместо одного вердикта строится спектр
показателей по всем измерениям, а итог — взвешенная сумма.

Измерения спектра:
  1. Таймфреймы  5m → 15m → 1h → 4h → 1d (совпадение трендов)
  2. Тренд       EMA-стек, ADX/DI, SuperTrend, структура свингов
  3. Моментум    RSI, MACD, Stochastic, ROC, Williams %R
  4. Волатильность ATR-процентиль, Bollinger/Keltner squeeze, RV
  5. Объём/поток  OBV, CVD, MFI, z-score объёма
  6. Стакан      глубина, перекос, стены, спред (ликвидность)
  7. Деривативы  фандинг и его тренд, ликвидации
  8. Контекст    тренд BTC, Fear & Greed, доминация
  9. Новости     сентимент заголовков по монете

Итог: направление, сила (-1..+1), confluence 0..100, уверенность и
текстовый спектр для вывода в Telegram.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.core.logging import get_logger
from src.core.timeutil import now_ms, tf_label
from src.data.indicators import compute_all, last
from src.data.models import NewsItem

logger = get_logger("analysis.spectrum")

SPECTRUM_TIMEFRAMES = ("5m", "15m", "1h", "4h", "1d")
SPECTRUM_LIMITS = {"5m": 200, "15m": 300, "1h": 240, "4h": 200, "1d": 200}

# Веса групп в итоговом сигнале (сумма = 1.0)
GROUP_WEIGHTS = {
    "timeframes": 0.24,
    "trend": 0.20,
    "momentum": 0.14,
    "volatility": 0.08,
    "volume": 0.12,
    "orderbook": 0.07,
    "derivatives": 0.09,
    "context": 0.06,
}

GROUP_RU = {
    "timeframes": "Таймфреймы",
    "trend": "Тренд",
    "momentum": "Моментум",
    "volatility": "Волатильность",
    "volume": "Объём и поток",
    "orderbook": "Стакан",
    "derivatives": "Деривативы",
    "context": "Контекст рынка",
}


@dataclass
class TfSnapshot:
    """Срез одного таймфрейма."""

    timeframe: str
    trend: str
    rsi: float
    macd_hist: float
    adx: float
    st_dir: int
    above_ema200: bool
    atr_pct: float
    score: float                      # -1..+1
    note: str


@dataclass
class FactorReading:
    """Отдельный фактор спектра."""

    group: str
    name: str
    value: float                      # -1..+1
    detail: str


@dataclass
class SpectrumReport:
    symbol: str
    ts_ms: int
    price: float
    timeframes: list[TfSnapshot] = field(default_factory=list)
    factors: list[FactorReading] = field(default_factory=list)
    group_scores: dict[str, float] = field(default_factory=dict)
    total_score: float = 0.0          # -1..+1
    confluence: float = 0.0           # 0..100
    direction: str = "WAIT"
    confidence: float = 0.0
    tf_alignment: float = 0.0
    orderbook: dict = field(default_factory=dict)
    derivatives: dict = field(default_factory=dict)
    market_context: dict = field(default_factory=dict)
    news_sentiment: float = 0.0
    news_count: int = 0
    summary: str = ""
    is_demo: bool = False

    def bars(self, width: int = 8) -> list[str]:
        """Текстовый спектр: строка на каждую группу."""
        out: list[str] = []
        for group, score in self.group_scores.items():
            filled = int(round(abs(score) * width / 2 * 2))
            filled = max(0, min(width, filled))
            icon = "🟩" if score > 0.15 else ("🟥" if score < -0.15 else "⬜")
            bar = "▰" * filled + "▱" * (width - filled)
            label = GROUP_RU.get(group, group).ljust(15)
            out.append(f"{icon} <code>{label}{bar} {score:+.2f}</code>")
        return out

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "ts_ms": self.ts_ms,
            "price": self.price,
            "direction": self.direction,
            "confidence": round(self.confidence, 2),
            "total_score": round(self.total_score, 3),
            "confluence": round(self.confluence, 1),
            "tf_alignment": round(self.tf_alignment, 2),
            "group_scores": {k: round(v, 3) for k, v in self.group_scores.items()},
            "timeframes": [
                {
                    "timeframe": t.timeframe,
                    "trend": t.trend,
                    "rsi": round(t.rsi, 1),
                    "adx": round(t.adx, 1),
                    "st_dir": t.st_dir,
                    "atr_pct": round(t.atr_pct, 3),
                    "score": round(t.score, 2),
                    "note": t.note,
                }
                for t in self.timeframes
            ],
            "factors": [
                {"group": f.group, "name": f.name, "value": round(f.value, 2), "detail": f.detail}
                for f in self.factors
            ],
            "orderbook": self.orderbook,
            "derivatives": self.derivatives,
            "market_context": self.market_context,
            "news_sentiment": round(self.news_sentiment, 2),
            "news_count": self.news_count,
            "summary": self.summary,
            "is_demo": self.is_demo,
        }


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    if not np.isfinite(x):
        return 0.0
    return float(np.clip(x, lo, hi))


def _tf_snapshot(df: pd.DataFrame, timeframe: str) -> TfSnapshot:
    """Оценка одного таймфрейма: -1 (медвежий) .. +1 (бычий)."""
    c = float(df["close"].iloc[-1])
    ema20, ema50 = last(df["ema_20"]), last(df["ema_50"])
    ema200 = last(df["ema_200"]) or ema50
    r = float(last(df["rsi_14"], 50.0))
    hist = float(last(df["macd_hist"]))
    adx_v = float(last(df["adx"]))
    st_dir = int(last(df["st_dir"], 1.0) or 1)
    plus_di, minus_di = last(df["plus_di"]), last(df["minus_di"])
    atr_pct = float(last(df["atr_pct"]))

    parts: list[float] = []
    parts.append(_clip((ema20 - ema50) / max(ema50, 1e-9) * 120))          # наклон стека EMA
    parts.append(_clip((c - ema200) / max(ema200, 1e-9) * 40))             # позиция к EMA200
    parts.append(_clip((r - 50.0) / 30.0))                                 # RSI-смещение
    parts.append(_clip((plus_di - minus_di) / 40.0))                       # DI-баланс
    parts.append(float(st_dir))                                            # SuperTrend
    atr_scale = max(atr_pct, 1e-6)
    parts.append(_clip(hist / max(c, 1e-9) / atr_scale * 8))               # MACD в ATR
    score = _clip(float(np.mean(parts)))

    if score > 0.35:
        trend = "up"
    elif score < -0.35:
        trend = "down"
    else:
        trend = "range"
    trend_ru = {"up": "восходящий", "down": "нисходящий", "range": "боковой"}[trend]
    note = f"{trend_ru}, RSI {r:.0f}, ADX {adx_v:.0f}, ST {'▲' if st_dir > 0 else '▼'}"
    return TfSnapshot(
        timeframe=timeframe,
        trend=trend,
        rsi=r,
        macd_hist=hist,
        adx=adx_v,
        st_dir=st_dir,
        above_ema200=c > ema200,
        atr_pct=atr_pct,
        score=score,
        note=note,
    )


class SpectrumAnalyzer:
    """Собирает полный спектр по монете."""

    def __init__(self, source, settings):
        self.source = source
        self.settings = settings

    async def analyze(self, symbol: str, news: list[NewsItem] | None = None) -> SpectrumReport:
        symbol = symbol.upper()
        frames: dict[str, pd.DataFrame] = {}
        for tf in SPECTRUM_TIMEFRAMES:
            try:
                df = await self.source.get_klines(symbol, tf, SPECTRUM_LIMITS[tf])
            except Exception as e:  # noqa: BLE001
                logger.debug("Спектр %s: нет %s (%s)", symbol, tf, e)
                continue
            if len(df) < 30:
                continue
            frames[tf] = compute_all(df)
        if not frames:
            raise ValueError(f"{symbol}: нет данных ни на одном таймфрейме")

        main_tf = "15m" if "15m" in frames else sorted(frames)[0]
        fe = frames[main_tf]
        price = float(fe["close"].iloc[-1])

        snaps = [_tf_snapshot(frames[tf], tf) for tf in SPECTRUM_TIMEFRAMES if tf in frames]

        factors: list[FactorReading] = []
        factors += self._tf_factors(snaps)
        factors += self._trend_factors(fe)
        factors += self._momentum_factors(fe)
        factors += self._volatility_factors(fe)
        factors += self._volume_factors(fe)

        ob = await self._orderbook(symbol)
        if ob:
            factors += self._orderbook_factors(ob)
        deriv = await self._derivatives(symbol)
        if deriv["factors"]:
            factors += deriv["factors"]

        ctx = await self._market_context(symbol, fe)
        if ctx["factors"]:
            factors += ctx["factors"]

        news_sent, news_n = self._news_sentiment(symbol, news or [])
        if news_n:
            factors.append(
                FactorReading("context", "Сентимент новостей", _clip(news_sent), f"{news_n} заголовков")
            )

        group_scores = self._aggregate(factors, snaps)
        total = float(
            sum(group_scores.get(g, 0.0) * w for g, w in GROUP_WEIGHTS.items())
            / sum(GROUP_WEIGHTS.values())
        )
        total = _clip(total)

        direction, confidence = self._direction(total, snaps, group_scores)
        confluence = (abs(total) * 60.0) + (40.0 * self._alignment(snaps, total))
        confluence = float(np.clip(confluence, 0.0, 100.0))

        summary = self._summary(symbol, direction, total, confluence, snaps, group_scores)

        return SpectrumReport(
            symbol=symbol,
            ts_ms=now_ms(),
            price=price,
            timeframes=snaps,
            factors=factors,
            group_scores=group_scores,
            total_score=total,
            confluence=confluence,
            direction=direction,
            confidence=confidence,
            tf_alignment=self._alignment(snaps, total),
            orderbook=ob or {},
            derivatives={k: v for k, v in deriv.items() if k != "factors"},
            market_context={k: v for k, v in ctx.items() if k != "factors"},
            news_sentiment=news_sent,
            news_count=news_n,
            summary=summary,
            is_demo=bool(getattr(self.source, "is_demo", False)),
        )

    # ── факторы по группам ──
    @staticmethod
    def _tf_factors(snaps: list[TfSnapshot]) -> list[FactorReading]:
        out: list[FactorReading] = []
        for s in snaps:
            out.append(
                FactorReading("timeframes", f"Тренд {tf_label(s.timeframe)}", s.score, s.note)
            )
        return out

    @staticmethod
    def _trend_factors(fe: pd.DataFrame) -> list[FactorReading]:
        c = float(fe["close"].iloc[-1])
        e9, e20, e50 = last(fe["ema_9"]), last(fe["ema_20"]), last(fe["ema_50"])
        adx_v, plus_di, minus_di = last(fe["adx"]), last(fe["plus_di"]), last(fe["minus_di"])
        vwap = last(fe["vwap"]) or c
        st_dir = int(last(fe["st_dir"], 1.0) or 1)
        stack = 0.0
        if e9 > e20 > e50:
            stack = 1.0
        elif e9 < e20 < e50:
            stack = -1.0
        else:
            stack = _clip((e9 - e50) / max(e50, 1e-9) * 60)
        return [
            FactorReading("trend", "Стек EMA (9/20/50)", stack,
                          "бычий" if stack > 0.3 else ("медвежий" if stack < -0.3 else "перемешан")),
            FactorReading("trend", "ADX / DI", _clip((plus_di - minus_di) / 40.0),
                          f"ADX {adx_v:.0f}, +DI {plus_di:.0f} / -DI {minus_di:.0f}"),
            FactorReading("trend", "SuperTrend", float(st_dir), "выше линии" if st_dir > 0 else "ниже линии"),
            FactorReading("trend", "Цена к VWAP", _clip((c - vwap) / max(vwap, 1e-9) * 60),
                          f"VWAP {vwap:.8g}"),
        ]

    @staticmethod
    def _momentum_factors(fe: pd.DataFrame) -> list[FactorReading]:
        c = float(fe["close"].iloc[-1])
        r = last(fe["rsi_14"], 50.0)
        hist = last(fe["macd_hist"])
        k, d = last(fe["stoch_k"], 50.0), last(fe["stoch_d"], 50.0)
        roc20 = last(fe["roc_20"])
        atr_pct = max(last(fe["atr_pct"]), 1e-6)
        # RSI: экстремумы трактуем контрариански
        rsi_val = _clip((r - 50.0) / 25.0)
        if r > 75:
            rsi_val = -0.6
        elif r < 25:
            rsi_val = 0.6
        return [
            FactorReading("momentum", "RSI 14", rsi_val, f"{r:.0f}"),
            FactorReading("momentum", "MACD-гистограмма", _clip(hist / max(c, 1e-9) / atr_pct * 8),
                          f"{hist:.6g}"),
            FactorReading("momentum", "Stochastic", _clip((k - d) / 25.0 + (k - 50.0) / 90.0),
                          f"K {k:.0f} / D {d:.0f}"),
            FactorReading("momentum", "ROC 20 баров", _clip(roc20 / 12.0), f"{roc20:+.2f}%"),
        ]

    @staticmethod
    def _volatility_factors(fe: pd.DataFrame) -> list[FactorReading]:
        pctl = last(fe["atr_pctl"], 0.5)
        squeeze = bool(fe["squeeze"].iloc[-1]) if "squeeze" in fe.columns else False
        # Волатильность не имеет направления: даём знак по сжатию (готовность к импульсу)
        if squeeze:
            val, note = 0.5, "squeeze: BB внутри KC — ждём выход"
        elif pctl >= 0.9:
            val, note = -0.5, "волатильность на максимуме — вход дорогой"
        elif pctl <= 0.15:
            val, note = 0.2, "рынок спокоен, импульса пока нет"
        else:
            val, note = 0.0, f"ATR-процентиль {pctl:.0%}"
        return [
            FactorReading("volatility", "ATR-процентиль", _clip(val), note),
            FactorReading("volatility", "Bollinger %B", _clip((last(fe['bb_pctb'], 0.5) - 0.5) * 2 * -1),
                          f"позиция в канале {last(fe['bb_pctb'], 0.5):.2f}"),
        ]

    @staticmethod
    def _volume_factors(fe: pd.DataFrame) -> list[FactorReading]:
        return [
            FactorReading("volume", "OBV-тренд", _clip(last(fe["obv_trend"]) * 3),
                          f"наклон {last(fe['obv_trend']):+.3f}"),
            FactorReading("volume", "CVD (дельта)", _clip(last(fe["cvd_trend"]) * 3),
                          f"наклон {last(fe['cvd_trend']):+.3f}"),
            FactorReading("volume", "MFI 14", _clip((last(fe['mfi_14'], 50.0) - 50.0) / 30.0),
                          f"{last(fe['mfi_14'], 50.0):.0f}"),
            FactorReading("volume", "Всплеск объёма", _clip(last(fe["vol_z"]) / 2.5),
                          f"z={last(fe['vol_z']):+.2f}"),
        ]

    @staticmethod
    def _orderbook_factors(ob: dict) -> list[FactorReading]:
        imb = ob.get("imbalance", 0.0)
        return [
            FactorReading("orderbook", "Перекос стакана", _clip(imb * 1.6),
                          f"{imb:+.2f} (bid {ob.get('bid_usd', 0) / 1e6:.2f}M / ask {ob.get('ask_usd', 0) / 1e6:.2f}M)"),
            FactorReading("orderbook", "Спред", _clip(0.3 - ob.get("spread_pct", 0.0)),
                          f"{ob.get('spread_pct', 0.0):.4f}%"),
        ]

    # ── асинхронные блоки ──
    async def _orderbook(self, symbol: str) -> dict:
        try:
            ob = await self.source.get_orderbook(symbol, 50)
        except Exception as e:  # noqa: BLE001
            logger.debug("Стакан %s недоступен: %s", symbol, e)
            return {}
        bid_usd, ask_usd = ob.depth(1.0)
        walls = ob.walls(2)
        return {
            "imbalance": round(ob.imbalance, 3),
            "spread_pct": round(ob.spread_pct, 4),
            "bid_usd": bid_usd,
            "ask_usd": ask_usd,
            "mid": ob.mid,
            "walls": walls,
        }

    async def _derivatives(self, symbol: str) -> dict:
        out: dict = {"factors": []}
        rate = None
        try:
            tickers = await self.source.get_tickers([symbol])
            if tickers:
                rate = tickers[0].funding_rate
                out["open_interest_usd"] = tickers[0].open_interest_usd
        except Exception:  # noqa: BLE001
            pass
        try:
            hist = await self.source.get_funding(symbol, 12)
            rates = [h.rate for h in hist]
            if rates:
                rate = rates[-1]
                out["funding_avg"] = float(np.mean(rates))
                out["funding_trend"] = float(rates[-1] - rates[0])
        except Exception:  # noqa: BLE001
            rates = []
        if rate is not None:
            out["funding_rate"] = rate
            # перегретый лонг → контрарианский минус, перегретый шорт → плюс
            if rate > 0.0015:
                val, note = -0.7, "толпа в лонгах — риск сквиза вниз"
            elif rate < -0.001:
                val, note = 0.7, "толпа в шортах — топливо для роста"
            else:
                val, note = _clip(rate / 0.0015 * 0.35), "фандинг нейтральный"
            out["factors"].append(FactorReading("derivatives", "Ставка финансирования", val, note))
        try:
            liqs = [x for x in await self.source.get_recent_liquidations(200) if x.symbol == symbol]
            buy = sum(x.size for x in liqs if x.side == "Buy")
            sell = sum(x.size for x in liqs if x.side == "Sell")
            out["liquidations_buy_usd"] = buy
            out["liquidations_sell_usd"] = sell
            if buy + sell > 0:
                # ликвидации лонгов = давление вниз (и наоборот)
                val = _clip((buy - sell) / max(buy + sell, 1.0) * -1.0)
                out["factors"].append(
                    FactorReading("derivatives", "Ликвидации", val,
                                  f"лонгов ${(buy / 1e6):.2f}M / шортов ${(sell / 1e6):.2f}M")
                )
        except Exception:  # noqa: BLE001
            pass
        return out

    async def _market_context(self, symbol: str, fe: pd.DataFrame) -> dict:
        out: dict = {"factors": []}
        # тренд BTC как рыночный бета-фильтр
        if symbol != "BTCUSDT":
            try:
                btc = compute_all(await self.source.get_klines("BTCUSDT", "4h", 200))
                btc_score = _tf_snapshot(btc, "4h").score
                out["btc_4h_score"] = round(btc_score, 2)
                out["factors"].append(
                    FactorReading("context", "Тренд BTC (4h)", btc_score * 0.8, "бета-фильтр рынка")
                )
            except Exception as e:  # noqa: BLE001
                logger.debug("Контекст BTC недоступен: %s", e)
        try:
            fg = await self.source.get_fear_greed()
            out["fear_greed"] = fg.value
            out["fear_greed_label"] = fg.classification
            # контрарианская логика: крайний страх — плюс к лонгу
            val = _clip((50.0 - fg.value) / 45.0)
            out["factors"].append(
                FactorReading("context", "Fear & Greed", val, f"{fg.value} ({fg.classification})")
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Fear&Greed недоступен: %s", e)
        return out

    @staticmethod
    def _news_sentiment(symbol: str, news: list[NewsItem]) -> tuple[float, int]:
        base = symbol.replace("USDT", "")
        relevant = [n for n in news if base in [s.upper() for s in n.symbols] or base in n.title.upper()]
        if not relevant:
            return 0.0, 0
        return float(np.mean([n.sentiment for n in relevant])), len(relevant)

    # ── агрегация ──
    @staticmethod
    def _aggregate(factors: list[FactorReading], snaps: list[TfSnapshot]) -> dict[str, float]:
        grouped: dict[str, list[float]] = {}
        for f in factors:
            grouped.setdefault(f.group, []).append(f.value)
        scores = {g: float(np.mean(v)) for g, v in grouped.items() if v}
        for g in GROUP_WEIGHTS:
            scores.setdefault(g, 0.0)
        return scores

    @staticmethod
    def _alignment(snaps: list[TfSnapshot], total: float) -> float:
        """Доля таймфреймов, согласных с итоговым направлением (0..1)."""
        if not snaps or abs(total) < 0.05:
            return 0.0
        sign = 1 if total > 0 else -1
        agreed = sum(1 for s in snaps if s.score * sign > 0.1)
        return agreed / len(snaps)

    def _direction(self, total: float, snaps: list[TfSnapshot], groups: dict) -> tuple[str, float]:
        alignment = self._alignment(snaps, total)
        if abs(total) < 0.12:
            return "WAIT", 0.3
        direction = "LONG" if total > 0 else "SHORT"
        confidence = float(np.clip(abs(total) * 0.75 + alignment * 0.25, 0.15, 0.93))
        # без согласия старших таймфреймов уверенность режем
        macro = [s for s in snaps if s.timeframe in ("4h", "1d")]
        if macro:
            macro_score = float(np.mean([s.score for s in macro]))
            if macro_score * (1 if direction == "LONG" else -1) < 0:
                confidence *= 0.72
        return direction, float(confidence)

    @staticmethod
    def _summary(
        symbol: str,
        direction: str,
        total: float,
        confluence: float,
        snaps: list[TfSnapshot],
        groups: dict,
    ) -> str:
        base = symbol.replace("USDT", "")
        arrow = {"LONG": "📈", "SHORT": "📉"}.get(direction, "⏸")
        strongest = max(groups.items(), key=lambda kv: abs(kv[1])) if groups else ("—", 0.0)
        weakest = min(groups.items(), key=lambda kv: abs(kv[1])) if groups else ("—", 0.0)
        parts = [
            f"{arrow} {base}: спектр {total:+.2f} → {direction}, confluence {confluence:.0f}/100",
            f"сильнее всего «{GROUP_RU.get(strongest[0], strongest[0])}» ({strongest[1]:+.2f})",
            f"слабее всего «{GROUP_RU.get(weakest[0], weakest[0])}» ({weakest[1]:+.2f})",
        ]
        ups = [s.timeframe for s in snaps if s.score > 0.1]
        downs = [s.timeframe for s in snaps if s.score < -0.1]
        if ups and not downs:
            parts.append("все таймфреймы вверх")
        elif downs and not ups:
            parts.append("все таймфреймы вниз")
        elif ups and downs:
            parts.append(f"конфликт таймфреймов: вверх {len(ups)}, вниз {len(downs)}")
        return " | ".join(parts)
