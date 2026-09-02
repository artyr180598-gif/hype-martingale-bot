"""Качество входа: стоп за уровнем, «не догоняй рынок», пауза после стопов.

Появилось после бэктеста на РЕАЛЬНЫХ свечах OKX (300 баров 15m BTC-USDT-SWAP):
двиок нашёл 5 сетапов, 4 из них были выбиты стопом за 2-12 баров, итог −3.276R
(profit factor 0.345). Разбор причин и источники решений — в
``docs/AUDIT.md`` (раунд 8) и ``docs/IMPROVEMENTS_RESEARCH.md``.

Три изменения, которые здесь зафиксированы:
  1. стоп относится ЗА очевидный уровень на ``ATR_STOP_BUFFER`` (иначе его
     выбивают сбором ликвидности — stop hunting);
  2. вход отклоняется, если цена уже ушла от VWAP в сторону сделки дальше
     ``ENTRY_MAX_EXTENSION_ATR`` (не догоняем прошедшее движение);
  3. авто-сигнал гасится после серии стопов по монете (``ALERT_STOPOUT_GUARD``,
     аналог PerformanceFilter/PairInformationFilter в freqtrade).
"""

from __future__ import annotations

import pandas as pd
import pytest

from v3.alerts import stopout_pause
from v3.analysis.levels import build_levels, chase_reason, extension_atr
from v3.analysis.timeframes import build_timeframe_view
from v3.config import SignalConfig


def make_df(n: int = 200, start: float = 100.0) -> pd.DataFrame:
    """Синтетическая серия свечей: ровный тренд вниз, шаг 15m."""
    ts = 1_700_000_000_000 + pd.Series(range(n), dtype="int64") * 900_000
    close = start - pd.Series(range(n), dtype="float64") * 0.05
    return pd.DataFrame(
        {
            "ts": ts,
            "open": close + 0.05,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": 1000.0 + (pd.Series(range(n), dtype="float64") % 7) * 10,
        }
    )


def make_view(**overrides):
    view = build_timeframe_view(make_df(200), "15m")
    for key, value in overrides.items():
        setattr(view, key, value)
    return view


# ── 1. стоп ЗА уровнем, а не ровно на нём ───────────────────────
def test_stop_is_pushed_beyond_structural_support():
    cfg = SignalConfig()
    view = make_view()
    atr = view.atr
    support = 100.0 - 1.2 * atr  # внутри допустимого окна 0.8..3.5 ATR
    view.support = support

    levels = build_levels("LONG", 100.0, atr, view, cfg)
    assert levels is not None
    assert levels.stop_loss == pytest.approx(support - cfg.ATR_STOP_BUFFER * atr, rel=1e-9)
    assert levels.stop_loss < support, "стоп должен быть ЗА уровнем, а не на нём"
    assert any("буфер" in w for w in levels.why)


def test_stop_buffer_applies_to_shorts_and_scenario_levels():
    cfg = SignalConfig()
    view = make_view()
    atr = view.atr
    resistance = 100.0 + 1.2 * atr
    view.resistance = resistance

    short = build_levels("SHORT", 100.0, atr, view, cfg)
    assert short is not None
    assert short.stop_loss == pytest.approx(resistance + cfg.ATR_STOP_BUFFER * atr, rel=1e-9)

    hint = 100.0 - 0.6 * atr
    scenario = build_levels("LONG", 100.0, atr, make_view(), cfg, stop_override=hint)
    assert scenario is not None
    assert scenario.stop_loss == pytest.approx(hint - cfg.ATR_STOP_BUFFER * atr, rel=1e-9)


def test_wider_stop_keeps_the_rr_gate_alive():
    """Шире стоп → дальше цели: R:R не должен провалиться ниже порога."""
    cfg = SignalConfig()
    levels = build_levels("LONG", 100.0, make_view().atr, make_view(), cfg)
    assert levels is not None
    assert levels.rr >= cfg.MIN_RISK_REWARD - 1e-9
    assert len(levels.targets) == 3


# ── 2. «не догоняй рынок» ───────────────────────────────────────
def test_extension_atr_measures_the_move_already_made():
    view = make_view(vwap_dist_pct=-1.0)  # цена на 1% ниже VWAP
    # price 100, ATR выбран так, что 1% = 2.0 ATR
    assert extension_atr("SHORT", 100.0, 0.5, view) == pytest.approx(2.0, abs=1e-6)
    # для LONG движение в другую сторону — догонять нечего
    assert extension_atr("LONG", 100.0, 0.5, view) == 0.0
    # без данных — ноль (фильтр не может выдумать причину)
    assert extension_atr("SHORT", 100.0, 0.5, None) == 0.0
    assert extension_atr("WAIT", 100.0, 0.5, view) == 0.0


def test_chase_reason_blocks_only_extended_entries():
    cfg = SignalConfig()
    cfg.ENTRY_MAX_EXTENSION_ATR = 2.0
    extended = make_view(vwap_dist_pct=-1.0)   # 2.0 ATR при atr=0.5 — ровно на пороге
    assert chase_reason("SHORT", 100.0, 0.5, extended, cfg) == ""

    cfg.ENTRY_MAX_EXTENSION_ATR = 1.0
    reason = chase_reason("SHORT", 100.0, 0.5, extended, cfg)
    assert reason != ""
    assert "не догоняем" in reason
    assert "2.0 ATR" in reason

    # фильтр выключен нулём
    cfg.ENTRY_MAX_EXTENSION_ATR = 0.0
    assert chase_reason("SHORT", 100.0, 0.5, extended, cfg) == ""


def test_new_entry_rules_beat_the_old_ones_on_real_series():
    """A/B на РЕАЛЬНЫХ свечах: старые пороги против новых (одна и та же серия).

    Замерено на дословной серии OKX (299 закрытых баров, warmup 120):
      старые (SL 1.8×ATR, буфер 0, без фильтра) → 5 сделок, WR 20%, PF 0.345,
                                                    −3.276R, просадка 4.36R
      новые (SL 2.2×ATR, буфер 0.25, фильтр 2.0) → 2 сделки, WR 50%, PF 2.662,
                                                    +2.048R, просадка 1.232R
    Сделок мало, поэтому это регрессионный снимок, а не статистика: любое
    изменение порогов входа обязано пересчитать эти числа осознанно.
    """
    from pathlib import Path

    from v3.backtest import run_backtest
    from v3.engine import FuturesSignalEngine
    from v3.replay import load_candles_series

    series = load_candles_series(Path(__file__).parent / "fixtures" / "okx_btcusdt_15m_300.json")
    df, tf, symbol = series["df"], series["tf"], series["symbol"]

    class FakeData:
        mode = "replay"

    def run(**overrides):
        cfg = SignalConfig()
        for key, value in overrides.items():
            setattr(cfg, key, value)
        engine = FuturesSignalEngine(FakeData(), cfg)  # type: ignore[arg-type]
        res = run_backtest(
            engine, symbol, df, entry_tf=tf, medium_tf="1h", macro_tf="4h", warmup=120, cfg=cfg
        )
        assert res.metrics.get("error") is None
        return res

    old = run(ATR_SL_MULTIPLIER=1.8, ATR_STOP_BUFFER=0.0, ENTRY_MAX_EXTENSION_ATR=0.0)
    new = run()

    assert len(old.trades) == 5 and old.metrics["win_rate"] == 20.0
    assert old.metrics["profit_factor"] == pytest.approx(0.345, abs=1e-3)
    assert old.metrics["total_r"] == pytest.approx(-3.276, abs=1e-3)

    assert len(new.trades) == 2 and new.metrics["win_rate"] == 50.0
    assert new.metrics["profit_factor"] == pytest.approx(2.662, abs=1e-3)
    assert new.metrics["total_r"] == pytest.approx(2.048, abs=1e-3)
    assert new.metrics["max_dd_r"] < old.metrics["max_dd_r"]

    # и главное: новое лучше старого на этих же данных
    assert new.metrics["total_r"] > old.metrics["total_r"]
    assert new.metrics["profit_factor"] > old.metrics["profit_factor"]


# ── 3. пауза после серии стопов ─────────────────────────────────
def _outcome(outcome: str, exit_reason: str, exit_at: int) -> dict:
    return {"outcome": outcome, "exit_reason": exit_reason, "exit_at": exit_at}


def test_stopout_pause_needs_a_real_streak():
    cfg = SignalConfig()
    now = 1_788_350_000_000
    hour = 3_600_000

    assert stopout_pause([], cfg, now_ms=now) == (False, "")
    # открытых сделок не считаем: пауза только по ЗАКРЫТЫМ стопам
    assert stopout_pause([_outcome("OPEN", "", 0)] * 5, cfg, now_ms=now) == (False, "")
    # одного стопа мало (порог ALERT_STOPOUT_GUARD = 2)
    one = [_outcome("LOSS", "stop_loss", now - hour), _outcome("WIN", "tp3", now - 2 * hour)]
    assert stopout_pause(one, cfg, now_ms=now)[0] is False

    streak = [
        _outcome("LOSS", "stop_loss", now - hour),
        _outcome("LOSS", "stop_loss", now - 2 * hour),
    ]
    paused, why = stopout_pause(streak, cfg, now_ms=now)
    assert paused is True
    assert "на паузе" in why and "2 стопа подряд" in why

    # выигрыш разрывает серию
    broken = [streak[0], _outcome("WIN", "tp3", now - 2 * hour)]
    assert stopout_pause(broken, cfg, now_ms=now)[0] is False

    # старые стопы уже не держат паузу
    old = [
        _outcome("LOSS", "stop_loss", now - 40 * hour),
        _outcome("LOSS", "stop_loss", now - 41 * hour),
    ]
    assert stopout_pause(old, cfg, now_ms=now)[0] is False

    # защита выключается нулём
    cfg_off = SignalConfig()
    cfg_off.ALERT_STOPOUT_GUARD = 0
    assert stopout_pause(streak, cfg_off, now_ms=now) == (False, "")
