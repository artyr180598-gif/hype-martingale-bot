"""Раунд 4: ранний отбор «намечающегося движения» + фиксы, которые его обеспечивают.

Проверяем:
  * emergence-детектор: RVOL, squeeze-release, консолидация, dpos, анти-chase;
  * emission: emergence — признак ранжирования, но НЕ триггер и НЕ гейт;
  * BOS/CHoCH исправлены (BOS = по ходу тренда, CHoCH = первый противо-тренд);
  * positioning-матрица OI × funding × цена;
  * scanner: анти-chase штраф, RS-бонус, диверсификация, возраст листинга.
"""

from __future__ import annotations

import asyncio
import math
import time

import numpy as np
import pandas as pd

from v3.analysis.derivatives import analyze_derivatives
from v3.analysis.emergence import detect_emergence
from v3.analysis.timeframes import build_timeframe_view
from v3.config import SignalConfig
from v3.engine import FuturesSignalEngine
from v3.models import DataBundle
from v3.scanner import Scanner
from v3.tg.render import EMERGING_DISCLAIMER


def make_df(n: int = 200, direction: str = "up", vol_spike: float = 1.0, squeeze: bool = False, start: float = 100.0) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    returns = rng.normal(0, 0.0008, n)
    if direction == "up":
        returns += 0.0006
    elif direction == "down":
        returns -= 0.0006
    close = start * np.cumprod(1 + returns)
    ts = np.arange(n, dtype=np.int64) * 3_600_000 + 1_700_000_000_000
    opens = close * (1 + rng.normal(0, 0.0003, n))
    highs = np.maximum(opens, close) * (1 + np.abs(rng.normal(0, 0.0008, n)))
    lows = np.minimum(opens, close) * (1 - np.abs(rng.normal(0, 0.0008, n)))
    volume = np.full(n, 1000.0) * (1 + rng.normal(0, 0.05, n))
    volume[-1] *= vol_spike  # последний бар: всплеск объёма
    return pd.DataFrame({"ts": ts, "open": opens, "high": highs, "low": lows, "close": close, "volume": volume})


# ── emergence ────────────────────────────────────────────────────
def test_emergence_detects_volume_wakeup_and_consolidation():
    cfg = SignalConfig()
    df = make_df(200, "up", vol_spike=2.0)
    e = detect_emergence(df, price_24h_pct=2.0, btc_24h_pct=1.0, cfg=cfg)
    assert e.enabled
    assert e.rvol >= 1.5
    assert e.ignition >= 25.0
    assert "объём" in " ".join(e.notes)
    assert e.early_direction in ("LONG", "SHORT", "FLAT")


def test_emergence_anti_chase_penalizes_already_hot():
    cfg = SignalConfig()
    # монета уже у вершины после большого хода
    df = make_df(200, "up", vol_spike=1.0)
    close = float(df["close"].iloc[-1])
    high = close * 1.001
    low = close * 0.95
    e = detect_emergence(df, price_24h_pct=15.0, high_24h=high, low_24h=low, cfg=cfg)
    assert e.dpos >= 0.9
    assert any("разогрето" in n for n in e.notes)
    assert e.ignition <= 20.0  # анти-chase съедает «подогрев»


def test_emergence_rs_bonus_only_when_room_left():
    cfg = SignalConfig()
    # +10% при dpos ≈ 0.97: бонус «раньше BTC и есть место» НЕ даётся
    df = make_df(200, "up", vol_spike=1.0)
    close = float(df["close"].iloc[-1])
    e = detect_emergence(df, price_24h_pct=10.0, high_24h=close * 1.001, low_24h=close * 0.90,
                         btc_24h_pct=1.0, cfg=cfg)
    assert e.dpos >= 0.95
    assert not any("ещё есть место" in n for n in e.notes)
    assert any("разогрето" in n or "вершине" in n for n in e.notes)


def test_emergence_squeeze_release_detected():
    cfg = SignalConfig()
    df = make_df(240, "up", vol_spike=1.6)
    # насильно ужимаем диапазон последних баров (имитация сжатия) -> squeeze=True,
    # затем последний бар расширяет полосы -> release
    df.loc[df.index[-15:-1], "high"] = df["close"].iloc[-15:-1] * 1.002
    df.loc[df.index[-15:-1], "low"] = df["close"].iloc[-15:-1] * 0.998
    df.loc[df.index[-1], "high"] = df["close"].iloc[-1] * 1.05
    df.loc[df.index[-1], "low"] = df["close"].iloc[-1] * 0.95
    e = detect_emergence(df, cfg=cfg)
    assert e.squeeze or e.squeeze_release


def test_emergence_is_feature_not_gate_direction():
    """Emergence может подсказывать направление, но не обязан менять гейт — он не в нём."""
    cfg = SignalConfig()
    df = make_df(200, "up", vol_spike=1.7)
    # тест инварианта: у модели нет поля direction/gate — только ранняя подсказка
    e = detect_emergence(df, cfg=cfg)
    assert e.early_direction in ("LONG", "SHORT", "FLAT")


# ── timeframes: новые поля + исправленный BOS/CHoCH ─────────────
def test_timeframe_view_exposes_new_indicators():
    view = build_timeframe_view(make_df(300, "up"), "1h")
    assert math.isfinite(view.plus_di)
    assert math.isfinite(view.minus_di)
    assert isinstance(view.ema_stack, int)
    assert 0 <= view.mfi <= 100
    assert view.rvol > 0
    assert view.macd_cross in (-1, 0, 1)
    assert view.stoch_cross in (-1, 0, 1)


def test_structure_signal_trend_aware():
    """BOS в тренде = продолжение; смена характера = CHoCH (не «BOS наоборот»)."""
    # сильный ап-тренд: структура обязана дать BOS_UP или none, но не BOS_DOWN
    view_up = build_timeframe_view(make_df(300, "up"), "1h")
    assert view_up.structure_signal not in ("BOS_DOWN", "CHoCH_DOWN") or view_up.adx < 25
    # сильный даун-тренд: не может быть BOS_UP/CHoCH_UP при высоком ADX
    view_down = build_timeframe_view(make_df(300, "down"), "1h")
    assert view_down.structure_signal not in ("BOS_UP", "CHoCH_UP") or view_down.adx < 25


# ── derivatives: positioning-матрица ────────────────────────────
def test_positioning_healthy_long_when_oi_up_price_up():
    cfg = SignalConfig()
    bundle = DataBundle(
        symbol="T", ts_ms=int(time.time() * 1000), price=100.0, price_24h_pct=3.0,
        funding_rate=0.0001, open_interest_usd=1e7,
        open_interest_history=[(0.0, 5.0)],  # (+5% OI за 24ч)
        liquidations=[], degraded=[], data_age_seconds=1.0,
    )
    der = analyze_derivatives(bundle, cfg)
    assert der.positioning == "healthy_long"
    assert der.positioning_score >= 60
    assert der.oi_change_24h_pct == 5.0


def test_positioning_overheated_long_when_oi_up_price_down_funding_high():
    cfg = SignalConfig()
    bundle = DataBundle(
        symbol="T", ts_ms=int(time.time() * 1000), price=100.0, price_24h_pct=-3.0,
        funding_rate=0.003, open_interest_usd=1e7,
        open_interest_history=[(0.0, 8.0)],
        liquidations=[], degraded=[], data_age_seconds=1.0,
    )
    der = analyze_derivatives(bundle, cfg)
    assert der.positioning == "overheated_long"
    assert der.positioning_score <= 40


def test_positioning_capitulation_when_oi_down_price_down():
    cfg = SignalConfig()
    bundle = DataBundle(
        symbol="T", ts_ms=int(time.time() * 1000), price=100.0, price_24h_pct=-4.0,
        funding_rate=-0.0005, open_interest_usd=1e7,
        open_interest_history=[(0.0, -6.0)],
        liquidations=[], degraded=[], data_age_seconds=1.0,
    )
    der = analyze_derivatives(bundle, cfg)
    assert der.positioning == "capitulation"
    assert der.positioning_score >= 55


def test_liq_acceleration_penalizes_cascade():
    cfg = SignalConfig()
    now = int(time.time() * 1000)
    bundle = DataBundle(
        symbol="T", ts_ms=now, price=100.0, price_24h_pct=-5.0,
        funding_rate=0.0001, open_interest_usd=1e7,
        open_interest_history=[],
        liquidations=[
            {"side": "buy", "size": 2_000_000.0, "ts_ms": now - 60_000},
            {"side": "buy", "size": 1_000_000.0, "ts_ms": now - 120_000},
            {"side": "sell", "size": 500_000.0, "ts_ms": now - 3_600_000},
        ],
        degraded=[], data_age_seconds=1.0,
    )
    der = analyze_derivatives(bundle, cfg)
    assert der.liq_accel_usd >= 3_000_000.0
    assert der.score < 50  # каскад ликвидаций = штраф


# ── scanner: анти-chase / RS / диверсификация / возраст ─────────
class _T:
    def __init__(self, sym, pct, high, low, last, turnover=100_000_000.0, funding=0.0001):
        self.symbol = sym
        self.turnover_24h = turnover
        self.volume_24h = turnover / 10
        self.last = last
        self.price_24h_pct = pct
        self.high_24h = high
        self.low_24h = low
        self.bid = last * 0.9999
        self.ask = last * 1.0001
        self.funding_rate = funding
        self.open_interest_usd = 1e6
        self.open_interest = 1000.0


def test_rank_penalizes_chase_and_rewards_early_relative_strength():
    from v3.scanner import _rank_candidate

    cfg = SignalConfig()
    btc = 2.0
    # A: уже у вершины после +15% (chase) — должен штрафоваться
    a = _T("AAAUSDT", 15.0, 115.0, 95.0, 114.8)
    # B: +5%, середина диапазона, сильнее BTC — «намечается»
    b = _T("BBBUSDT", 5.0, 103.0, 97.0, 100.0)
    ca = _rank_candidate(a, cfg, btc, median_pct=2.0)
    cb = _rank_candidate(b, cfg, btc, median_pct=2.0)
    assert ca is not None and cb is not None
    assert ca.rs24 == 13.0  # 15 - 2
    assert cb.dpos == 0.5
    # анти-chase штраф должен съесть «разогретость» A: «намечающееся» B выше
    assert cb.heat > ca.heat


def test_diversify_limits_similar_candidates():
    cfg = SignalConfig()
    cands = []
    for i, pct in enumerate([4.0, 4.2, 3.9, -3.0, -3.1, 0.5, 0.6]):
        c = _T(f"S{i}USDT", pct, 105.0, 95.0, 100.0)
        from v3.scanner import _rank_candidate

        cc = _rank_candidate(c, cfg, 2.0, 2.0)
        assert cc is not None
        cands.append(cc)
    scanner = Scanner(None, cfg)  # type: ignore[arg-type]
    picked = scanner._diversify(cands, top=3)
    assert len(picked) == 3
    # не все три из одной корзины (макс. 1 на кластер => минимум 2 разных)
    groups = {(round(c.rs24, 1), c.dpos) for c in picked}
    assert len(groups) >= 2


def test_scanner_emergence_path_with_klines():
    """Полный путь: klines доступны → emergence считается → попадает в features и в сигнал."""
    from v3.models import TradingSignal

    cfg = SignalConfig(SCAN_EMERGENCE_POOL=2)

    class FakeData:
        mode = "fake"

        async def klines(self, sym, tf, limit):
            return make_df(150, "up", vol_spike=1.9)

        async def tickers(self, symbols=None):
            return {s: _T(s, 4.0, 103.0, 97.0, 100.0) for s in (symbols or ["AAAUSDT"])}

        async def instruments(self):
            return []

    class FakeEngine:
        data = FakeData()

        async def analyze_batch(self, symbols, concurrency=4):
            return [
                TradingSignal(uid=f"u{i}", symbol=s, ts_ms=int(time.time() * 1000),
                              direction="LONG", status="CONFIRMED", quality=70,
                              price=100.0, entry_zone=(99.0, 100.0), stop_loss=98.0,
                              targets=[103.0, 105.0, 108.0], rr=2.0, tier="A",
                              score=70, confidence=0.9, risk_score=3, regime="RANGING")
                for i, s in enumerate(symbols)
            ]

    tickers = {
        "AAAUSDT": _T("AAAUSDT", 4.0, 103.0, 97.0, 100.0),
        "BBBUSDT": _T("BBBUSDT", 4.3, 104.0, 96.0, 100.5),
    }
    scanner = Scanner(FakeEngine(), cfg)  # type: ignore[arg-type]
    result = asyncio.run(scanner.run(tickers, limit=10, top=2))
    assert result.candidates
    assert any(c.ignition > 0 for c in result.candidates)
    assert result.analyzed
    sig = result.analyzed[0]["signal"]
    assert "emergence" in sig.features
    # emergence — признак объяснения, но не меняет direction/levels
    assert sig.direction == "LONG"
    assert sig.entry_price == 99.5 and sig.stop_loss == 98.0
    # «⚡ НАМЕЧАЕТСЯ» список работает и сортирует по ignition
    assert any(item["candidate"]["ignition"] >= 30 for item in scanner.emerging(30))


def test_report_shows_emergence_when_ignition_high():
    from v3.models import TradingSignal
    from v3.report import render_beginner, render_pro

    cfg = SignalConfig()
    sig = TradingSignal(
        uid="e1", symbol="XUSDT", ts_ms=int(time.time() * 1000), direction="LONG",
        status="CONFIRMED", quality=74, tier="A", price=100.0, entry_zone=(99.0, 100.0),
        stop_loss=98.0, targets=[103.0, 106.0], rr=2.0, confidence=0.9, risk_score=3,
        regime="RANGING", features={
            "emergence": {
                "ignition": 78.0, "early_direction": "LONG", "rvol": 1.9,
                "notes": ["объём заметно выше обычного — кто-то активно заходит",
                          "волатильность сжималась и теперь расширяется"],
            },
            "timeframes": [], "derivatives": {}, "orderflow": {}, "context": {},
        },
    )
    beginner = render_beginner(sig)
    assert "⚡" in beginner and "движение только начинается" in beginner.lower()
    pro = render_pro(sig)
    assert "Emergence" in pro


# ── beginner UX: понятные объяснения новых признаков ────────────
def test_plain_reasons_includes_emergence_and_positioning():
    from v3.models import TradingSignal
    from v3.tg.render import plain_reasons

    sig = TradingSignal(
        uid="x", symbol="XUSDT", ts_ms=int(time.time() * 1000), direction="LONG",
        status="CONFIRMED", features={
            "emergence": {
                "ignition": 74.0, "early_direction": "LONG",
                "notes": ["объём заметно выше обычного — кто-то активно заходит",
                          "открытые позиции растут (+4.0%), а цена спокойна — кто-то готовится"],
            },
            "derivatives": {"positioning": "healthy_long"},
            "timeframes": [], "orderflow": {}, "context": {},
        },
    )
    out = plain_reasons(sig)
    joined = " ".join(out)
    assert "движение только намечается" in joined
    assert "строят лонг" in joined
    # человеческие объяснения не должны содержать внутренние коды движка
    for token in ("ignition", "RVOL", "dpos", "oi_delta", "heat", "OI +"):
        assert token not in joined


def test_setup_row_shows_emergence_marker():
    from v3.models import TradingSignal
    from v3.tg.render import render_setup_row

    sig = TradingSignal(
        uid="x", symbol="XUSDT", ts_ms=int(time.time() * 1000), direction="LONG",
        status="CONFIRMED", quality=74, tier="A", price=100.0, entry_zone=(99.0, 100.0),
        stop_loss=98.0, targets=[103.0, 106.0], rr=2.0,
        features={"emergence": {"ignition": 73.0, "early_direction": "LONG"}},
    )
    row = render_setup_row({"signal": sig}, 1)
    assert "⚡" in row
    assert "только намечается" in row
    assert "ignition" not in row


def test_glossary_has_new_terms():
    from v3.tg.render import GLOSSARY, render_glossary

    assert "emergence" in GLOSSARY and "rvol" in GLOSSARY and "positioning" in GLOSSARY
    text = render_glossary("emergence").lower()
    assert "намечается" in text and "не гарантия" in text


def test_engine_risks_include_positioning_warning():
    from v3.tests.test_v3 import make_tf_map

    cfg = SignalConfig()
    engine = FuturesSignalEngine(data=None, cfg=cfg)  # type: ignore[arg-type]
    bundle = DataBundle(
        symbol="TESTUSDT", ts_ms=int(time.time() * 1000), price=100.0, price_24h_pct=-3.0,
        turnover_24h=100_000_000.0, volume_24h=1_000_000.0, funding_rate=0.003,
        funding_history=[0.003, 0.003, 0.003], open_interest_usd=50_000_000.0,
        open_interest_history=[(0.0, 6.0)],
        orderbook={"bids": [(99.98 + i * 0.01, 100) for i in range(20)],
                   "asks": [(100.02 + i * 0.01, 100) for i in range(20)], "ts_ms": 0},
        btc_price_24h_pct=1.5, btc_turnover_24h=20_000_000_000.0, btc_dominance=55.0,
        global_change_pct=1.0, degraded=[], data_age_seconds=2.0,
    )
    sig = engine.evaluate_bundle(bundle, make_tf_map(), btc_tf=None, strict_liquidity=False)
    if sig.direction in ("LONG", "SHORT"):
        assert any("перегреты" in r for r in sig.risks)


def test_scanner_run_works_with_fake_engine_and_no_klines():
    """Fake-движок без klines: скан не падает, emergence просто пропускается."""

    class FakeEngine:
        class FakeData:
            mode = "fake"

        data = FakeData()

        async def analyze_batch(self, symbols, concurrency=4):
            from v3.models import TradingSignal

            return [
                TradingSignal(uid=f"u{i}", symbol=s, ts_ms=int(time.time() * 1000),
                              direction="NO_TRADE", status="NO_TRADE", quality=50,
                              no_trade_reasons=["test"])
                for i, s in enumerate(symbols)
            ]

    cfg = SignalConfig()
    tickers = {
        "AAAUSDT": _T("AAAUSDT", 4.0, 103.0, 97.0, 100.0),
        "BBBUSDT": _T("BBBUSDT", 4.3, 104.0, 96.0, 100.5),
        "CCCUSDT": _T("CCCUSDT", -3.0, 102.0, 94.0, 95.0),
    }
    scanner = Scanner(FakeEngine(), cfg)  # type: ignore[arg-type]
    result = asyncio.run(scanner.run(tickers, limit=10, top=2))
    assert result.candidates
    assert result.analyzed
    assert isinstance(result.duration_sec, float)
    assert scanner.emerging() == []  # ignite не посчитан — честный пустой список


# ── «⚡ НАМЕЧАЕТСЯ ДВИЖЕНИЕ» в Telegram-скане ────────────────────
def emerging_df(n: int = 150, spike: float = 2.0) -> pd.DataFrame:
    """1h-свечи «до разгона»: цена стоит в узком коридоре, объём бара вырос."""
    df = make_df(n, "up", vol_spike=spike)
    idx = df.index[-14:]
    base = df["close"].iloc[-14]
    df.loc[idx, "close"] = base
    df.loc[idx, "open"] = base
    df.loc[idx, "high"] = base * 1.002
    df.loc[idx, "low"] = base * 0.998
    return df


def _scan_core(cfg: SignalConfig, with_klines: bool, db_path: str):
    """V3Core на фейковых данных: скан без сети, но с настоящим Scanner."""
    from v3.models import TradingSignal
    from v3.store import SignalLifecycle, SignalStore
    from v3.telegram import V3Core

    class FakeData:
        mode = "fake"

        async def klines(self, sym, tf, limit):
            return emerging_df() if with_klines else None

        async def tickers(self, symbols=None):
            return {s: _T(s, 2.0, 103.0, 97.0, 100.0) for s in (symbols or ["AAAUSDT"])}

        async def instruments(self):
            return []

    class FakeEngine:
        data = FakeData()

        async def analyze_batch(self, symbols, concurrency=4):
            return [
                TradingSignal(uid=f"scan-{s}-{i}", symbol=s, ts_ms=int(time.time() * 1000),
                              direction="LONG", status="CONFIRMED", quality=70, tier="A",
                              price=100.0, entry_zone=(99.0, 100.0), stop_loss=98.0,
                              targets=[103.0, 105.0, 108.0], rr=2.0, score=70,
                              confidence=0.9, risk_score=3, regime="RANGING",
                              # инвариант «только реальные данные»: без возраста
                              # данных сигнал честно превращается в NO TRADE
                              data_age_seconds=2.0)
                for i, s in enumerate(symbols)
            ]

    store = SignalStore(db_path)
    lifecycle = SignalLifecycle(store, cooldown_seconds=60, max_active=3)
    return V3Core(FakeData(), FakeEngine(), store, lifecycle, cfg)  # type: ignore[arg-type]


def test_scan_text_shows_emerging_block_with_disclaimer(tmp_path):
    """В Telegram-скане появился блок «⚡ НАМЕЧАЕТСЯ ДВИЖЕНИЕ» (был только в CLI)."""
    cfg = SignalConfig()
    core = _scan_core(cfg, with_klines=True, db_path=str(tmp_path / "scan_kg.db"))
    try:
        text = asyncio.run(core.scan_text("beginner"))
    finally:
        core.store.close()

    assert "НАМЕЧАЕТСЯ ДВИЖЕНИЕ" in text
    assert EMERGING_DISCLAIMER in text
    assert "AAAUSDT" in text and "подогрев" in text.lower()
    # подогрев (ранние признаки) и уверенность бота (полный анализ) —
    # отдельные строки, чтобы их нельзя было перепутать
    assert "Оценка сетапа:" in text and "Уверенность бота:" in text
    # заметки — человеческие, из emergence, а не сырые поля движка
    assert "объём заметно выше обычного" in text or "узком коридоре" in text
    low = text.lower()
    assert "ignition" not in low and "early_direction" not in low
    # блок раннего отбора не подменяет основной движок: сетапы и гейт на месте
    assert "СКАН РЫНКА" in text and "НАЙДЕНО СЕТАПОВ" in text


def test_scan_text_hides_emerging_block_when_empty(tmp_path):
    """Пустой список → блока нет (не печатаем «для галочки»)."""
    cfg = SignalConfig()
    core = _scan_core(cfg, with_klines=False, db_path=str(tmp_path / "scan_empty.db"))
    try:
        text = asyncio.run(core.scan_text("beginner"))
    finally:
        core.store.close()
    assert "НАМЕЧАЕТСЯ" not in text
    assert "СКАН РЫНКА" in text


def test_scan_text_pro_mode_may_show_raw_ignition(tmp_path):
    """PRO-режиму можно сырые ignition/подсказку — новичку нет."""
    cfg = SignalConfig()
    core = _scan_core(cfg, with_klines=True, db_path=str(tmp_path / "scan_pro.db"))
    try:
        text = asyncio.run(core.scan_text("pro"))
    finally:
        core.store.close()
    assert "НАМЕЧАЕТСЯ ДВИЖЕНИЕ" in text
    assert "ignition" in text.lower()


def test_render_emerging_block_rules():
    """Хелпер блока: максимум 5 монет, заметки новичковые, дисклеймер, пусто → ''."""
    from v3.tg.render import render_emerging

    cfg = SignalConfig()
    items = [
        {"candidate": {"symbol": f"S{i}USDT", "ignition": 90.0 - i, "early_direction": "LONG",
                       "emergence_note": "объём заметно выше обычного (×2.1) — кто-то активно заходит"},
         "signal": None}
        for i in range(8)
    ]
    text = render_emerging(items, cfg)
    assert text.startswith("⚡ НАМЕЧАЕТСЯ ДВИЖЕНИЕ")
    assert text.count("подогрев ") == 5  # не больше 5 монет
    assert "S0USDT" in text and "S7USDT" not in text
    assert EMERGING_DISCLAIMER in text
    assert "ignition" not in text.lower()
    # пустой список → пустая строка
    assert render_emerging([], cfg) == ""
    assert render_emerging(None, cfg) == ""  # type: ignore[arg-type]
    # анти-chase заметки (движение уже было) в блок «намечается» не идут
    hot = [{"candidate": {"symbol": "HOTUSDT", "ignition": 60.0, "early_direction": "LONG",
                          "emergence_note": ("объём заметно выше обычного (×2.1) — кто-то активно заходит"
                                             " | уже у вершины после большого хода — не «намечается», а разогрето")},
            "signal": None}]
    hot_text = render_emerging(hot, cfg)
    assert "объём заметно выше обычного" in hot_text
    assert "уже у вершины" not in hot_text


def test_relative_volume_baseline_excludes_current_bar():
    """Всплеск не должен сам разбавлять среднее, с которым сравнивается."""
    from v3.analysis.emergence import relative_volume

    df = make_df(80, "up", vol_spike=3.0)
    assert relative_volume(df, 20) > 2.5


def test_emergence_exposes_phase_and_pressure():
    """Сканер различает раннюю базу, подтверждённый старт и истощение."""
    cfg = SignalConfig()
    early = emerging_df(150, spike=2.0)
    e = detect_emergence(early, price_24h_pct=1.0, btc_24h_pct=0.0, cfg=cfg)
    assert e.phase in ("EARLY", "TRIGGERED", "NEUTRAL")
    assert -1.0 <= e.breakout_pressure <= 1.0
    assert e.compression_ratio > 0
    assert 0.0 <= e.room_pct <= 1.0


def test_exhausted_phase_is_not_an_emerging_setup():
    cfg = SignalConfig()
    df = make_df(200, "up", vol_spike=1.0)
    close = float(df["close"].iloc[-1])
    e = detect_emergence(
        df, price_24h_pct=15.0, high_24h=close * 1.001, low_24h=close * 0.95, cfg=cfg
    )
    assert e.phase == "EXHAUSTED"
    assert e.room_pct < cfg.EMERGENCE_MIN_ROOM_PCT or e.dpos > 0.95
