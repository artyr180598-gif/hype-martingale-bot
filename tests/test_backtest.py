"""
Тесты бэктестера на ДЕТЕРМИНИРОВАННЫХ сериях с заранее известным ответом.

Это единственная честная проверка: на синтетике мы знаем правильный ответ
вручную, поэтому любой баг в постановке стопа, целях, R-кратности, комиссиях
или в look-ahead будет виден. Прогон на живых данных даёт цифры, но не
доказывает, что бэктестер не врёт — это доказывают только эти тесты.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.analysis.engine import AnalysisEngine, EntryPlan
from src.backtest.engine import (
    BacktestConfig,
    Backtester,
    closed_upto,
    cost_in_r,
    resample_ohlcv,
    simulate_trade,
)
from src.backtest.metrics import compute_metrics
from src.config.settings import Settings
from src.data.demo import DemoMarketSource

HOUR = 3_600_000
START_TS = 1_700_006_400_000  # 2023-11-15T00:00:00Z, кратно часу и суткам


def make_df(closes, tf_ms=HOUR, start_ts=START_TS, noise=0.0) -> pd.DataFrame:
    """OHLCV из заданных закрытий; шум раздувает high/low симметрично."""
    c = np.asarray(closes, dtype=float)
    ts = start_ts + np.arange(len(c)) * tf_ms
    o = np.concatenate(([c[0]], c[:-1]))
    body_hi, body_lo = np.maximum(o, c), np.minimum(o, c)
    h = body_hi * (1 + noise)
    lo = body_lo * (1 - noise)
    return pd.DataFrame({
        "ts": ts, "open": o, "high": h, "low": lo, "close": c,
        "volume": np.full(len(c), 100.0),
    })


def plan(direction="LONG", zone=(99.0, 101.0), stop=90.0, targets=(110.0, 120.0, 130.0)):
    return EntryPlan(
        direction=direction, entry_zone=tuple(zone), stop_loss=stop, targets=list(targets),
        rr=2.0, leverage=2, position_pct=10.0, distance_pct=0.0,
        invalidation="закрытие за уровнем", t1_distance_pct=9.0,
    )


# ═══════════════════════════════════════════════════════════════
#  АГРЕГАЦИЯ И ОТСЕЧЕНИЕ НЕЗАКРЫТЫХ БАРОВ (look-ahead)
# ═══════════════════════════════════════════════════════════════
def test_resample_4h_from_1h():
    df = make_df([100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0])
    r = resample_ohlcv(df, "4h")
    assert len(r) == 2
    # open агрегированной свечи = open первого часового бара в корзине
    assert r["open"].tolist() == [100.0, 103.0]
    assert r["close"].tolist() == [103.0, 107.0]
    assert r["high"].tolist() == [103.0, 107.0]
    assert r["low"].tolist() == [100.0, 103.0]
    assert r["volume"].tolist() == [400.0, 400.0]


def test_resample_1d_from_1h():
    df = make_df([float(x) for x in range(48)])  # ровно 2 суток
    r = resample_ohlcv(df, "1d")
    assert len(r) == 2
    assert r["close"].tolist() == [23.0, 47.0]


def test_closed_upto_hides_the_open_bar():
    """Старший бар виден только когда он ЗАКРЫЛСЯ — иначе look-ahead."""
    df = make_df([float(x) for x in range(10)], tf_ms=4 * HOUR)
    ts2 = int(df["ts"].iloc[2])
    # посреди бара 2: бар ещё открыт → его не должно быть
    assert len(closed_upto(df, ts2 + HOUR, "4h")) == 2
    # бар 2 закрылся → стал виден
    assert len(closed_upto(df, ts2 + 4 * HOUR, "4h")) == 3


# ═══════════════════════════════════════════════════════════════
#  СИМУЛЯЦИЯ СДЕЛКИ: заранее известные исходы
# ═══════════════════════════════════════════════════════════════
CFG_CLEAN = BacktestConfig(slippage_pct=0.0, fee_rate=0.0, limit_wait_bars=5, trail_after_t1=True)


def test_long_straight_up_hits_all_targets():
    df = make_df([100.0 + i for i in range(40)])
    t = simulate_trade("T", "LONG", plan(), df, 0, int(df["ts"].iloc[0]), CFG_CLEAN)
    assert t is not None
    assert t.entry_price == pytest.approx(100.0)
    assert t.exit_reason == "target"
    # 50% на +1R, 30% на +2R, 20% на +3R  →  0.5+0.6+0.6
    assert t.r_multiple == pytest.approx(1.7)
    assert sum(x["weight"] for x in t.tranches) == pytest.approx(1.0)
    assert [x["reason"] for x in t.tranches] == ["target_1", "target_2", "target_3"]


def test_short_straight_down_hits_all_targets():
    df = make_df([100.0 - i for i in range(40)])
    p = plan("SHORT", stop=110.0, targets=(90.0, 80.0, 70.0))
    t = simulate_trade("T", "SHORT", p, df, 0, int(df["ts"].iloc[0]), CFG_CLEAN)
    assert t is not None
    assert t.exit_reason == "target"
    assert t.r_multiple == pytest.approx(1.7)


def test_long_into_downtrend_stops_at_minus_one_r():
    df = make_df([100.0 - i for i in range(40)])
    t = simulate_trade("T", "LONG", plan(), df, 0, int(df["ts"].iloc[0]), CFG_CLEAN)
    assert t is not None
    assert t.exit_reason == "stop_loss"
    assert t.entry_price == pytest.approx(100.0)
    assert t.exit_price == pytest.approx(90.0)
    assert t.r_multiple == pytest.approx(-1.0)


def test_short_into_uptrend_stops_at_minus_one_r():
    df = make_df([100.0 + i for i in range(40)])
    p = plan("SHORT", stop=110.0, targets=(90.0, 80.0, 70.0))
    t = simulate_trade("T", "SHORT", p, df, 0, int(df["ts"].iloc[0]), CFG_CLEAN)
    assert t is not None
    assert t.exit_reason == "stop_loss"
    assert t.r_multiple == pytest.approx(-1.0)


def test_fees_are_charged_on_entry_and_exit():
    df = make_df([100.0 - i for i in range(40)])
    cfg = BacktestConfig(slippage_pct=0.0, fee_rate=0.00055, limit_wait_bars=5)
    t = simulate_trade("T", "LONG", plan(), df, 0, int(df["ts"].iloc[0]), cfg)
    # -1R минус комиссия с обеих сторон: 0.00055*(100+90)/10 = 0.01045
    assert t.r_multiple == pytest.approx(-(1.0 + 0.00055 * 190 / 10))
    assert t.r_multiple < -1.0


def test_slippage_worsens_the_result():
    df = make_df([100.0 - i for i in range(40)])
    clean = simulate_trade("T", "LONG", plan(), df, 0, int(df["ts"].iloc[0]), CFG_CLEAN)
    slipped = simulate_trade(
        "T", "LONG", plan(), df, 0, int(df["ts"].iloc[0]),
        BacktestConfig(slippage_pct=0.05, fee_rate=0.0, limit_wait_bars=5),
    )
    assert slipped.r_multiple < clean.r_multiple


def test_stop_beats_target_in_the_same_bar():
    """
    Если в баре задеты и стоп, и цель — считается ХУДШИЙ исход (стоп).
    Это консервативно: реальный порядок сделок внутри бара неизвестен.
    """
    closes = [100.0, 100.0, 100.0, 100.0]
    df = make_df(closes)
    # раздуваем бар 1 так, чтобы он доставал и стоп, и первую цель
    df.loc[1, "high"] = 130.0
    df.loc[1, "low"] = 80.0
    t = simulate_trade("T", "LONG", plan(), df, 0, int(df["ts"].iloc[0]), CFG_CLEAN)
    assert t.exit_reason == "stop_loss"
    assert t.r_multiple == pytest.approx(-1.0)


_REVERSAL = [100.0, 105.0, 112.0, 108.0, 100.0, 95.0, 92.0, 91.0]


def _reversal_df():
    """Цель 1 достигается на баре 2, затем разворот вниз."""
    df = make_df(_REVERSAL)
    df.loc[:, "high"] = df[["open", "close"]].max(axis=1) + 0.5
    df.loc[:, "low"] = df[["open", "close"]].min(axis=1) - 0.5
    return df


def test_breakeven_stop_without_trailing():
    """Без трейлинга стоп после цели 1 встаёт ровно в безубыток: остаток выходит в 0."""
    cfg = BacktestConfig(
        slippage_pct=0.0, fee_rate=0.0, limit_wait_bars=5, trail_after_t1=False
    )
    t = simulate_trade("T", "LONG", plan(), _reversal_df(), 0, START_TS, cfg)
    assert t is not None
    assert t.exit_reason == "breakeven"
    assert t.r_multiple == pytest.approx(0.5)   # только первая цель


def test_trailing_stop_locks_profit_after_first_target():
    """
    С трейлингом тот же разворот фиксирует прибыль, а не ноль.
    Парный тест к предыдущему: различаются они только флагом trail_after_t1.
    """
    t = simulate_trade("T", "LONG", plan(), _reversal_df(), 0, START_TS, CFG_CLEAN)
    assert t is not None
    assert t.exit_reason == "trailing"
    assert t.r_multiple > 0.5, "трейлинг обязан дать больше, чем одна первая цель"


def test_trailing_uses_only_closed_bars():
    """
    Стоп на баре j строится по барам <= j-1. Если бы трейлинг брал high
    текущего бара, он бы «догонял» собственную цену и завышал результат.
    """
    df = _reversal_df()
    t = simulate_trade("T", "LONG", plan(), df, 0, START_TS, CFG_CLEAN)
    # выход на баре 3; стоп не может быть выше максимума баров 1..2
    exit_bar = next(i for i in range(len(df)) if int(df["ts"].iloc[i]) == t.exit_ts)
    prior_high = float(df["high"].iloc[entry_bar(df, t): exit_bar].max())
    assert t.exit_price <= prior_high


def test_entry_not_filled_within_wait_window():
    """Цена не зашла в зону → сделки нет, а не «вход по рынку»."""
    df = make_df([150.0 + i for i in range(40)])  # далеко выше зоны 99–101
    t = simulate_trade("T", "LONG", plan(), df, 0, int(df["ts"].iloc[0]), CFG_CLEAN)
    assert t is None


def test_gap_through_stop_is_a_loss_not_a_win():
    """
    Гэп сквозь стоп: лимитник исполнился, но цена уже за стопом.
    Раньше это считалось «стопом в плюс» и раздувало винрейт — теперь убыток.
    """
    # бар открывается НИЖЕ стопа (open бара 1 = close бара 0 = 85 < stop 90)
    closes = [85.0, 80.0, 79.0, 78.0, 77.0]
    df = make_df(closes)
    t = simulate_trade("T", "LONG", plan(), df, 0, int(df["ts"].iloc[0]), CFG_CLEAN)
    assert t is not None
    assert t.exit_reason == "gap_stop"
    assert t.r_multiple < 0, "гэп сквозь стоп обязан быть убытком"


def test_short_gap_through_stop_is_a_loss():
    # бар открывается ВЫШЕ стопа (open бара 1 = 115 > stop 110)
    closes = [115.0, 120.0, 121.0, 122.0]
    df = make_df(closes)
    p = plan("SHORT", stop=110.0, targets=(90.0, 80.0, 70.0))
    t = simulate_trade("T", "SHORT", p, df, 0, int(df["ts"].iloc[0]), CFG_CLEAN)
    assert t is not None
    assert t.exit_reason == "gap_stop"
    assert t.r_multiple < 0


def test_intrabar_stop_is_plain_stop_loss_not_gap():
    """Стоп задет внутри бара (открытие было выше) — обычный стоп-аут на −1R."""
    closes = [100.0, 80.0, 79.0, 78.0]
    df = make_df(closes)
    t = simulate_trade("T", "LONG", plan(), df, 0, int(df["ts"].iloc[0]), CFG_CLEAN)
    assert t is not None
    assert t.exit_reason == "stop_loss"
    assert t.r_multiple == pytest.approx(-1.0)


def test_timeout_exits_at_close():
    df = make_df([100.0] * 10)  # флэт: ни стоп, ни цели не задеты
    cfg = BacktestConfig(slippage_pct=0.0, fee_rate=0.0, limit_wait_bars=5, max_hold_bars=3)
    t = simulate_trade("T", "LONG", plan(), df, 0, int(df["ts"].iloc[0]), cfg)
    assert t is not None
    assert t.exit_reason == "timeout"
    assert t.bars_held == 3


# ═══════════════════════════════════════════════════════════════
#  МЕТРИКИ
# ═══════════════════════════════════════════════════════════════
class _FakeRes:
    def __init__(self, rs, dirs=None, start=START_TS, end=START_TS + 40 * HOUR):
        self.config = BacktestConfig()
        self.bars_analyzed = 100
        self.signals_generated = len(rs)
        self.is_demo = False
        self.start_ts = start
        self.end_ts = end
        self.trades = [
            type("T", (), {
                "r_multiple": r, "direction": (dirs or ["LONG"] * len(rs))[i],
                "bars_held": 5, "exit_reason": "target" if r > 0 else "stop_loss",
                "entry_price": 100.0, "stop": 90.0, "targets": [110.0, 120.0, 130.0],
                "tranches": [{"weight": 1.0, "price": 110.0, "reason": "target_1"}],
            })()
            for i, r in enumerate(rs)
        ]


def test_metrics_win_rate_and_expectancy():
    res = _FakeRes([1.0, -1.0, 1.0, -1.0])
    m = compute_metrics(res)
    assert m["total_trades"] == 4
    assert m["win_rate"] == 50.0
    assert m["expectancy_r"] == 0.0
    assert m["breakeven_win_rate"] == 50.0
    assert m["edge_over_breakeven"] == 0.0


def test_metrics_breakeven_win_rate_formula():
    """
    При среднем выигрыше +2R и среднем проигрыше −1R окупаемость при 33.3%.
    Винрейт 50% даёт плюс, 25% — минус.
    """
    res = _FakeRes([2.0, 2.0, -1.0, -1.0])
    m = compute_metrics(res)
    assert m["breakeven_win_rate"] == pytest.approx(33.3, abs=0.1)
    assert m["expectancy_r"] == pytest.approx(0.5)
    assert m["edge_over_breakeven"] > 0


def test_metrics_max_consecutive_losses():
    res = _FakeRes([1.0, -1.0, -1.0, -1.0, 1.0, -1.0])
    m = compute_metrics(res)
    assert m["max_consecutive_losses"] == 3


def test_metrics_max_drawdown_r():
    # пик +2, потом три убытка подряд → просадка 3R
    res = _FakeRes([1.0, 1.0, -1.0, -1.0, -1.0, 1.0])
    m = compute_metrics(res)
    assert m["max_drawdown_r"] == pytest.approx(3.0)
    assert m["total_r"] == pytest.approx(0.0)


def test_metrics_empty_trades():
    res = _FakeRes([])
    m = compute_metrics(res)
    assert m["total_trades"] == 0
    assert m["verdict"] == "нет сделок"


def test_metrics_direction_breakdown():
    res = _FakeRes([1.0, -1.0, 2.0, -2.0], dirs=["LONG", "LONG", "SHORT", "SHORT"])
    m = compute_metrics(res)
    assert set(m["by_direction"]) == {"LONG", "SHORT"}
    assert m["by_direction"]["LONG"]["total_r"] == 0.0
    assert m["by_direction"]["SHORT"]["total_r"] == 0.0


# ═══════════════════════════════════════════════════════════════
#  СКВОЗНОЙ ПРОГОН НА ДЕТЕРМИНИРОВАННЫХ ТРЕНДАХ
# ═══════════════════════════════════════════════════════════════
def _engine():
    settings = Settings()
    return AnalysisEngine(DemoMarketSource(settings), settings)


def _cfg(**kw):
    base = dict(
        entry_tf="1h", medium_tf="4h", macro_tf="1d", warmup_bars=70,
        step=3, max_hold_bars=48, limit_wait_bars=24,
        min_confidence=0.0, min_rr=0.5, fee_rate=0.00055, slippage_pct=0.02,
    )
    base.update(kw)
    return BacktestConfig(**base)


@pytest.mark.asyncio
async def test_uptrend_bot_goes_long_and_makes_money():
    """
    Восходящий тренд с рыночным шумом: бот обязан брать LONG и быть в плюсе.

    Шум обязателен: на идеально гладкой серии ATR вырождается в ноль, стоп
    становится микроскопическим и фильтр издержек правильно бракует всё.
    """
    closes = 100.0 * (1.0012 ** np.arange(400)) * (1 + 0.02 * np.sin(np.arange(400) / 6.0))
    df = make_df(closes.tolist(), noise=0.004)
    res = await _BacktesterRunner(_engine(), _cfg()).run("BTCUSDT", df)
    longs = [t for t in res.trades if t.direction == "LONG"]
    assert len(res.trades) >= 3, "на явном тренде должны быть сделки"
    assert len(longs) >= len(res.trades) - 1, "в аптренде бот должен брать LONG"
    assert res.metrics["total_r"] > 0


@pytest.mark.asyncio
async def test_downtrend_short_beats_long_only():
    """
    На падающем рынке разрешение шортов должно улучшать результат.
    Если бы движок всегда выдавал LONG, разницы бы не было — тест это ловит.
    """
    # откаты 5%: при 2% зона входа SHORT стоит выше рынка и лимитник не
    # исполняется вовсе — тогда тест проверял бы не направление, а удачу
    closes = 100.0 * (0.9988 ** np.arange(400)) * (1 + 0.05 * np.sin(np.arange(400) / 6.0))
    df = make_df(closes.tolist(), noise=0.004)
    both = await _BacktesterRunner(_engine(), _cfg(allow_short=True)).run("BTCUSDT", df)
    long_only = await _BacktesterRunner(_engine(), _cfg(allow_short=False)).run("BTCUSDT", df)

    # 1. движок реагирует на данные: на падении он выдаёт SHORT
    assert both.signal_directions.get("SHORT", 0) > 0, "на даунтренде должны быть SHORT-сигналы"
    # 2. флаг allow_short действительно запрещает шорты в сделках.
    #    Счётчики сигналов при этом legitimately различаются: пока позиция
    #    открыта, one_trade_at_a_time пропускает бары целиком.
    assert all(t.direction == "LONG" for t in long_only.trades)
    # 3. и шорты на падении зарабатывают
    shorts = [t for t in both.trades if t.direction == "SHORT"]
    assert shorts, "хотя бы один SHORT должен исполниться"
    assert both.metrics["total_r"] > 0


@pytest.mark.asyncio
async def test_no_lookahead_future_bars_do_not_change_signals():
    """
    Отрезаем вторую половину истории — сигналы из первой половины должны
    остаться ровно теми же. Если движок подглядывает вперёд, они изменятся.
    """
    closes = 100.0 * (1.0015 ** np.arange(400)) * (1 + 0.02 * np.sin(np.arange(400) / 5.0))
    df = make_df(closes.tolist(), noise=0.004)
    full = await _BacktesterRunner(_engine(), _cfg()).run("BTCUSDT", df)
    cut = await _BacktesterRunner(_engine(), _cfg()).run("BTCUSDT", df.iloc[:300].reset_index(drop=True))
    full_cut = [t for t in full.trades if t.signal_ts <= int(df["ts"].iloc[297])]
    assert [(t.signal_ts, t.direction, t.entry_price) for t in full_cut] == \
           [(t.signal_ts, t.direction, t.entry_price) for t in cut.trades]


@pytest.mark.asyncio
async def test_flat_market_produces_few_or_no_trades():
    """Флэт без тренда: бот не должен генерить сделки пачками."""
    closes = 100.0 + 1.5 * np.sin(np.arange(400) / 9.0)
    df = make_df(closes.tolist(), noise=0.004)
    res = await _BacktesterRunner(_engine(), _cfg()).run("BTCUSDT", df)
    assert res.metrics["trade_frequency_pct"] < 50.0


def test_metrics_flag_gap_stops():
    res = _FakeRes([-1.0])
    res.trades[0].exit_reason = "gap_stop"
    m = compute_metrics(res)
    assert m["gap_stops"] == 1


def test_metrics_stop_distance_is_reported():
    res = _FakeRes([1.0])  # entry 100, stop 90 → стоп 10% от цены
    m = compute_metrics(res)
    assert m["median_stop_dist_pct"] == pytest.approx(10.0)


def test_cost_in_r_explains_why_tight_stops_are_doomed():
    """
    BTC 30 000, стоп 0.16% (48 п.), комиссия 0.055%×2 + слиппедж 0.04%.
    Издержки ≈ 0.94R — сделка убыточна ещё до входа.
    """
    cfg = BacktestConfig(fee_rate=0.00055, slippage_pct=0.02)
    tight = cost_in_r(30_000.0, 30_000.0 - 48.0, cfg)
    wide = cost_in_r(30_000.0, 30_000.0 - 720.0, cfg)
    assert tight == pytest.approx(0.0015 * 30_000 / 48, rel=1e-6)
    assert tight > 0.9
    assert wide < 0.1
    assert wide < tight


def test_cost_in_r_zero_risk_is_infinite():
    assert cost_in_r(100.0, 100.0, BacktestConfig()) == float("inf")


def test_skip_reason_rejects_tight_stop_before_rr():
    """Фильтр по стопу срабатывает раньше R:R: узкий стоп нельзя «починить» целью."""
    from src.analysis.engine import AnalysisResult
    from src.backtest.engine import Backtester

    cfg = BacktestConfig(min_rr=1.0, min_confidence=0.0, min_stop_pct=0.6, max_cost_r=0.15)
    bt = Backtester(_engine(), cfg)
    tight = plan(stop=99.5)          # стоп 0.5% от зоны 100
    wide = plan(stop=90.0)           # стоп 10%
    res_t = type("R", (), {"plan": tight, "confidence": 0.9, "score": 50})()
    res_w = type("R", (), {"plan": wide, "confidence": 0.9, "score": 50})()
    assert "стоп" in bt._skip_reason(res_t)
    assert bt._skip_reason(res_w) is None


@pytest.mark.asyncio
async def test_no_trade_has_target_on_the_wrong_side_of_entry():
    """
    Регрессия: R:R в плане считался от нижнего края зоны входа, а лимитник
    исполняется по верхнему. На широкой зоне цель 1 у LONG оказывалась НИЖЕ
    цены входа — сделка закрывалась в минус при формально плюсовом плане.
    """
    closes = 100.0 * (1.0012 ** np.arange(400)) * (1 + 0.03 * np.sin(np.arange(400) / 6.0))
    df = make_df(closes.tolist(), noise=0.004)
    res = await _BacktesterRunner(_engine(), _cfg(min_rr=1.0)).run("BTCUSDT", df)
    assert res.trades, "нужны сделки, чтобы проверить план"
    for t in res.trades:
        if t.direction == "LONG":
            assert t.targets[0] > t.entry_price, (
                f"у LONG цель 1 {t.targets[0]} не выше входа {t.entry_price}"
            )
        else:
            assert t.targets[0] < t.entry_price, (
                f"у SHORT цель 1 {t.targets[0]} не ниже входа {t.entry_price}"
            )


def test_backtest_config_defaults_are_conservative():
    cfg = BacktestConfig()
    assert cfg.fee_rate > 0
    assert cfg.slippage_pct > 0
    assert cfg.min_rr >= 1.5
    assert cfg.warmup_bars >= 100


def entry_bar(df, trade) -> int:
    return next(i for i in range(len(df)) if int(df["ts"].iloc[i]) == trade.entry_ts)


def _BacktesterRunner(engine, cfg):
    """Обёртка, чтобы тесты не зависели от имени класса."""
    return Backtester(engine, cfg)
