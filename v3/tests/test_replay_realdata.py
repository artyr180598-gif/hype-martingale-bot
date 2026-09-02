"""Прогон движка на РЕАЛЬНЫХ рыночных данных (сняты с биржи, без сети).

Фикстура ``v3/tests/fixtures/okx_btcusdt_swap_capture.json`` — дословные ответы
публичного REST OKX v5 по BTC-USDT-SWAP на 2026-09-02 ~11:52 UTC: 5 таймфреймов
по 60 свечей, тикер, ставка финансирования, открытый интерес, стакан.

Зачем эти тесты отдельным файлом
-------------------------------
Юнит-тесты и бэктест вызывают ``evaluate_bundle()`` напрямую и поэтому не
проходят проверку свежести свечей в ``analyze()``. Ошибка в этой проверке
(«данные устарели» почти в любой момент времени → NO_TRADE) не ловилась ничем
и была найдена только прогоном на реальных свечах (``python -m v3 replay``).
Тесты ниже фиксируют и сам прогон, и найденное поведение.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from v3.analysis.confidence import assess_confidence
from v3.config import SignalConfig
from v3.replay import (
    SnapshotSource,
    load_snapshot,
    okx_capture_to_snapshot,
    replay_once,
    trim_snapshot,
    walk_points,
)

FIXTURE = Path(__file__).parent / "fixtures" / "okx_btcusdt_swap_capture.json"
CAPTURE_MS = 1788349954307          # момент съёма снапшота (ts стакана)
LAST_CLOSED_15M_OPEN = 1788348600000  # последняя закрытая 15m-свеча на тот момент
TF_15M_MS = 900_000


@pytest.fixture(scope="module")
def capture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def snapshot(capture: dict) -> dict:
    return okx_capture_to_snapshot(capture)


# ── 1. фикстура действительно настоящая и целая ─────────────────
def test_fixture_is_a_real_okx_capture(capture: dict):
    assert capture["kind"] == "okx_capture_v1"
    assert capture["inst_id"] == "BTC-USDT-SWAP"
    assert set(capture["candles"]) == {"5m", "15m", "1h", "4h", "1d"}
    step_ms = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
    for tf, rows in capture["candles"].items():
        assert len(rows) == 60, tf
        ts = [int(r[0]) for r in rows]
        assert all(b - a == step_ms[tf] for a, b in zip(ts, ts[1:])), f"{tf}: рваный шаг свечей"
        for row in rows:
            o, h, low, c = (float(row[i]) for i in (1, 2, 3, 4))
            assert low <= min(o, c) and max(o, c) <= h, f"{tf}: OHLC несогласован"
            assert float(row[6]) > 0, f"{tf}: нулевой объём"
    # цена в тикере и закрытие последней свечи одного порядка — данные одного момента
    assert abs(float(capture["ticker"][0]["last"]) - 76607.2) < 1e-6
    assert float(capture["open_interest"][0]["oiUsd"]) > 1e9


def test_okx_symbol_is_normalized_without_swap_suffix(snapshot: dict):
    # normalize_symbol('BTC-USDT-SWAP') дал бы BTCUSDTSWAPUSDT
    assert snapshot["symbol"] == "BTCUSDT"
    assert snapshot["captured_at_ms"] == CAPTURE_MS
    assert snapshot["ticker"]["funding_rate"] == pytest.approx(0.0001)
    assert snapshot["ticker"]["open_interest_usd"] == pytest.approx(2260869691.23, rel=1e-9)
    assert len(snapshot["orderbook"]["bids"]) == 12
    assert snapshot["funding_history"]  # текущая + последняя рассчитанная ставка


def test_loaded_snapshot_matches_converter(snapshot: dict):
    assert load_snapshot(FIXTURE) == snapshot


# ── 2. движок реально отрабатывает на этих данных ───────────────
async def test_engine_runs_on_real_exchange_data(snapshot: dict):
    res = await replay_once(snapshot, cfg=SignalConfig(), mode="beginner")
    sig = res.signal

    assert res.symbol == "BTCUSDT"
    assert sig.price == pytest.approx(76607.2)          # цена из реального тикера
    assert sig.direction in ("LONG", "SHORT", "WAIT", "NO_TRADE")
    assert 0.0 <= sig.quality <= 100.0
    assert 0.0 <= res.confidence.percent <= 100.0
    assert len(res.confidence.parts) == 6               # все шесть анализов разобраны
    assert not any("анализ недоступен" in p.note for p in res.confidence.parts)
    # карточка для пользователя собралась и показывает уверенность отдельным блоком
    assert "УВЕРЕННОСТЬ БОТА" in res.card
    assert f"{res.confidence.percent:.0f}%" in res.card
    assert res.alert.percent == pytest.approx(res.confidence.percent)
    # assess_confidence на том же сигнале даёт ту же цифру (детерминизм)
    assert assess_confidence(sig).percent == pytest.approx(res.confidence.percent)


async def test_fresh_real_data_is_not_stale(snapshot: dict):
    """Регрессия: свежие данные биржи не должны объявляться устаревшими.

    Прежняя проверка сравнивала время ОТКРЫТИЯ последней закрытой свечи с
    ``tf + MAX_DATA_AGE_SECONDS`` и поэтому считала данные устаревшими почти
    всё время (закрытая часовая свеча по построению старше часа).
    """
    res = await replay_once(snapshot, cfg=SignalConfig(), mode="pro")
    assert not [d for d in res.degraded if "stale klines" in d], res.degraded
    assert res.signal.stale is False
    assert not [r for r in res.signal.no_trade_reasons if "stale" in r]


async def test_no_false_staleness_at_any_minute_of_the_hour(snapshot: dict):
    """В любую минуту часа ни один таймфрейм не «устаревает» (8 точек замера)."""
    cfg = SignalConfig()
    hour_open = 1788346800000
    for minute in (0, 5, 15, 25, 35, 45, 55, 59):
        as_of = hour_open + minute * 60_000 + 30_000
        res = await replay_once(snapshot, cfg=cfg, mode="pro", as_of_ms=as_of)
        stale_klines = [d for d in res.degraded if "stale klines" in d]
        assert not stale_klines, f"{minute} мин: {stale_klines}"


async def test_lagging_chart_is_still_reported_stale(snapshot: dict):
    """Починка не выключила проверку: отстающий график по-прежнему виден."""
    lagging = json.loads(json.dumps(snapshot))
    for tf in ("5m", "15m", "1h"):
        lagging["klines"][tf] = lagging["klines"][tf][:-3]  # биржа «не отдала» 3 последние свечи
    res = await replay_once(lagging, cfg=SignalConfig(), mode="pro")
    assert [d for d in res.degraded if "stale klines" in d], res.degraded
    assert res.signal.stale is True
    assert any("stale" in r for r in res.signal.no_trade_reasons)


async def test_data_age_counts_from_bar_close_not_open(snapshot: dict):
    """Возраст данных = время с ЗАКРЫТИЯ последней свечи (fallback без тикера)."""

    class _NoTickerTs(SnapshotSource):
        async def get_tickers(self, symbols=None):
            rows = await super().get_tickers(symbols)
            for row in rows:
                row.ts_ms = None  # биржа не дала timestamp — движок считает возраст по свече
            return rows

    import v3.replay as replay_mod

    original = replay_mod.SnapshotSource
    replay_mod.SnapshotSource = _NoTickerTs
    try:
        res = await replay_once(snapshot, cfg=SignalConfig(), mode="pro")
    finally:
        replay_mod.SnapshotSource = original

    expected = (CAPTURE_MS - LAST_CLOSED_15M_OPEN - TF_15M_MS) / 1000.0
    assert res.signal.data_age_seconds == pytest.approx(expected, abs=1.0)
    assert res.signal.data_age_seconds < TF_15M_MS / 1000.0  # меньше одного таймфрейма


# ── 3. честность: чего нет в снапшоте, того нет и в отчёте ───────
async def test_missing_sources_are_reported_not_fabricated(snapshot: dict):
    res = await replay_once(snapshot, cfg=SignalConfig(), mode="pro")
    assert "global context unavailable" in res.degraded
    marks = {row["field"]: row["real"] for row in res.availability}
    assert marks["свечи 15m"] is True
    assert marks["стакан"] is True
    assert marks["новости/сентимент"] is False
    assert marks["ликвидации"] is False
    assert marks["long/short ratio"] is False


async def test_weak_real_setup_stays_silent(snapshot: dict):
    """На слабом реальном сетапе бот молчит и объясняет почему (без спама)."""
    res = await replay_once(snapshot, cfg=SignalConfig(), mode="beginner")
    cfg = SignalConfig()
    if res.signal.direction not in ("LONG", "SHORT"):
        assert res.alert.ok is False
        assert res.alert.reasons
        assert res.signal.no_trade_reasons
    else:  # если рынок всё же дал вход — авто-сигнал обязан пройти те же пороги
        assert res.alert.ok is (
            res.signal.quality >= cfg.ALERT_MIN_QUALITY and res.alert.percent >= cfg.ALERT_MIN_BOT_CONFIDENCE
        )


# ── 4. проход по истории не заглядывает в будущее ────────────────
def test_trim_snapshot_keeps_only_closed_bars(snapshot: dict):
    as_of = 1788349500000
    trimmed = trim_snapshot(snapshot, as_of)
    step_ms = {"5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
    for tf, rows in trimmed["klines"].items():
        assert rows, tf
        assert int(rows[-1][0]) + step_ms[tf] <= as_of, f"{tf}: в срезе осталась незакрытая свеча"
    # forming-свечи (confirm=0) в срез не попали
    assert int(trimmed["klines"]["15m"][-1][0]) == LAST_CLOSED_15M_OPEN


def test_walk_points_go_forward_and_stay_inside_snapshot(snapshot: dict):
    points = walk_points(snapshot, steps=6, step=2, entry_tf="15m")
    assert len(points) == 6
    assert points == sorted(points)
    assert len(set(points)) == len(points)
    # ни одна точка не позже момента съёма (closing формирующейся свечи отброшен)
    assert all(p <= snapshot["captured_at_ms"] for p in points)
    # последняя точка — закрытие последней ЗАКРЫТОЙ свечи входного ТФ
    assert points[-1] == LAST_CLOSED_15M_OPEN + TF_15M_MS
    assert int(snapshot["klines"]["15m"][-1][0]) + TF_15M_MS > snapshot["captured_at_ms"]


# ════════════════════════════════════════════════════════════════
#  ДЛИННАЯ РЕАЛЬНАЯ СЕРИЯ + БЭКТЕСТ НА НЕЙ
# ════════════════════════════════════════════════════════════════
SERIES_FIXTURE = Path(__file__).parent / "fixtures" / "okx_btcusdt_15m_300.json"
SERIES_ROWS = 300
SERIES_OLDEST_TS = 1788083100000   # 2026-08-30 09:45 UTC
SERIES_NEWEST_TS = 1788352200000   # 2026-09-02 12:30 UTC (свеча ещё формировалась)


@pytest.fixture(scope="module")
def series_raw() -> dict:
    return json.loads(SERIES_FIXTURE.read_text(encoding="utf-8"))


def test_series_fixture_is_a_real_okx_series(series_raw: dict):
    """Фикстура — дословный ответ OKX, а не синтетика."""
    assert series_raw["kind"] == "okx_candles_v1"
    assert series_raw["inst_id"] == "BTC-USDT-SWAP"
    assert "okx.com/api/v5/market/candles" in series_raw["source"]
    rows = series_raw["rows"]
    assert len(rows) == SERIES_ROWS
    assert {len(r) for r in rows} == {9}
    ts = [int(r[0]) for r in rows]
    assert ts == sorted(ts, reverse=True), "OKX отдаёт свечи от новых к старым"
    assert ts[0] == SERIES_NEWEST_TS and ts[-1] == SERIES_OLDEST_TS
    # равномерная сетка 15m без пропусков — иначе пересборка 1h/4h врёт
    assert {ts[i - 1] - ts[i] for i in range(1, len(ts))} == {TF_15M_MS}
    # ровно одна недоформированная свеча (самая свежая)
    assert [str(r[8]) for r in rows].count("0") == 1
    assert str(rows[0][8]) == "0"
    # OHLC непротиворечив на каждой свече
    for r in rows:
        o, h, low, c = (float(r[i]) for i in (1, 2, 3, 4))
        assert low <= min(o, c) and max(o, c) <= h, r
        assert float(r[6]) > 0


def test_load_candles_series_drops_the_forming_bar():
    from v3.replay import load_candles_series

    series = load_candles_series(SERIES_FIXTURE)
    assert series["symbol"] == "BTCUSDT"
    assert series["tf"] == "15m"
    assert series["rows"] == SERIES_ROWS
    assert series["forming_dropped"] == 1
    df = series["df"]
    assert len(df) == SERIES_ROWS - 1 == series["closed"]
    assert list(df.columns) == ["ts", "open", "high", "low", "close", "volume"]
    assert int(df["ts"].iloc[0]) == SERIES_OLDEST_TS
    # недоформированная свеча в историю не попала
    assert int(df["ts"].iloc[-1]) == SERIES_NEWEST_TS - TF_15M_MS
    assert df["ts"].is_monotonic_increasing
    assert df[["open", "high", "low", "close", "volume"]].notna().all().all()


def test_load_candles_series_rejects_other_formats(tmp_path: Path, series_raw: dict):
    from v3.replay import load_candles_series

    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({**series_raw, "kind": "okx_capture_v1"}), encoding="utf-8")
    with pytest.raises(ValueError, match="okx_candles_v1"):
        load_candles_series(wrong)


def test_bot_confidence_reads_the_attached_report():
    """В отчёте — сводный процент бота, а не полнота данных (0..1)."""
    from v3.replay import _bot_confidence

    class Trade:
        signal = {"features": {"bot_confidence": {"percent": 57.5}}}

    assert _bot_confidence(Trade()) == 57.5

    class Empty:
        signal = {}

    assert _bot_confidence(Empty()) == 0.0


def test_backtest_runs_on_real_series_and_finds_setups(capsys):
    """Реальные данные: бэктестер исполняется И движок находит сетапы.

    До этого вся «реальная» проверка показывала только NO_TRADE — не было
    доказательства, что бот вообще способен найти вход на живом рынке.
    """
    from v3.replay import run_replay_backtest

    code = run_replay_backtest(str(SERIES_FIXTURE), warmup=120)
    out = capsys.readouterr().out
    assert code == 0
    assert "БЭКТЕСТ НА РЕАЛЬНЫХ СВЕЧАХ · BTCUSDT" in out
    assert "закрытых использовано: 299" in out
    assert "точек решения: 100" in out          # 299 баров - warmup 120 - 3
    assert "win_rate" in out and "profit_factor" in out
    # ключевое утверждение: на РЕАЛЬНЫХ свечах движок выдаёт исполняемые сетапы
    assert "исполняемых сетапов: 5" in out
    assert "Сделок нет" not in out
    # живые пороги печатаются честно, включая нуль по авто-сигналам
    assert "авто-сигнал" in out and "0 из 5" in out
    assert "не гарантирует будущих результатов" in out


def test_cli_exposes_the_backtest_flag():
    from v3.cli import build_parser

    args = build_parser().parse_args(["replay", str(SERIES_FIXTURE), "--backtest", "--warmup", "150"])
    assert args.command == "replay"
    assert args.backtest is True
    assert args.warmup == 150
    assert build_parser().parse_args(["replay", "x.json"]).backtest is False
