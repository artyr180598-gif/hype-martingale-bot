"""
Бэктестер советника: walk-forward симуляция без заглядывания в будущее.

Принципы (по образцу Jesse — zero look-ahead bias, и nautilus_trader —
backtest/live parity):

1. Единственная точка входа в логику — `AnalysisEngine.analyze_frames()`.
   Live и бэктест вызывают один и тот же код, поэтому «в бэктесте работало,
   в жизни нет» из-за разной логики быть не может.
2. На шаге i в анализ попадают ТОЛЬКО бары <= i, причём для старших
   таймфреймов — только уже ЗАКРЫТЫЕ (open_time + tf <= close_time текущего
   бара). Сделка симулируется начиная с бара i+1.
3. Внутри бара, где задеты и стоп, и цель, считается ХУДШИЙ исход (стоп).
4. Комиссия берётся с входа и с каждого выхода, плюс слиппедж против позиции.
5. Выходы частичные, как советует карточка сделки: 50% на цели 1 (стоп
   переносится в безубыток), 30% на цели 2, остаток на цели 3.
6. Результат измеряется в R (кратностях начального риска) — это не зависит
   от плеча и депозита.

Отдельно считается честный базовый уровень: breakeven win rate, который
нужен при фактическом R:R, и buy&hold за тот же период. Если винрейт бота
ниже breakeven — стратегия убыточна, сколько бы сделок она ни делала.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.analysis.engine import ENTRY_TF, AnalysisEngine, AnalysisResult
from src.core.logging import get_logger
from src.core.timeutil import TIMEFRAME_MS, tf_ms

logger = get_logger("backtest.engine")

RESAMPLE_RULES = {
    "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "1h", "2h": "2h", "3h": "3h", "4h": "4h", "6h": "6h", "12h": "12h",
    "1d": "1D", "3d": "3D", "1w": "1W",
}

# Веса частичных выходов (как в карточке сделки)
TRANCHE_WEIGHTS = (0.5, 0.3, 0.2)


@dataclass
class BacktestConfig:
    entry_tf: str = "1h"
    medium_tf: str = "4h"
    macro_tf: str = "1d"
    warmup_bars: int = 200
    step: int = 1                  # оценивать каждый N-й бар
    max_hold_bars: int = 96        # тайм-аут сделки в барах entry_tf
    limit_wait_bars: int = 24      # сколько баров ждать исполнения лимитника
    fee_rate: float = 0.00055      # taker-комиссия за сторону
    slippage_pct: float = 0.02     # неблагоприятный слиппедж, %
    min_rr: float = 1.5            # не брать сделки с худшим R:R
    # Стоимость сделки в долях 1R. На узком стопе комиссия taker'а (0.055%×2)
    # плюс слиппедж легко съедают 0.5–1.1R, и сделка обречена до открытия.
    # Именно поэтому фильтр по R:R сам по себе ВРЕДИТ: высокое R:R движок
    # получает ужатием стопа, а не расширением цели.
    max_cost_r: float = 0.15
    min_stop_pct: float = 0.6      # стоп уже 0.6% — это шум, а не уровень
    cooldown_bars: int = 12        # пауза после закрытия: не входить в тот же сетап подряд
    min_score: float = 0.0         # фильтр по рейтингу монеты
    min_confidence: float = 0.45   # фильтр по уверенности сценария
    allow_short: bool = True
    staged_exits: bool = True      # частичные выходы; иначе всё на цели 1
    one_trade_at_a_time: bool = True

    def to_dict(self) -> dict:
        return {
            "entry_tf": self.entry_tf, "medium_tf": self.medium_tf, "macro_tf": self.macro_tf,
            "warmup_bars": self.warmup_bars, "step": self.step, "max_hold_bars": self.max_hold_bars,
            "limit_wait_bars": self.limit_wait_bars, "fee_rate": self.fee_rate,
            "slippage_pct": self.slippage_pct, "min_rr": self.min_rr,
            "cooldown_bars": self.cooldown_bars,
            "max_cost_r": self.max_cost_r, "min_stop_pct": self.min_stop_pct,
            "min_confidence": self.min_confidence, "allow_short": self.allow_short,
            "staged_exits": self.staged_exits,
        }


@dataclass
class Trade:
    symbol: str
    direction: str                 # LONG / SHORT
    signal_ts: int
    entry_ts: int
    entry_price: float
    stop: float
    targets: list[float]
    exit_ts: int
    exit_price: float              # средневзвешенная цена выхода
    r_multiple: float              # итог в R (после комиссий)
    pnl_pct: float                 # доходность на объём позиции, %
    bars_held: int
    exit_reason: str
    score: float
    confidence: float
    tranches: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol, "direction": self.direction, "signal_ts": self.signal_ts,
            "entry_ts": self.entry_ts, "entry_price": self.entry_price, "stop": self.stop,
            "targets": self.targets, "exit_ts": self.exit_ts, "exit_price": self.exit_price,
            "r_multiple": round(self.r_multiple, 3), "pnl_pct": round(self.pnl_pct, 3),
            "bars_held": self.bars_held, "exit_reason": self.exit_reason,
            "score": round(self.score, 1), "confidence": round(self.confidence, 2),
        }


@dataclass
class BacktestResult:
    symbol: str
    config: BacktestConfig
    trades: list[Trade] = field(default_factory=list)
    bars_analyzed: int = 0
    signals_generated: int = 0
    signals_skipped: int = 0
    signals_passed_filters: int = 0
    signal_directions: dict = field(default_factory=dict)
    skip_reasons: dict = field(default_factory=dict)
    start_ts: int = 0
    end_ts: int = 0
    is_demo: bool = False
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "config": self.config.to_dict(),
            "bars_analyzed": self.bars_analyzed,
            "signals_generated": self.signals_generated,
            "signals_skipped": self.signals_skipped,
            "signals_passed_filters": self.signals_passed_filters,
            "signal_directions": self.signal_directions,
            "skip_reasons": self.skip_reasons,
            "trades": [t.to_dict() for t in self.trades],
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "is_demo": self.is_demo,
            "metrics": self.metrics,
        }


# ════════════════════════════════════════════════════════════════
#  ПОДГОТОВКА ДАННЫХ
# ════════════════════════════════════════════════════════════════
def resample_ohlcv(df: pd.DataFrame, target_tf: str) -> pd.DataFrame:
    """Агрегирует OHLCV до старшего таймфрейма."""
    rule = RESAMPLE_RULES.get(target_tf)
    if rule is None:
        raise ValueError(f"Неизвестный таймфрейм для агрегации: {target_tf}")
    idx = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    d = df.set_index(idx)
    out = (
        d.resample(rule)
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )
    out["ts"] = (out.index.view("int64") // 1_000_000).astype("int64")
    return out.reset_index(drop=True)[["ts", "open", "high", "low", "close", "volume"]]


def closed_upto(df: pd.DataFrame, ts_ms: int, tf: str, entry_tf: str = ENTRY_TF) -> pd.DataFrame:
    """
    Оставляет только ЗАКРЫТЫЕ бары старшего таймфрейма.

    Бар с open_time t закрыт в момент t + tf_ms(tf). Решения принимаются на
    закрытии текущего бара entry-таймфрейма, то есть в ts_ms + tf_ms(entry_tf).
    Бар старшего ТФ виден только если он закрылся не позже этого момента —
    иначе симуляция подглядывала бы в будущее.
    """
    if df.empty:
        return df
    limit = ts_ms + tf_ms(entry_tf)
    mask = (df["ts"].astype("int64") + tf_ms(tf)) <= limit
    return df[mask]


# ════════════════════════════════════════════════════════════════
#  СИМУЛЯЦИЯ СДЕЛКИ
# ════════════════════════════════════════════════════════════════
def simulate_trade(
    symbol: str,
    direction: str,
    plan,
    future: pd.DataFrame,
    signal_idx: int,
    signal_ts: int,
    cfg: BacktestConfig,
    score: float = 0.0,
    confidence: float = 0.0,
) -> Trade | None:
    """
    Прогоняет сделку вперёд по барам future[signal_idx+1:].
    Возвращает Trade или None, если вход не состоялся.
    """
    is_long = direction == "LONG"
    slip = cfg.slippage_pct / 100.0
    lo_zone, hi_zone = plan.entry_zone
    targets = list(plan.targets)
    stop0 = plan.stop_loss

    # ── 1. Ищем исполнение входа ──
    entry_idx = None
    entry_price = None
    start = signal_idx + 1
    for j in range(start, min(start + cfg.limit_wait_bars + 1, len(future))):
        bar = future.iloc[j]
        if is_long:
            # лимитник на покупку исполняется, когда цена спустилась в зону
            if bar["low"] <= hi_zone:
                entry_idx = j
                # цена исполнения: не лучше границы зоны, с учётом слиппеджа
                fill = min(hi_zone, bar["open"])
                entry_price = fill * (1 - slip)
                break
        else:
            if bar["high"] >= lo_zone:
                entry_idx = j
                fill = max(lo_zone, bar["open"])
                entry_price = fill * (1 + slip)
                break
    if entry_idx is None or entry_price is None or entry_price <= 0:
        return None

    # Гэп сквозь стоп: лимитник исполнился, но цена уже ЗА стопом.
    # Такая позиция не имеет смысла — считаем мгновенный стоп-аут по цене
    # открытия. Иначе бэктест рисовал бы «стопы в плюс» там, где их не было.
    crossed = (entry_price <= stop0) if is_long else (entry_price >= stop0)
    if crossed:
        fee_r = cfg.fee_rate * 2 * entry_price / abs(entry_price - stop0)
        gap_sign = 1.0 if is_long else -1.0
        loss = gap_sign * (stop0 - entry_price) / abs(entry_price - stop0)
        return Trade(
            symbol=symbol, direction=direction, signal_ts=signal_ts,
            entry_ts=int(future.iloc[entry_idx]["ts"]), entry_price=entry_price,
            stop=stop0, targets=targets, exit_ts=int(future.iloc[entry_idx]["ts"]),
            exit_price=entry_price, r_multiple=-(abs(loss) + fee_r),
            pnl_pct=0.0, bars_held=0, exit_reason="gap_stop",
            score=score, confidence=confidence,
            tranches=[{"weight": 1.0, "price": entry_price, "reason": "gap_stop"}],
        )

    risk = abs(entry_price - stop0)
    if risk <= 0:
        return None

    sign = 1.0 if is_long else -1.0
    stop = stop0
    remaining = 1.0
    r_total = 0.0
    tranches: list[dict] = []
    exit_idx = None
    exit_reason = "timeout"
    exit_price = None
    be_moved = False

    weights = TRANCHE_WEIGHTS if (cfg.staged_exits and len(targets) >= 3) else (1.0,)
    active_targets = targets[: len(weights)]
    done = [False] * len(active_targets)

    for j in range(entry_idx, min(entry_idx + cfg.max_hold_bars + 1, len(future))):
        bar = future.iloc[j]
        hi, lo = float(bar["high"]), float(bar["low"])

        # ── сначала проверяем стоп (пессимистично: худший исход внутри бара) ──
        stopped = (lo <= stop) if is_long else (hi >= stop)
        if stopped and remaining > 0:
            fill = stop * (1 - slip) if is_long else stop * (1 + slip)
            r_total += _tranche_r(remaining, entry_price, fill, risk, sign, cfg.fee_rate)
            tranches.append({"weight": remaining, "price": fill, "reason": "stop"})
            exit_idx, exit_price, exit_reason = j, fill, "stop_loss" if not be_moved else "breakeven"
            remaining = 0.0
            break

        # ── затем цели по порядку; каждая исполняется ровно один раз ──
        for k, tgt in enumerate(active_targets):
            if done[k]:
                continue
            hit = (hi >= tgt) if is_long else (lo <= tgt)
            if not hit:
                continue
            w = weights[k]
            fill = tgt * (1 - slip) if is_long else tgt * (1 + slip)
            r_total += _tranche_r(w, entry_price, fill, risk, sign, cfg.fee_rate)
            tranches.append({"weight": w, "price": fill, "reason": f"target_{k + 1}"})
            remaining -= w
            done[k] = True
            if k == 0 and cfg.staged_exits:
                # после цели 1 стоп уходит в безубыток
                stop = entry_price
                be_moved = True
            if remaining <= 1e-9:
                exit_idx, exit_price, exit_reason = j, fill, "target"
                break
        if remaining <= 1e-9:
            break

    if remaining > 1e-9:
        last_idx = min(entry_idx + cfg.max_hold_bars, len(future) - 1)
        fill = float(future.iloc[last_idx]["close"])
        fill = fill * (1 - slip) if is_long else fill * (1 + slip)
        r_total += _tranche_r(remaining, entry_price, fill, risk, sign, cfg.fee_rate)
        tranches.append({"weight": remaining, "price": fill, "reason": "timeout"})
        exit_idx, exit_price, exit_reason = last_idx, fill, "timeout"

    if exit_price is None:
        return None

    pnl_pct = r_total * (risk / entry_price * 100.0)
    return Trade(
        symbol=symbol,
        direction=direction,
        signal_ts=signal_ts,
        entry_ts=int(future.iloc[entry_idx]["ts"]),
        entry_price=entry_price,
        stop=stop0,
        targets=targets,
        exit_ts=int(future.iloc[exit_idx]["ts"]),
        exit_price=exit_price,
        r_multiple=r_total,
        pnl_pct=pnl_pct,
        bars_held=exit_idx - entry_idx,
        exit_reason=exit_reason,
        score=score,
        confidence=confidence,
        tranches=tranches,
    )


def cost_in_r(entry: float, stop: float, cfg: "BacktestConfig") -> float:
    """
    Сколько R стоит открыть и закрыть позицию: комиссия за обе стороны
    плюс слиппедж на входе и выходе, делённые на размер риска.

    Пример: BTC 30 000, стоп 0.16% (48 п.), комиссия 0.055%×2 + слиппедж 0.04%
    → 0.15% × 30000 / 48 ≈ 0.94R. Сделка убыточна ещё до входа.
    """
    risk = abs(entry - stop)
    if risk <= 0:
        return float("inf")
    return (2 * cfg.fee_rate + 2 * cfg.slippage_pct / 100.0) * entry / risk


def _tranche_r(weight: float, entry: float, exit_price: float, risk: float, sign: float, fee: float) -> float:
    """R-доля траншеи с учётом комиссии за вход и выход."""
    gross = sign * (exit_price - entry) / risk
    fee_r = fee * (entry + exit_price) / risk
    return weight * (gross - fee_r)


# ════════════════════════════════════════════════════════════════
#  ОСНОВНОЙ ЦИКЛ
# ════════════════════════════════════════════════════════════════
class Backtester:
    def __init__(self, engine: AnalysisEngine, cfg: BacktestConfig | None = None):
        self.engine = engine
        self.cfg = cfg or BacktestConfig()

    async def run(
        self,
        symbol: str,
        df_entry: pd.DataFrame,
        df_medium: pd.DataFrame | None = None,
        df_macro: pd.DataFrame | None = None,
    ) -> BacktestResult:
        """
        df_entry — история на entry-таймфрейме (сортировка по времени).
        df_medium/df_macro — если не заданы, агрегируются из df_entry.
        """
        cfg = self.cfg
        symbol = symbol.upper()
        df = df_entry.sort_values("ts").reset_index(drop=True)
        if len(df) <= cfg.warmup_bars + 5:
            raise ValueError(
                f"{symbol}: нужно минимум {cfg.warmup_bars + 5} баров {cfg.entry_tf}, дано {len(df)}"
            )

        med = df_medium if df_medium is not None else resample_ohlcv(df, cfg.medium_tf)
        mac = df_macro if df_macro is not None else resample_ohlcv(df, cfg.macro_tf)

        result = BacktestResult(
            symbol=symbol,
            config=cfg,
            start_ts=int(df["ts"].iloc[0]),
            end_ts=int(df["ts"].iloc[-1]),
            is_demo=bool(getattr(self.engine.source, "is_demo", False)),
        )

        busy_until = -1  # индекс бара, до которого позиция ещё открыта
        for i in range(cfg.warmup_bars, len(df) - 2, cfg.step):
            if cfg.one_trade_at_a_time and i < busy_until:
                continue
            ts = int(df["ts"].iloc[i])

            fe = df.iloc[: i + 1]
            fm = closed_upto(med, ts, cfg.medium_tf, cfg.entry_tf)
            fa = closed_upto(mac, ts, cfg.macro_tf, cfg.entry_tf)
            if len(fm) < 30:
                continue

            try:
                res: AnalysisResult = self.engine.analyze_frames(symbol, fe, fm, fa)
            except Exception:  # noqa: BLE001
                continue

            if res.direction not in ("LONG", "SHORT") or res.plan is None:
                continue
            result.signal_directions[res.direction] = (
                result.signal_directions.get(res.direction, 0) + 1
            )
            if res.direction == "SHORT" and not cfg.allow_short:
                continue

            result.signals_generated += 1
            skip = self._skip_reason(res)
            if skip is None:
                result.signals_passed_filters += 1
            if skip:
                result.signals_skipped += 1
                key = skip.split(" ")[0]
                result.skip_reasons[key] = result.skip_reasons.get(key, 0) + 1
                continue

            trade = simulate_trade(
                symbol=symbol,
                direction=res.direction,
                plan=res.plan,
                future=df,
                signal_idx=i,
                signal_ts=ts,
                cfg=cfg,
                score=res.score,
                confidence=res.confidence,
            )
            if trade is None:
                result.signals_skipped += 1
                result.skip_reasons["вход не исполнен"] = (
                    result.skip_reasons.get("вход не исполнен", 0) + 1
                )
                continue

            result.trades.append(trade)
            # помечаем, до какого бара позиция была открыта
            end_idx = next(
                (k for k in range(len(df)) if int(df["ts"].iloc[k]) >= trade.exit_ts), len(df) - 1
            )
            busy_until = end_idx + cfg.cooldown_bars

        result.bars_analyzed = len(range(cfg.warmup_bars, len(df) - 2, cfg.step))

        from src.backtest.metrics import compute_metrics

        result.metrics = compute_metrics(result, df)
        return result

    def _skip_reason(self, res: AnalysisResult) -> str | None:
        cfg = self.cfg
        plan = res.plan
        if plan is None:
            return "нет плана"
        lo, hi = plan.entry_zone
        ref = (lo + hi) / 2.0
        stop_pct = 100.0 * abs(ref - plan.stop_loss) / ref if ref > 0 else 0.0
        if stop_pct < cfg.min_stop_pct:
            return f"стоп {stop_pct:.2f}% < {cfg.min_stop_pct}% (шум)"
        cost_r = cost_in_r(ref, plan.stop_loss, cfg)
        if cost_r > cfg.max_cost_r:
            return f"издержки {cost_r:.2f}R > {cfg.max_cost_r}R"
        if plan.rr < cfg.min_rr:
            return f"R:R {plan.rr:.2f} < {cfg.min_rr}"
        if res.confidence < cfg.min_confidence:
            return f"уверенность {res.confidence:.2f} < {cfg.min_confidence}"
        if res.score < cfg.min_score:
            return f"рейтинг {res.score:.0f} < {cfg.min_score}"
        return None
