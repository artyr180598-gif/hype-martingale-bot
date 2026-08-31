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
from v3.analysis.context import build_context
from v3.analysis.derivatives import analyze_derivatives
from v3.analysis.levels import build_levels
from v3.analysis.orderflow import analyze_orderflow
from v3.analysis.regime import detect_regime
from v3.analysis.risk import build_risk_brief, risk_score
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


class FuturesSignalEngine:
    def __init__(self, data: FuturesDataService, cfg: SignalConfig | None = None) -> None:
        self.data = data
        self.cfg = cfg or SignalConfig()
        self._cache: dict[str, tuple[float, TradingSignal]] = {}
        self.reasoner = build_reasoner(self.cfg) if self.cfg.AI_ENABLED else None

    async def analyze(self, symbol: str, refresh: bool = False) -> TradingSignal:
        symbol = symbol.upper()
        key = f"{symbol}"
        if not refresh and key in self._cache:
            ts, cached = self._cache[key]
            if time.time() - ts < 60:
                return cached

        started = time.time()
        bundle = await self.data.build_bundle(symbol)
        tf_map: dict[str, Any] = {}
        now_ms = int(time.time() * 1000)
        for tf in self.cfg.timeframes:
            df = await self.data.klines(symbol, tf, self.cfg.ANALYSIS_BARS)
            if len(df) >= min(40, self.cfg.MIN_BARS):
                tf_map[tf] = df
                # stale candle detection: the newest bar must start within the
                # timeframe + max allowed age, otherwise the chart is stale.
                last_close = int(df["ts"].iloc[-1])
                tf_ms = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "1d": 86_400_000}.get(tf, 3_600_000)
                age = now_ms - last_close
                if age > tf_ms + self.cfg.MAX_DATA_AGE_SECONDS * 1000 and not any("stale klines" in d for d in bundle.degraded):
                    bundle.degraded.append(f"stale klines ({tf})")

        btc_df = None
        try:
            btc_df = await self.data.klines("BTCUSDT", "1h", self.cfg.ANALYSIS_BARS)
            if len(btc_df) < 40:
                btc_df = None
        except Exception:  # noqa: BLE001
            btc_df = None

        signal = self.evaluate_bundle(bundle, tf_map, btc_tf=btc_df)
        signal.duration_sec = time.time() - started
        self._cache[key] = (time.time(), signal)
        metrics.mark_mode(self.data.mode, not bundle.is_demo)
        metrics.record_analysis(symbol, signal.duration_sec)
        return signal

    async def analyze_batch(self, symbols: list[str], concurrency: int = 6) -> list[TradingSignal]:
        sem = asyncio.Semaphore(max(1, concurrency))

        async def _one(sym: str) -> TradingSignal:
            async with sem:
                try:
                    return await self.analyze(sym, refresh=True)
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
            return self._no_trade(
                bundle, ["no usable timeframe data"], degraded,
                "WAIT", None, RegimeSnapshot(note="no data"), started,
            )

        btc_view = None
        if btc_tf is not None:
            btc_view = build_timeframe_view(btc_tf, "1h") if len(btc_tf) >= 40 else None

        context = build_context(bundle, btc_view, self.cfg)
        regime = detect_regime(views, self.cfg)
        derivatives = analyze_derivatives(bundle, self.cfg)
        entry_view = views[0]
        orderflow = analyze_orderflow(bundle.orderbook, entry_view, self.cfg)

        direction_vote, vote_strength = _weighted_vote(views)
        direction = "WAIT"
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

        levels = build_levels(direction, bundle.price, entry_view.atr, entry_view, self.cfg) if direction in ("LONG", "SHORT") else None

        rsk, risk_why = risk_score(bundle, views, derivatives, orderflow, context, regime, self.cfg)
        risks.extend(risk_why[:4])

        score = score_signal(
            bundle, views, derivatives, orderflow, context, regime, levels, rsk,
            direction if direction in ("LONG", "SHORT") else "WAIT", self.cfg,
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

        if direction in ("LONG", "SHORT") and levels is not None:
            direction, status, no_trade, levels = self.apply_risk_reward_gate(levels, rsk, score, deposit_usd or self.cfg.DEFAULT_DEPOSIT_USD)

        if direction in ("LONG", "SHORT"):
            reasons.extend(signal_reasons(views, derivatives, orderflow, context, regime, levels, self.cfg))
            if levels is not None:
                risks.extend(level_risks(levels, view=entry_view))
            if score.total >= self.cfg.A_TIER_MIN:
                risks.append("high quality score still does not guarantee profit")
        else:
            reasons.extend(no_trade[:4] if no_trade else explain_wait(views, regime))
            risks.extend(["signal quality below threshold", "market regime unclear"] if not no_trade else [])

        quality = score.total
        tier = tier_from_quality(quality, self.cfg) if direction in ("LONG", "SHORT") else "NONE"

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
            horizon="1m-4h",
            reasons=dedupe(reasons[:10]),
            risks=dedupe(risks[:8]),
            invalidation=levels.invalidation if levels is not None else "",
            no_trade_reasons=no_trade[:8],
            features=features_dict(views, derivatives, orderflow, context, regime),
            score_breakdown=score,
            risk_brief=risk_brief,
            is_demo=bundle.is_demo,
            created_ms=time.time() * 1000,
            updated_ms=time.time() * 1000,
            duration_sec=round(time.time() - started, 2),
        )
        active_reasoner = ai_reasoner or self.reasoner
        if active_reasoner is not None:
            try:
                signal = active_reasoner(signal)
            except Exception as exc:  # noqa: BLE001
                signal.risks.append(f"AI explanation degraded: {exc}")
        return signal

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
        if bundle.is_demo:
            v.append("demo data is not a live signal")
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
    ) -> tuple[str, str, list[str], Any]:
        no_trade: list[str] = []
        if levels is None:
            return "WAIT", "NO_TRADE", ["could not build entry levels"], None
        if levels.rr < self.cfg.MIN_RISK_REWARD:
            no_trade.append(f"R:R 1:{levels.rr:.2f} below minimum 1:{self.cfg.MIN_RISK_REWARD:.1f}")
        if rsk > self.cfg.MAX_RISK_SCORE_TO_ENTER:
            no_trade.append(f"risk score {rsk}/10 above max {self.cfg.MAX_RISK_SCORE_TO_ENTER}/10")
        if score.total < self.cfg.QUALITY_MIN:
            no_trade.append(f"quality {score.total:.1f} below min {self.cfg.QUALITY_MIN:.0f}")
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
            is_demo=bundle.is_demo,
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
) -> dict[str, Any]:
    return {
        "timeframes": [v.to_dict() for v in views],
        "derivatives": derivatives.to_dict(),
        "orderflow": orderflow.to_dict(),
        "context": context.to_dict(),
        "regime": regime.to_dict(),
    }


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
