"""
Оркестрация бэктеста: достать историю → прогнать бэктестер → отдать отчёт.

Живой режим и бэктест идут через один и тот же `AnalysisEngine.analyze_frames`,
поэтому расхождение «в бэктесте плюс, в жизни минус» из-за разной логики
исключено (backtest/live parity).
"""

from __future__ import annotations

import time

from src.analysis.engine import AnalysisEngine
from src.backtest.engine import BacktestConfig, Backtester
from src.core.errors import AdvisorError, DataSourceError
from src.core.logging import get_logger
from src.core.timeutil import tf_ms

logger = get_logger("backtest.service")

# Сколько баров истории нужно на таймфрейме входа, чтобы хватило на warmup
WARMUP_BARS = 200


def bars_for(period_days: float, entry_tf: str, warmup: int = WARMUP_BARS) -> int:
    """Сколько баров entry-таймфрейма покрывает период + прогрев индикаторов."""
    step = tf_ms(entry_tf)
    if step <= 0:
        raise AdvisorError(f"Неизвестный таймфрейм: {entry_tf}")
    need = int(period_days * 86_400_000 / step) + warmup
    return max(warmup + 60, min(need, 20_000))


async def run_backtest(
    source,
    engine: AnalysisEngine,
    symbol: str,
    cfg: BacktestConfig | None = None,
    period_days: float = 30.0,
    bars: int | None = None,
):
    """
    Возвращает BacktestResult.

    period_days используется, если bars не задан. bars жёстко фиксирует
    глубину истории (полезно для CLI: --bars 3000).
    """
    cfg = cfg or BacktestConfig()
    symbol = symbol.upper()
    want = bars if bars is not None else bars_for(period_days, cfg.entry_tf, cfg.warmup_bars)

    t0 = time.time()
    try:
        df = await source.get_history(symbol, cfg.entry_tf, want)
    except DataSourceError as e:
        raise AdvisorError(f"Не удалось получить историю {symbol} на {cfg.entry_tf}: {e}") from e

    if df is None or len(df) <= cfg.warmup_bars + 5:
        raise AdvisorError(
            f"{symbol}: история на {cfg.entry_tf} слишком короткая — "
            f"{0 if df is None else len(df)} баров, нужно больше {cfg.warmup_bars + 5}."
        )

    logger.info("Бэктест %s: %d баров %s за %.1f c", symbol, len(df), cfg.entry_tf, time.time() - t0)
    result = await Backtester(engine, cfg).run(symbol, df)
    result.metrics["history_bars"] = len(df)
    result.metrics["fetch_seconds"] = round(time.time() - t0, 1)
    result.metrics["run_seconds"] = round(time.time() - t0, 1)
    return result
