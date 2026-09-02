"""v3 signal engine: the deterministic core of the futures signal system.

Live runs and the backtester call the same ``evaluate_bundle`` method
(backtest/live parity).  The engine:

  * normalises data into timeframes / derivatives / order-flow / context;
  * detects market regime;
  * selects a direction or returns WAIT;
  * computes entry / SL / TP and risk;
  * runs the deterministic gate: bad data, poor R:R, thin liquidity,
    contradictory timeframes, or a weak score => NO TRADE;
  * only then produces a tradable signal.

The optional AI reasoning layer (``reason``) is an *explanation* layer and is
not allowed to invent market data or override the deterministic gate.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from v3.ai import build_reasoner
from v3.analysis.confidence import attach_confidence
from v3.analysis.context import build_context
from v3.analysis.derivatives import analyze_derivatives, oi_change_pct
from v3.analysis.emergence import detect_emergence
from v3.analysis.levels import build_levels
from v3.analysis.orderflow import analyze_orderflow
from v3.analysis.regime import detect_regime
from v3.analysis.risk import build_risk_brief, risk_score
from v3.analysis.scenarios import pick_scenario
from v3.analysis.scoring import score_signal, tier_from_quality
from v3.analysis.timeframes import build_timeframe_view
from v3.config import SignalConfig
from v3.data import FuturesDataService
from v3.models import (
    DataBundle,
    MarketContext,
    RegimeSnapshot,
    TimeframeView,
    TradingSignal,
)
from v3.observability import metrics

TREND_DIRECTION_TRUST = 0.34


def _weighted_vote(views: list[TimeframeView]) -> tuple[str, float]:
    up, down, total = 0.0, 0.0, 0.0
    for i, v in enumerate(views):
        w = 1.0 + i * 0.35
        total += w
        if v.trend == "up":
            up += w
        elif v.trend == "down":
            down += w
    if total <= 0:
        return "flat", 0.0
    score = (up - down) / total
    if score >= TREND_DIRECTION_TRUST:
        return "up", score
    if score <= -TREND_DIRECTION_TRUST:
        return "down", score
    return "flat", score


def _emergence_snapshot(bundle: DataBundle, tf_map: dict[str, Any], cfg: SignalConfig):
    """Build the early-impulse feature from a closed intermediate timeframe.

    The explicit DataFrame selection is intentional: ``DataFrame.__bool__`` is
    undefined, so ``a or b`` here would intermittently crash real analyses.
    """
    if not cfg.SCAN_EMERGENCE_ENABLED or bundle.price <= 0:
        return None
    tf = cfg.INTERMEDIATE_TF if cfg.INTERMEDIATE_TF in tf_map else cfg.ENTRY_TF
    df = tf_map.get(tf)
    if df is None or len(df) < 30:
        return None
    tf_ms = {
        "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
        "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000,
    }.get(tf, 3_600_000)
    bars_24h = max(12, int(86_400_000 / tf_ms))
    tail = df.tail(bars_24h)
    # ``open_interest_history`` stores raw OI, not percentages. Keep the
    # conversion in one helper so emergence and derivatives use the same value.
    oi_delta = oi_change_pct(bundle)
    return detect_emergence(
        df,
        price_24h_pct=bundle.price_24h_pct,
        high_24h=float(tail["high"].max()) if len(tail) else None,
        low_24h=float(tail["low"].min()) if len(tail) else None,
        btc_24h_pct=bundle.btc_price_24h_pct,
        oi_delta_pct=oi_delta,
        funding_rate=bundle.funding_rate,
        cfg=cfg,
    )


class FuturesSignalEngine:
    def __init__(self, data: FuturesDataService, cfg: SignalConfig | None = None) -> None:
        self.data = data
        self.cfg = cfg or SignalConfig()
        self._cache: dict[str, tuple[float, TradingSignal]] = {}
        self.reasoner = build_reasoner(self.cfg) if self.cfg.AI_ENABLED else None

    TF_MS_MAP = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000}
    ANALYZE_CACHE_TTL = 60.0

    async def analyze(self, symbol: str, refresh: bool = False, deep: bool = True) -> TradingSignal:
        symbol = symbol.upper()
        key = f"{symbol}"
        if not refresh and key in self._cache:
            ts, cached = self._cache[key]
            # устаревший кэш (stale-данные) никогда не отдаём повторно
            if time.time() - ts < self.ANALYZE_CACHE_TTL and not cached.stale:
                return cached

        started = time.time()
        bundle, fetches = await asyncio.gather(
            self.data.build_bundle(symbol, deep=deep),
            asyncio.gather(
                *(self.data.klines(symbol, tf, self.cfg.ANALYSIS_BARS) for tf in self.cfg.timeframes),
                return_exceptions=True,
            ),
        )
        now_ms = int(time.time() * 1000)
        tf_map: dict[str, Any] = {}
        for tf, df in zip(self.cfg.timeframes, fetches):
            if isinstance(df, BaseException):
                bundle.degraded.append(f"{tf}: fetch failed")
                continue
            if df is not None and len(df) >= min(40, self.cfg.MIN_BARS):
                tf_map[tf] = df
                # Проверка «отстающего графика». Сервис данных отдаёт только
                # ЗАКРЫТЫЕ свечи (см. data._closed_bars), поэтому свежие данные
                # — это «последняя закрытая свеча закрылась не раньше одного
                # таймфрейма назад». Две прошлые версии правила были неверны:
                #  * сравнение ВРЕМЕНИ ОТКРЫТИЯ с tf + MAX_DATA_AGE_SECONDS
                #    объявляло устаревшими любые данные почти всё время
                #    (закрытая часовая свеча по построению старше часа);
                #  * выравнивание по now // tf_ms ломается на биржах, где сутки
                #    начинаются не в 00:00 UTC (OKX — 00:00 UTC+8).
                # Найдено прогоном на реальных свечах биржи (v3/replay.py).
                last_open = int(df["ts"].iloc[-1])
                tf_ms = self.TF_MS_MAP.get(tf, 3_600_000)
                since_close_ms = now_ms - (last_open + tf_ms)
                if since_close_ms > tf_ms + self.cfg.MAX_DATA_AGE_SECONDS * 1000 and not any(
                    "stale klines" in d for d in bundle.degraded
                ):
                    bundle.degraded.append(f"stale klines ({tf})")
                # непрерывность свечей: пропуски/нулевые объёмы честно деградируют confidence
                gaps, zero_vol = candle_series_problems(df, tf_ms)
                if gaps:
                    bundle.degraded.append(f"пропуск свечей {tf} ({gaps})")
                if zero_vol:
                    bundle.degraded.append(f"нулевой объём свечей {tf} ({zero_vol})")

        # возраст данных — по биржевому timestamp последней свечи входного ТФ,
        # если тикер его не дал (тикерный возраст = возраст кэша, это fallback).
        # Свеча закрыта в момент (open + длительность) — от него и считаем.
        entry_df = tf_map.get(self.cfg.ENTRY_TF)
        if bundle.data_age_seconds is None and entry_df is not None and len(entry_df):
            last_open = int(entry_df["ts"].iloc[-1])
            entry_tf_ms = self.TF_MS_MAP.get(self.cfg.ENTRY_TF, 3_600_000)
            bundle.data_age_seconds = max(0.0, (now_ms - last_open - entry_tf_ms) / 1000.0)

        btc_df = None
        try:
            btc_df = await self.data.klines("BTCUSDT", "1h", self.cfg.ANALYSIS_BARS)
            if len(btc_df) < 40:
                btc_df = None
        except Exception:  # noqa: BLE001
            btc_df = None

        signal = self.evaluate_bundle(bundle, tf_map, btc_tf=btc_df)
        signal.source = getattr(self.data, "mode", "") or ""
        signal.duration_sec = time.time() - started
        self._cache[key] = (time.time(), signal)
        metrics.mark_mode(getattr(self.data, "mode", "unknown"), True)
        metrics.record_analysis(symbol, signal.duration_sec)
        return signal

    async def analyze_batch(self, symbols: list[str], concurrency: int = 6, deep: bool = True) -> list[TradingSignal]:
        sem = asyncio.Semaphore(max(1, concurrency))

        async def _one(sym: str) -> TradingSignal:
            async with sem:
                try:
                    return await self.analyze(sym, refresh=True, deep=deep)
                except Exception:  # noqa: BLE001
                    return TradingSignal(uid="", symbol=sym, ts_ms=0, direction="NO_TRADE", status="NO_TRADE", no_trade_reasons=["analysis failed"])

        return await asyncio.gather(*(_one(s) for s in symbols))

    # ── pure evaluation (used by live + backtest) ───────────────
    def evaluate_bundle(
        self,
        bundle: DataBundle,
        tf_map: dict[str, Any],
        btc_tf: Any = None,
        deposit_usd: float | None = None,
        ai_reasoner: Any = None,
        strict_liquidity: bool = True,
    ) -> TradingSignal:
        started = time.time()
        views: list[TimeframeView] = []
        degraded = list(bundle.degraded)
        for tf in self.cfg.timeframes:
            df = tf_map.get(tf)
            if df is not None and len(df) >= min(40, self.cfg.MIN_BARS):
                try:
                    views.append(build_timeframe_view(df, tf))
                except Exception as exc:  # noqa: BLE001
                    degraded.append(f"{tf}: indicator error ({exc})")
            else:
                degraded.append(f"{tf}: insufficient data")

        if not views:
            sig = self._no_trade(
                bundle, ["no usable timeframe data"], degraded,
                "WAIT", None, RegimeSnapshot(note="no data"), started,
            )
            sig.features["no_data"] = True
            return sig

        btc_view = None
        if btc_tf is not None:
            btc_view = build_timeframe_view(btc_tf, "1h") if len(btc_tf) >= 40 else None

        context = build_context(bundle, btc_view, self.cfg)
        regime = detect_regime(views, self.cfg)
        derivatives = analyze_derivatives(bundle, self.cfg)
        entry_view = views[0]
        orderflow = analyze_orderflow(bundle.orderbook, entry_view, self.cfg)
        # Ранний импульс считается в том же pure-пути, что и live, и backtest.
        # Поэтому его можно использовать как независимый фактор качества, не
        # создавая расхождения между исторической проверкой и продакшеном.
        emergence = _emergence_snapshot(bundle, tf_map, self.cfg)

        direction_vote, vote_strength = _weighted_vote(views)
        direction = "WAIT"
        scenario = ""
        condition = ""
        stop_hint: float | None = None
        reasons: list[str] = []
        risks: list[str] = []

        if regime.regime in ("TRENDING_UP", "BREAKOUT", "ACCUMULATION") and direction_vote == "up":
            direction = "LONG"
        elif regime.regime in ("TRENDING_DOWN", "BREAKDOWN", "DISTRIBUTION") and direction_vote == "down":
            direction = "SHORT"
        elif not regime.conflicts and direction_vote in ("up", "down") and vote_strength >= TREND_DIRECTION_TRUST:
            direction = "LONG" if direction_vote == "up" else "SHORT"

        if direction == "LONG" and not any(v.trend == "up" for v in views[-2:]):
            direction = "WAIT"
        if direction == "SHORT" and not any(v.trend == "down" for v in views[-2:]):
            direction = "WAIT"

        # альтернативные сценарии на реальных признаках (только если трендовый
        # путь не дал направления): sweep / CHoCH / range / условный пробой
        if direction == "WAIT" and not regime.conflicts:
            candidate = pick_scenario(
                views,
                tf_map.get(self.cfg.ENTRY_TF),
                bundle.price,
                regime,
                orderflow,
                self.cfg,
            )
            if candidate is not None:
                direction = candidate.direction
                scenario = candidate.kind
                condition = candidate.condition
                stop_hint = candidate.stop_hint
                reasons.extend(candidate.reasons)

        levels = build_levels(direction, bundle.price, entry_view.atr, entry_view, self.cfg, stop_override=stop_hint) if direction in ("LONG", "SHORT") else None

        rsk, risk_why = risk_score(bundle, views, derivatives, orderflow, context, regime, self.cfg)
        risks.extend(risk_why[:4])

        score = score_signal(
            bundle, views, derivatives, orderflow, context, regime, levels, rsk,
            direction if direction in ("LONG", "SHORT") else "WAIT", self.cfg,
            emergence=emergence,
        )

        if direction in ("LONG", "SHORT") and levels is not None:
            entry = bundle.price or levels.entry_zone[0]
            risk_brief = build_risk_brief(
                deposit_usd or self.cfg.DEFAULT_DEPOSIT_USD,
                entry,
                levels.stop_loss,
                rsk,
                entry_view.atr_pct,
                self.cfg,
            )
        else:
            risk_brief = build_risk_brief(
                deposit_usd or self.cfg.DEFAULT_DEPOSIT_USD,
                bundle.price or entry_view.atr or 1.0,
                bundle.price or 1.0,
                rsk,
                entry_view.atr_pct,
                self.cfg,
            )

        confidence = data_confidence(bundle, views, orderflow, context, self.cfg)

        # ── deterministic validation gate ───────────────────────
        violations = self.validate(
            bundle, views, context, orderflow, regime, levels, rsk, score, direction,
            strict_liquidity=strict_liquidity,
        )
        direction, status, no_trade = self.apply_direction_gate(violations, direction)

        # разворотные сценарии — мягче порог R:R (гейт не отключён); диапазон
        # средний; аномальная волатильность — строже по качеству
        min_rr = self.cfg.MIN_RISK_REWARD
        if scenario in ("reversal_choch", "liquidity_sweep", "range_reversion"):
            min_rr = self.cfg.MIN_RISK_REWARD_REVERSAL
        quality_min = self.cfg.QUALITY_MIN
        if regime.regime == "HIGH_VOLATILITY" and direction in ("LONG", "SHORT"):
            quality_min += 5.0
            reasons.append("аномальная волатильность — вход только при усиленном качестве, размер уменьшен")

        if direction in ("LONG", "SHORT") and levels is not None:
            direction, status, no_trade, levels = self.apply_risk_reward_gate(
                levels, rsk, score, deposit_usd or self.cfg.DEFAULT_DEPOSIT_USD,
                min_rr=min_rr, quality_min=quality_min,
            )

        if direction in ("LONG", "SHORT"):
            reasons.extend(signal_reasons(views, derivatives, orderflow, context, regime, levels, self.cfg))
            if scenario == "reversal_choch":
                risks.append("разворотный сценарий — повышенный риск, стоп за структурой")
            elif scenario == "liquidity_sweep":
                risks.append("сценарий stop-hunt — стоп за фитилём ложного пробоя")
            elif scenario == "range_reversion":
                risks.append("вход в диапазоне — меньший размер, выход у середины")
            if condition:
                risks.append("условный сетап: вход только после подтверждения условия")
            # positioning-риски простыми словами (раунд 4)
            pos = derivatives.positioning
            if pos == "overheated_long" and direction == "LONG":
                risks.append("позиции перегреты: OI растёт, цена падает, фандинг высокий — риск резкой коррекции")
            elif pos == "short_squeeze" and direction == "SHORT":
                risks.append("шорты выкупаются (short squeeze) — резкое движение может быть избыточным")
            elif pos == "capitulation" and direction == "SHORT":
                risks.append("капитуляция лонгов — шорт в зоне возможного разворота")
            if derivatives.liq_accel_usd >= 1_000_000:
                risks.append(
                    f"каскад ликвидаций ${derivatives.liq_accel_usd / 1e6:.1f}M "
                    f"за последние ~{self.cfg.LIQ_ACCELERATION_WINDOW_SEC // 60} мин — повышенная волатильность"
                )
            if levels is not None:
                risks.extend(level_risks(levels, view=entry_view))
            if score.total >= self.cfg.A_TIER_MIN:
                risks.append("high quality score still does not guarantee profit")
        else:
            reasons.extend(no_trade[:4] if no_trade else explain_wait(views, regime))
            risks.extend(["signal quality below threshold", "market regime unclear"] if not no_trade else [])

        quality = score.total
        tier = tier_from_quality(quality, self.cfg) if direction in ("LONG", "SHORT") else "NONE"

        data_age = bundle.data_age_seconds
        stale = bool(
            (data_age is not None and data_age > self.cfg.MAX_DATA_AGE_SECONDS)
            or any("stale" in d for d in degraded)
        )
        signal = TradingSignal(
            uid=signal_uid(bundle.symbol, bundle.ts_ms),
            symbol=bundle.symbol,
            ts_ms=bundle.ts_ms,
            direction=direction,
            status=status,
            entry_zone=levels.entry_zone if levels is not None else (0.0, 0.0),
            stop_loss=levels.stop_loss if levels is not None else 0.0,
            targets=levels.targets if levels is not None else [],
            rr=levels.rr if levels is not None else 0.0,
            tier=tier,
            score=round(score.total, 1),
            confidence=round(confidence, 2),
            quality=round(quality, 1),
            regime=regime.regime,
            risk_score=rsk,
            leverage=risk_brief.leverage,
            price=bundle.price,
            timeframe=self.cfg.ENTRY_TF,
            horizon=self.cfg.horizon,
            reasons=dedupe(reasons[:10]),
            risks=dedupe(risks[:8]),
            invalidation=levels.invalidation if levels is not None else "",
            no_trade_reasons=no_trade[:8],
            features=features_dict(
                views, derivatives, orderflow, context, regime, bundle.news_items,
                scenario=scenario, emergence=emergence,
            ),
            score_breakdown=score,
            risk_brief=risk_brief,
            scenario=scenario,
            condition=condition,
            data_age_seconds=round(data_age, 1) if data_age is not None else None,
            stale=stale,
            created_ms=time.time() * 1000,
            updated_ms=time.time() * 1000,
            duration_sec=round(time.time() - started, 2),
        )
        # «нет реальных данных»: тикер недоступен или нет ни одного биржевого
        # timestamp — пользователь видит предупреждение, а не «успешный» анализ
        if bundle.price <= 0 or any("no real market data" in v for v in no_trade):
            signal.features["no_data"] = True
        # «Уверенность бота» считается здесь же, в чистом пути: разбор уходит в
        # features вместе с сигналом, поэтому Telegram, API и SQLite показывают
        # одну и ту же цифру, а бэктест видит ровно тот же расчёт, что и live.
        attach_confidence(signal, self.cfg)
        active_reasoner = ai_reasoner or self.reasoner
        if active_reasoner is not None:
            try:
                signal = active_reasoner(signal)
            except Exception as exc:  # noqa: BLE001
                signal.risks.append(f"AI explanation degraded: {exc}")
        return signal

    def _attach_emergence(self, signal: TradingSignal, bundle: DataBundle, tf_map: dict[str, Any]) -> None:
        """Compatibility helper: attach the same pure early-impulse snapshot.

        ``evaluate_bundle`` already attaches it. Keeping this method idempotent
        protects integrations that called it directly in older deployments.
        """
        if "emergence" in signal.features:
            return
        try:
            emergence = _emergence_snapshot(bundle, tf_map, self.cfg)
            if emergence is None or not emergence.enabled:
                return
            signal.features["emergence"] = emergence.to_dict()
            signal.reasons = dedupe(signal.reasons + [n for n in emergence.notes if n])[:10]
        except Exception as exc:  # noqa: BLE001
            signal.risks.append(f"emergence degraded: {exc}")

    # ── gates ───────────────────────────────────────────────────
    def validate(
        self,
        bundle: DataBundle,
        views: list[TimeframeView],
        context: MarketContext,
        orderflow,
        regime: RegimeSnapshot,
        levels,
        rsk: int,
        score,
        direction: str,
        *,
        strict_liquidity: bool = True,
    ) -> list[str]:
        v: list[str] = []
        if bundle.price <= 0 or not _fin(bundle.price):
            v.append("non-positive/missing price")
        # инвариант «только реальные данные»: в живом пути нет биржевого
        # timestamp — нет сигнала (в бэктестовом пути метка возраста не нужна)
        if strict_liquidity and bundle.data_age_seconds is None:
            v.append("no real market data (missing exchange timestamp)")
        if bundle.data_age_seconds is not None and bundle.data_age_seconds > self.cfg.MAX_DATA_AGE_SECONDS:
            v.append(f"stale market data ({bundle.data_age_seconds:.0f}s old)")
        if any("stale klines" in d for d in bundle.degraded):
            v.append("stale kline data")
        if len(views) < 2:
            v.append("fewer than two usable timeframes")
        if strict_liquidity and orderflow.liquidity_grade == "empty":
            v.append("no usable order-book liquidity")
        if orderflow.spread_pct is not None and orderflow.spread_pct > self.cfg.MAX_SPREAD_PCT:
            v.append(f"spread {orderflow.spread_pct:.2f}% too wide")
        if bundle.turnover_24h and bundle.turnover_24h < self.cfg.SCAN_MIN_TURNOVER_USD:
            v.append("24h turnover below minimum liquidity")
        if rsk >= self.cfg.MAX_RISK_SCORE_TO_ENTER + 2:
            v.append("risk score critical")
        if regime.conflicts and direction in ("LONG", "SHORT"):
            v.append("timeframe conflict")
        if score is not None and score.total < self.cfg.C_TIER_MIN:
            v.append("quality score below C threshold")
        if context.btc_trend == "flat" and direction in ("LONG", "SHORT"):
            # BTC context missing is a caution, not a hard block
            pass
        return v

    def apply_direction_gate(
        self,
        violations: list[str],
        direction: str,
    ) -> tuple[str, str, list[str]]:
        if violations:
            return "NO_TRADE", "NO_TRADE", violations
        if direction == "WAIT":
            return "WAIT", "NO_TRADE", ["no directional setup in current market regime"]
        return direction, "CONFIRMED", []

    def apply_risk_reward_gate(
        self,
        levels,
        rsk: int,
        score,
        deposit_usd: float,
        *,
        min_rr: float | None = None,
        quality_min: float | None = None,
    ) -> tuple[str, str, list[str], Any]:
        no_trade: list[str] = []
        if levels is None:
            return "WAIT", "NO_TRADE", ["could not build entry levels"], None
        min_rr = self.cfg.MIN_RISK_REWARD if min_rr is None else min_rr
        qmin = self.cfg.QUALITY_MIN if quality_min is None else quality_min
        if levels.rr < min_rr:
            no_trade.append(f"R:R 1:{levels.rr:.2f} below minimum 1:{min_rr:.1f}")
        if rsk > self.cfg.MAX_RISK_SCORE_TO_ENTER:
            no_trade.append(f"risk score {rsk}/10 above max {self.cfg.MAX_RISK_SCORE_TO_ENTER}/10")
        if score.total < qmin:
            no_trade.append(f"quality {score.total:.1f} below min {qmin:.0f}")
        if no_trade:
            return "NO_TRADE", "NO_TRADE", no_trade, levels
        return levels.direction, "CONFIRMED", [], levels

    @staticmethod
    def _no_trade(
        bundle: DataBundle,
        reasons: list[str],
        degraded: list[str],
        _direction: str,
        _levels,
        regime: RegimeSnapshot,
        started: float,
    ) -> TradingSignal:
        age = bundle.data_age_seconds
        return TradingSignal(
            uid=signal_uid(bundle.symbol, bundle.ts_ms),
            symbol=bundle.symbol,
            ts_ms=bundle.ts_ms,
            direction="NO_TRADE",
            status="NO_TRADE",
            regime=regime.regime,
            no_trade_reasons=reasons,
            risks=degraded[:6],
            features={"degraded": degraded},
            data_age_seconds=round(age, 1) if age is not None else None,
            stale=any("stale" in d for d in degraded),
            duration_sec=round(time.time() - started, 2),
        )


def signal_uid(symbol: str, ts_ms: int) -> str:
    return f"{symbol}:{ts_ms}:{str(uuid.uuid4())[:8]}"


def data_confidence(bundle: DataBundle, views: list[TimeframeView], orderflow, context: MarketContext, cfg: SignalConfig) -> float:
    score = 1.0
    score -= min(0.5, 0.1 * len(getattr(bundle, "degraded", [])))
    if not views:
        score -= 0.4
    if orderflow.liquidity_grade == "empty":
        score -= 0.2
    if context.btc_trend == "flat":
        score -= 0.1
    if bundle.funding_rate is None:
        score -= 0.1
    return round(max(0.0, min(1.0, score)), 3)


def signal_reasons(
    views: list[TimeframeView],
    derivatives,
    orderflow,
    context: MarketContext,
    regime: RegimeSnapshot,
    levels,
    cfg: SignalConfig,
) -> list[str]:
    out: list[str] = []
    entry = views[0]
    out.append(f"regime {regime.regime}")
    if entry.trend == "up":
        out.append(f"{entry.timeframe} trend up (ADX {entry.adx:.0f})")
    elif entry.trend == "down":
        out.append(f"{entry.timeframe} trend down (ADX {entry.adx:.0f})")
    if derivatives.funding_trend in ("neutral", "falling"):
        out.append("funding not overheated")
    if orderflow.liquidity_grade in ("excellent", "ok"):
        out.append(f"liquidity {orderflow.liquidity_grade}")
    if orderflow.imbalance > 0.25:
        out.append("bid-side book imbalance")
    elif orderflow.imbalance < -0.25:
        out.append("ask-side book imbalance")
    if context.btc_trend != "flat":
        out.append(f"BTC context {context.btc_trend}")
    if levels is not None:
        out.append(f"R:R 1:{levels.rr:.2f}")
    return out


def level_risks(levels, view) -> list[str]:
    return [
        f"stop distance {levels.stop_pct:.2f}% is volatility-sensitive",
        f"invalidation: {levels.invalidation}",
    ]


def explain_wait(views: list[TimeframeView], regime: RegimeSnapshot) -> list[str]:
    conflicts = ", ".join(regime.conflicts) if regime.conflicts else "no single strong direction"
    return [f"{conflicts}; waiting for confirmation"]


def features_dict(
    views: list[TimeframeView],
    derivatives,
    orderflow,
    context: MarketContext,
    regime: RegimeSnapshot,
    news: list[dict[str, Any]] | None = None,
    scenario: str = "",
    emergence: Any | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "timeframes": [v.to_dict() for v in views],
        "derivatives": derivatives.to_dict(),
        "orderflow": orderflow.to_dict(),
        "context": context.to_dict(),
        "regime": regime.to_dict(),
    }
    if scenario:
        out["scenario"] = scenario
    if emergence is not None and getattr(emergence, "enabled", False):
        out["emergence"] = emergence.to_dict()
    if news:
        out["news"] = news
    return out


def candle_series_problems(df: Any, tf_ms: int) -> tuple[int, int]:
    """(пропуски свечей, свечи с нулевым объёмом) — честная деградация данных."""
    gaps = 0
    zero_vol = 0
    try:
        if df is None or len(df) < 3:
            return 0, 0
        diffs = df["ts"].diff().iloc[1:]
        gaps = int((diffs > int(tf_ms * 1.5)).sum())
        zero_vol = int((df["volume"] <= 0).sum())
    except Exception:  # noqa: BLE001
        return 0, 0
    return gaps, zero_vol


def dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        if v and v not in out:
            out.append(v)
    return out


def _fin(v: float) -> bool:
    try:
        import math

        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False
