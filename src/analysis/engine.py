"""
Аналитический движок советника.

Для монеты собираются: свечи на 15m/1h/4h, индикаторы, волны Эллиотта,
режим волатильности, структура рынка, моментум, фандинг, ликвидации.
На выходе — AnalysisResult с готовым планом: зона входа, стоп, цели,
риск/прибыль, уверенность, рейтинг и текстовое обоснование.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.analysis.fib import compute_rr, fib_levels
from src.analysis.scoring import tier_from_score, verdict_from_score
from src.analysis.waves import (
    ElliottResult,
    MomentumState,
    StructureState,
    VolatilityState,
    elliott,
    market_structure,
    momentum,
    volatility_state,
    zigzag,
)
from src.config.settings import Settings
from src.core.errors import AnalysisError, NotEnoughData
from src.core.logging import get_logger
from src.core.timeutil import now_ms
from src.data.collector import MarketDataSource
from src.data.indicators import compute_all
from src.data.models import Liquidation, Ticker

logger = get_logger("analysis.engine")

ENTRY_TF = "15m"
MEDIUM_TF = "1h"
MACRO_TF = "4h"

MIN_BARS = 60


@dataclass
class EntryPlan:
    direction: str                      # LONG / SHORT / WAIT
    entry_zone: tuple[float, float]     # зона входа
    stop_loss: float
    targets: list[float]
    rr: float                           # риск/прибыль к основной цели
    leverage: int
    position_pct: float                 # % депозита по риск-правилу
    distance_pct: float                 # % до зоны входа от текущей цены
    invalidation: str                   # что отменит идею
    t1_distance_pct: float


@dataclass
class AnalysisResult:
    symbol: str
    ts_ms: int
    timeframe_main: str
    price: float
    price_24h_pct: float
    turnover_24h: float
    volume_24h: float
    market_cap: float | None
    direction: str                      # LONG / SHORT / NEUTRAL / WAIT
    confidence: float                   # 0..1
    score: float                        # 0..100
    tier: str
    verdict: str
    recommendation: str
    plan: EntryPlan | None
    summary: str
    reasons: list[str]
    risks: list[str]
    structure: StructureState
    volatility: VolatilityState
    momentum: MomentumState
    elliott: ElliottResult
    funding_rate: float | None
    funding_trend: str
    funding_history: list[float]
    liquidations_note: str
    support: float | None
    resistance: float | None
    fib: dict
    is_demo: bool

    def to_dict(self) -> dict:
        d = {
            "symbol": self.symbol,
            "ts_ms": self.ts_ms,
            "timeframe_main": self.timeframe_main,
            "price": self.price,
            "price_24h_pct": self.price_24h_pct,
            "turnover_24h": self.turnover_24h,
            "volume_24h": self.volume_24h,
            "market_cap": self.market_cap,
            "direction": self.direction,
            "confidence": round(self.confidence, 2),
            "score": self.score,
            "tier": self.tier,
            "verdict": self.verdict,
            "recommendation": self.recommendation,
            "summary": self.summary,
            "reasons": self.reasons,
            "risks": self.risks,
            "volatility": {
                "state": self.volatility.state,
                "state_ru": self.volatility.state_ru,
                "atr_pct": round(self.volatility.atr_pct, 4),
                "atr_pctl": round(self.volatility.atr_pctl, 2),
                "squeeze": self.volatility.squeeze,
                "rv_20": round(self.volatility.rv_20, 3),
            },
            "structure": {
                "trend": self.structure.trend,
                "strength": round(self.structure.strength, 2),
                "adx": round(self.structure.adx, 1),
                "support": self.support,
                "resistance": self.resistance,
                "above_vwap": self.structure.above_vwap,
            },
            "momentum": {
                "rsi": round(self.momentum.rsi, 1),
                "macd_hist": round(self.momentum.macd_hist, 5),
                "stoch_k": round(self.momentum.stoch_k, 1),
                "vol_z": round(self.momentum.vol_z, 2),
                "st_dir": self.momentum.st_dir,
            },
            "elliott": {
                "pattern": self.elliott.pattern,
                "wave_position": self.elliott.wave_position,
                "confidence": round(self.elliott.confidence, 2),
                "note": self.elliott.note,
            },
            "funding_rate": self.funding_rate,
            "funding_trend": self.funding_trend,
            "liquidations_note": self.liquidations_note,
            "fib": {str(k): v for k, v in self.fib.items()},
            "is_demo": self.is_demo,
            "plan": None,
        }
        if self.plan:
            d["plan"] = {
                "direction": self.plan.direction,
                "entry_zone": [round(x, 8) for x in self.plan.entry_zone],
                "stop_loss": round(self.plan.stop_loss, 8),
                "targets": [round(t, 8) for t in self.plan.targets],
                "rr": round(self.plan.rr, 2),
                "leverage": self.plan.leverage,
                "position_pct": self.plan.position_pct,
                "distance_pct": round(self.plan.distance_pct, 2),
                "invalidation": self.plan.invalidation,
            }
        return d


class AnalysisEngine:
    """Оркестрирует сбор данных и анализ одной монеты."""

    def __init__(self, source: MarketDataSource, settings: Settings):
        self.source = source
        self.settings = settings
        self._cache: dict[str, tuple[float, AnalysisResult]] = {}
        self._cache_ttl = 60.0

    async def analyze(self, symbol: str, refresh: bool = False) -> AnalysisResult:
        import time

        symbol = symbol.upper()
        key = f"{symbol}:{ENTRY_TF}"
        cached = self._cache.get(key)
        if cached and not refresh and time.time() - cached[0] < self._cache_ttl:
            return cached[1]
        try:
            result = await self._analyze_inner(symbol)
            self._cache[key] = (time.time(), result)
            return result
        except (NotEnoughData, AnalysisError):
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("Анализ %s не удался: %s", symbol, e)
            raise AnalysisError(f"Анализ {symbol} не удался: {e}") from e

    async def _analyze_inner(self, symbol: str) -> AnalysisResult:
        # ── Данные: 15m (деталь), 1h (медиум), 4h (макро) ──
        df_entry = await self.source.get_klines(symbol, ENTRY_TF, 300)
        df_medium = await self.source.get_klines(symbol, MEDIUM_TF, 200)
        df_macro = await self.source.get_klines(symbol, MACRO_TF, 160)
        if len(df_entry) < MIN_BARS:
            raise NotEnoughData(f"{symbol}: всего {len(df_entry)} баров на {ENTRY_TF}")
        return self.analyze_frames(
            symbol,
            df_entry,
            df_medium,
            df_macro,
            ticker=await self._safe_ticker(symbol),
            funding_hist=await self._safe_funding(symbol),
            liquidations=await self._safe_liquidations(symbol),
        )

    def analyze_frames(
        self,
        symbol: str,
        df_entry: pd.DataFrame,
        df_medium: pd.DataFrame,
        df_macro: pd.DataFrame,
        ticker: Ticker | None = None,
        funding_hist: list | None = None,
        liquidations: list[Liquidation] | None = None,
        timeframe_main: str = ENTRY_TF,
    ) -> AnalysisResult:
        """
        Анализ на готовых срезах данных — единственный путь к честному бэктесту:
        вызывающий сам решает, какие бары считать «прошлым». Live-режим и
        бэктест используют один и тот же код, поэтому расхождения логики быть
        не может (backtest/live parity, как в nautilus_trader).
        """
        if len(df_entry) < MIN_BARS:
            raise NotEnoughData(f"{symbol}: всего {len(df_entry)} баров на {timeframe_main}")
        funding_hist = funding_hist or []
        liquidations = liquidations or []

        fe = compute_all(df_entry)
        fm = compute_all(df_medium) if len(df_medium) >= MIN_BARS else fe
        fa = compute_all(df_macro) if len(df_macro) >= 30 else fm

        price = float(fe["close"].iloc[-1])
        atr = float(fe["atr_14"].iloc[-1])

        # ── Рыночный контекст ──
        # В бэктесте тикера нет: считаем суточные метрики из самих свечей,
        # чтобы не подглядывать в будущее.
        if ticker is None:
            price_24h_pct = self._history_24h_pct(fe)
            turnover_24h = self._history_turnover(fe)
            volume_24h = float(fe["volume"].tail(96).sum())
            funding_rate = None
        else:
            price_24h_pct = ticker.price_24h_pct
            turnover_24h = ticker.turnover_24h
            volume_24h = ticker.volume_24h
            funding_rate = ticker.funding_rate

        # ── Компоненты анализа ──
        structure = market_structure(fe)
        vol_state = volatility_state(fe)
        mom = momentum(fe)
        ell = elliott(fm)

        funding_trend = self._funding_trend(funding_hist)
        liq_note = self._liquidations_note(liquidations)

        # ── План входа/выхода ──
        plan, reasons, risks, direction, confidence = self._build_plan(
            fe, fm, fa, price, atr, structure, vol_state, mom, ell, funding_trend, liquidations
        )

        # ── Скоринг «скрытой» монеты ──
        from src.analysis.scoring import score_hidden_gem

        market_cap = None
        if ticker and getattr(ticker, "open_interest", None):
            market_cap = None  # мкап придёт из сканера при наличии
        breakdown = score_hidden_gem(
            price_24h_pct=price_24h_pct,
            turnover_usd=turnover_24h,
            volume_z=float(fe["vol_z"].iloc[-1]),
            atr_pctl=vol_state.atr_pctl,
            rsi=mom.rsi,
            roc_20=mom.roc_20,
            market_cap=market_cap,
            st_dir=mom.st_dir,
            squeeze=vol_state.squeeze,
            funding_rate=funding_rate,
            is_major=symbol in {"BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"},
        )
        tier = tier_from_score(breakdown.total)
        verdict, recommendation = verdict_from_score(breakdown.total)

        # ── Fib-уровни ──
        fib = self._fib_map(fe)

        # ── Резюме ──
        summary = self._make_summary(symbol, price, direction, plan, structure, vol_state, ell, confidence)

        return AnalysisResult(
            symbol=symbol,
            ts_ms=now_ms(),
            timeframe_main=timeframe_main,
            price=price,
            price_24h_pct=round(price_24h_pct, 2),
            turnover_24h=turnover_24h,
            volume_24h=volume_24h,
            market_cap=market_cap,
            direction=direction,
            confidence=round(confidence, 2),
            score=breakdown.total,
            tier=tier,
            verdict=verdict,
            recommendation=recommendation,
            plan=plan,
            summary=summary,
            reasons=reasons,
            risks=risks,
            structure=structure,
            volatility=vol_state,
            momentum=mom,
            elliott=ell,
            funding_rate=funding_rate,
            funding_trend=funding_trend,
            funding_history=[f.rate for f in funding_hist[:8]],
            liquidations_note=liq_note,
            support=structure.support,
            resistance=structure.resistance,
            fib=fib,
            is_demo=self.source.is_demo,
        )
    # ── Метрики из истории (для бэктеста, без внешнего тикера) ──
    @staticmethod
    def _history_24h_pct(fe: pd.DataFrame) -> float:
        """Изменение за ~24ч: берём столько баров, сколько даёт фрейм."""
        close = fe["close"]
        n = min(len(close) - 1, 96)
        if n <= 0:
            return 0.0
        prev = float(close.iloc[-1 - n])
        if prev <= 0:
            return 0.0
        return (float(close.iloc[-1]) - prev) / prev * 100.0

    @staticmethod
    def _history_turnover(fe: pd.DataFrame) -> float:
        """Оборот за ~24ч в quote (close × volume)."""
        tail = fe.tail(min(len(fe), 96))
        return float((tail["close"] * tail["volume"]).sum())

    # ── Вспомогательные загрузки (с fallback) ──
    async def _safe_ticker(self, symbol: str) -> Ticker | None:
        try:
            tickers = await self.source.get_tickers([symbol])
            return tickers[0] if tickers else None
        except Exception:  # noqa: BLE001
            return None

    async def _safe_funding(self, symbol: str) -> list:
        try:
            return await self.source.get_funding(symbol, 12)
        except Exception:  # noqa: BLE001
            return []

    async def _safe_liquidations(self, symbol: str) -> list[Liquidation]:
        try:
            all_liq = await self.source.get_recent_liquidations(200)
            return [liq for liq in all_liq if liq.symbol == symbol]
        except Exception:  # noqa: BLE001
            return []

    @staticmethod
    def _funding_trend(hist: list) -> str:
        if not hist:
            return "нет данных"
        vals = [h.rate for h in hist]
        avg = sum(vals) / len(vals)
        last = vals[-1]
        if last > 0.0015:
            return "перегретый лонг 🔥"
        if last < -0.0010:
            return "перегруженный шорт ❄️"
        if last > avg + 0.0003:
            return "растущий (лонги платят всё больше)"
        if last < avg - 0.0003:
            return "падающий (шорты платят)"
        return "нейтральный"

    @staticmethod
    def _liquidations_note(liqs: list[Liquidation]) -> str:
        if not liqs:
            return "Свежих крупных ликвидаций не зафиксировано"
        buy = sum(liq.size for liq in liqs if liq.side == "Buy")
        sell = sum(liq.size for liq in liqs if liq.side == "Sell")
        n = len(liqs)
        if sell > buy * 1.4:
            return f"Массовые ликвидации шортов ({n} шт, ${sell/1e6:.1f}M) — топливо для роста"
        if buy > sell * 1.4:
            return f"Массовые ликвидации лонгов ({n} шт, ${buy/1e6:.1f}M) — давление вниз"
        return f"Ликвидации сбалансированы ({n} шт)"

    # ── План входа/выхода ──
    def _build_plan(
        self,
        fe: pd.DataFrame,
        fm: pd.DataFrame,
        fa: pd.DataFrame,
        price: float,
        atr: float,
        structure: StructureState,
        vol: VolatilityState,
        mom: MomentumState,
        ell: ElliottResult,
        funding_trend: str,
        liquidations: list[Liquidation],
    ) -> tuple[EntryPlan | None, list[str], list[str], str, float]:
        reasons: list[str] = []
        risks: list[str] = []
        direction = "NEUTRAL"
        confidence = 0.35

        # ── Макро-фильтр (4h) ──
        macro = market_structure(fa)
        macro_mom = momentum(fa)
        macro_up = macro.trend == "up" or float(fa["close"].iloc[-1]) > float(fa["ema_50"].iloc[-1])
        macro_down = macro.trend == "down" or float(fa["close"].iloc[-1]) < float(fa["ema_50"].iloc[-1])

        # ── Оценка факторов LONG ──
        long_factors: list[tuple[str, bool]] = []
        short_factors: list[tuple[str, bool]] = []

        # Структура на 15m/1h
        medium = market_structure(fm)
        long_factors.append(("15m-тренд вверх (ADX)", structure.trend == "up"))
        short_factors.append(("15m-тренд вниз (ADX)", structure.trend == "down"))
        long_factors.append(("1h-тренд вверх", medium.trend == "up"))
        short_factors.append(("1h-тренд вниз", medium.trend == "down"))

        # Моментум
        long_factors.append(("RSI в бычьей зоне (50–70)", 50 < mom.rsi < 70))
        short_factors.append(("RSI в медвежьей зоне (30–50)", 30 < mom.rsi < 50))
        long_factors.append(("MACD-гистограмма растёт", mom.macd_hist > 0))
        short_factors.append(("MACD-гистограмма падает", mom.macd_hist < 0))
        long_factors.append(("Supertrend вверх", mom.st_dir > 0))
        short_factors.append(("Supertrend вниз", mom.st_dir < 0))
        long_factors.append(("CVD/OBV подтверждают приток", mom.cvd_trend > 0 and mom.obv_trend > 0))
        short_factors.append(("CVD/OBV подтверждают отток", mom.cvd_trend < 0 and mom.obv_trend < 0))
        long_factors.append(("Объём выше нормы (z>0.5)", mom.vol_z > 0.5))
        short_factors.append(("Объём выше нормы (z>0.5)", mom.vol_z > 0.5))

        # Волны
        if ell.pattern == "impulse" and ell.confidence >= 0.5:
            risks.append(f"Волны: {ell.note} — риск входа на вершине импульса")
        if ell.pattern == "correction" and ell.trend_dir > 0:
            long_factors.append(("Коррекция A-B-C завершается", True))
        if ell.pattern == "correction" and ell.trend_dir < 0:
            short_factors.append(("Коррекция A-B-C завершается (вниз)", True))

        # Макро-фильтр
        long_factors.append(("4h-тренд вверх", macro_up))
        short_factors.append(("4h-тренд вниз", macro_down))

        # Фандинг
        if "растущий" in funding_trend:
            long_factors.append(("Фандинг поддерживает лонг", True))
        if "перегретый лонг" in funding_trend:
            risks.append("Фандинг перегрет — толпа в лонгах, возможен сквиз вниз")
            short_factors.append(("Перегретый фандинг (контрарианский шорт)", True))
        if "перегруженный шорт" in funding_trend:
            risks.append("Шорты перегружены — возможен шорт-сквиз вверх")
            long_factors.append(("Перегруженный фандинг (топливо для роста)", True))

        # Ликвидации
        if "шортов" in self._liquidations_note(liquidations):
            long_factors.append(("Ликвидации шортов — топливо вверх", True))
        if "лонгов" in self._liquidations_note(liquidations):
            short_factors.append(("Ликвидации лонгов — давление вниз", True))

        n_long = sum(1 for _, ok in long_factors if ok)
        n_short = sum(1 for _, ok in short_factors if ok)
        n_total = max(len(long_factors), len(short_factors))

        long_ok = [name for name, ok in long_factors if ok]
        short_ok = [name for name, ok in short_factors if ok]

        # ── Волатильность как фильтр ──
        if vol.state in ("extreme",):
            risks.append("Волатильность экстремальная — позицию держать с уменьшенным плечом или ждать остывания")
        if vol.state == "squeeze":
            reasons.append("✓ Squeeze: волатильность сжата — вероятен резкий выход из диапазона")
            confidence += 0.05

        ratio_long = n_long / n_total if n_total else 0
        ratio_short = n_short / n_total if n_total else 0

        # ── Выбор направления ──
        if ratio_long >= 0.62 and ratio_long > ratio_short and macro_up:
            direction = "LONG"
            confidence = 0.45 + 0.35 * (ratio_long - 0.6) + (0.1 if medium.trend == "up" else 0)
        elif ratio_short >= 0.62 and ratio_short > ratio_long and macro_down:
            direction = "SHORT"
            confidence = 0.45 + 0.35 * (ratio_short - 0.6) + (0.1 if medium.trend == "down" else 0)
        else:
            direction = "WAIT"
            confidence = 0.35
            reasons.append("• Направление не подтверждено на всех таймфреймах")

        confidence = float(np.clip(confidence, 0.1, 0.95))
        if confidence < 0.45:
            direction = "WAIT" if direction != "NEUTRAL" else direction

        # Показываем факторы выбранного направления (для WAIT — обе стороны)
        if direction in ("LONG", "SHORT"):
            aligned = long_ok if direction == "LONG" else short_ok
            for name in aligned:
                reasons.append(f"✓ {name}")
        else:
            if long_ok:
                reasons.append(f"Бычьи факторы: {', '.join(long_ok[:4])}")
            if short_ok:
                reasons.append(f"Медвежьи факторы: {', '.join(short_ok[:4])}")

        # ── Построение плана ──
        plan = self._make_entry_plan(
            fe, price, atr, direction, structure, vol, mom, macro_up, macro_down
        )
        if plan:
            reasons.append(f"• План: вход {plan.entry_zone[0]:.8g}–{plan.entry_zone[1]:.8g}, стоп {plan.stop_loss:.8g}, R:R {plan.rr:.1f}")
            if plan.rr < self.settings.MIN_RISK_REWARD:
                risks.append(f"R:R ниже порога {self.settings.MIN_RISK_REWARD} — уменьшите риск или пропустите сделку")

        if not risks:
            risks.append("Стандартный рыночный риск: резкий новостной импульс без отката")

        return plan, reasons, risks, direction, confidence

    def _make_entry_plan(
        self,
        fe: pd.DataFrame,
        price: float,
        atr: float,
        direction: str,
        structure: StructureState,
        vol: VolatilityState,
        mom: MomentumState,
        macro_up: bool,
        macro_down: bool,
    ) -> EntryPlan | None:
        if direction not in ("LONG", "SHORT"):
            return None
        if not np.isfinite(atr) or atr <= 0:
            return None

        is_long = direction == "LONG"
        sw_high = structure.last_swing_high or price * 1.02
        sw_low = structure.last_swing_low or price * 0.98

        # Базис свинга для фибо
        if is_long:
            base_low, base_high = min(sw_low, price), max(sw_high, price)
        else:
            base_low, base_high = min(sw_low, price), max(sw_high, price)

        fib = fib_levels(base_low, base_high, direction=1 if is_long else -1)

        risk_cap = 3.5 * atr  # макс. дистанция до структурного стопа

        # ── Опорная нога: последний значимый импульс в направлении сделки ──
        zz = zigzag(fe, pct_threshold=0.4, use_atr=True)
        leg: tuple[float, float] | None = None
        for i in range(len(zz) - 1, 0, -1):
            p0, p1 = zz[i - 1][1], zz[i][1]
            if (is_long and p1 > p0) or (not is_long and p1 < p0):
                if abs(p1 - p0) >= 1.2 * atr:
                    leg = (p0, p1)
                    break

        if leg:
            a, b = leg
            span = abs(b - a)
            if is_long:
                zone = (b - 0.618 * span, b - 0.382 * span)  # зона отката к импульсу
                if price < zone[0]:
                    zone = (price - 0.5 * atr, price)  # уже ниже зоны — вход по рынку
                elif price < zone[1]:
                    zone = (zone[0], price)  # цена внутри зоны
                stop = a - 0.15 * span  # инвалидация: ниже начала импульса
                if zone[0] - stop > risk_cap:
                    stop = zone[0] - 1.8 * atr
                targets = [b + k * span for k in (0.618, 1.0, 1.618)]
            else:
                zone = (b + 0.382 * span, b + 0.618 * span)  # зона отката вверх
                if price > zone[1]:
                    zone = (price, price + 0.5 * atr)  # уже выше зоны — вход по рынку
                elif price > zone[0]:
                    zone = (price, zone[1])  # цена внутри зоны
                stop = a + 0.15 * span  # инвалидация: выше начала импульса
                if stop - zone[1] > risk_cap:
                    stop = zone[1] + 1.8 * atr
                targets = [b - k * span for k in (0.618, 1.0, 1.618)]
            zone = (min(zone), max(zone))
        else:
            if is_long:
                zone_lo = max(price - 0.5 * atr, fib.retracements[0.618])
                zone_hi = min(price, fib.retracements[0.382])
                if zone_hi < zone_lo:
                    zone_lo, zone_hi = price - 0.5 * atr, price
                zone = (zone_lo, zone_hi)
                struct_stop = min(base_low, structure.last_swing_low or base_low)
                if zone_lo - struct_stop <= risk_cap and struct_stop < zone_lo:
                    stop = struct_stop
                else:
                    stop = zone_lo - 1.8 * atr  # структура далеко — риск ограничиваем ATR
                risk = zone_lo - stop
            else:
                zone_hi = min(price + 0.5 * atr, fib.retracements[0.618])
                zone_lo = max(price, fib.retracements[0.382])
                if zone_hi < zone_lo:
                    zone_lo, zone_hi = price, price + 0.5 * atr
                zone = (zone_lo, zone_hi)
                struct_stop = max(base_high, structure.last_swing_high or base_high)
                if struct_stop - zone_hi <= risk_cap and struct_stop > zone_hi:
                    stop = struct_stop
                else:
                    stop = zone_hi + 1.8 * atr  # структура далеко — риск ограничиваем ATR
                risk = stop - zone_hi
            if is_long:
                cand = sorted([base_high * k for k in (1.02, 1.05, 1.09)])
                targets = [t for t in cand if t > zone[1]]
            else:
                cand = sorted([base_low * k for k in (0.98, 0.95, 0.91)], reverse=True)
                targets = [t for t in cand if t < zone[0]]
            if not targets:
                targets = [base_high * 1.03 if is_long else base_low * 0.97]

        entry_ref = zone[0] if is_long else zone[1]
        rr = compute_rr(entry_ref, stop, targets[0], 1 if is_long else -1)

        # Дистанция до зоны входа (до ближайшей границы зоны)
        if is_long:
            distance_pct = (price - zone[1]) / price * 100 if price > zone[1] else 0.0
        else:
            distance_pct = (zone[0] - price) / price * 100 if price < zone[0] else 0.0
        distance_pct = max(distance_pct, 0.0)

        # Плечо: ограничиваем по волатильности и настройкам
        atr_pct = vol.atr_pct
        leverage = int(np.clip(math.floor(2.0 / max(atr_pct, 0.3)), 2, self.settings.MAX_LEVERAGE))
        if vol.state in ("extreme", "high"):
            leverage = max(2, leverage - 2)
        position_pct = self.settings.RISK_PER_TRADE_PCT

        invalidation = (
            f"Закрытие {ENTRY_TF}-свечи ниже {stop:.8g}" if is_long else f"Закрытие {ENTRY_TF}-свечи выше {stop:.8g}"
        )

        return EntryPlan(
            direction=direction,
            entry_zone=zone,
            stop_loss=stop,
            targets=targets,
            rr=round(rr, 2),
            leverage=leverage,
            position_pct=position_pct,
            distance_pct=round(distance_pct, 2),
            invalidation=invalidation,
            t1_distance_pct=round(abs(targets[0] - price) / price * 100, 2),
        )

    @staticmethod
    def _fib_map(fe: pd.DataFrame) -> dict:
        zz = zigzag(fe, pct_threshold=0.5, use_atr=True)
        if len(zz) < 2:
            return {"0.382": None, "0.5": None, "0.618": None, "1.0": None, "1.618": None}
        lo = min(p for _, p in zz[-4:])
        hi = max(p for _, p in zz[-4:])
        price = float(fe["close"].iloc[-1])
        up = price >= (lo + hi) / 2
        fib = fib_levels(lo, hi, direction=1 if up else -1)
        out: dict = {}
        for k, v in fib.retracements.items():
            out[str(k)] = v
        for k, v in fib.extensions.items():
            out[str(k)] = v
        return out

    @staticmethod
    def _make_summary(
        symbol: str,
        price: float,
        direction: str,
        plan: EntryPlan | None,
        structure: StructureState,
        vol: VolatilityState,
        ell: ElliottResult,
        confidence: float,
    ) -> str:
        base = symbol.replace("USDT", "")
        trend_ru = {"up": "восходящий", "down": "нисходящий", "range": "боковой"}.get(structure.trend, structure.trend)
        if direction == "LONG":
            head = f"📈 {base}: бычий сценарий (уверенность {confidence*100:.0f}%)"
        elif direction == "SHORT":
            head = f"📉 {base}: медвежий сценарий (уверенность {confidence*100:.0f}%)"
        else:
            head = f"⏸ {base}: входить рано (уверенность в сценарии {confidence*100:.0f}%)"
        parts = [
            head,
            f"Тренд 15m: {trend_ru}, ADX {structure.adx:.0f}",
            f"Волатильность: {vol.state_ru}, ATR {vol.atr_pct:.2f}%",
        ]
        if ell.pattern != "unclear":
            parts.append(f"Волны: {ell.note.lower()}")
        if plan:
            parts.append(
                f"План: {'лонг' if plan.direction == 'LONG' else 'шорт'} из зоны "
                f"{plan.entry_zone[0]:.8g}–{plan.entry_zone[1]:.8g} (сейчас {price:.8g}), "
                f"стоп {plan.stop_loss:.8g}, цели {', '.join(f'{t:.8g}' for t in plan.targets[:2])}, R:R {plan.rr:.1f}"
            )
        return " | ".join(parts)
