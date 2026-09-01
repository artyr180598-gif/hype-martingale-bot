"""Задача 1: понятный вывод для новичка.

В beginner-тексте нет внутренних переменных движка (adx=/atr=/vol_z/heat),
есть блоки «Что делать» / «Почему» человеческим языком, оценка сетапа со
словами, легенда тиров, источник+время, дисклеймеры гарантий нет.
"""

from __future__ import annotations

import time

from v3.config import SignalConfig
from v3.models import RiskBrief, TradingSignal
from v3.report import render_no_trade, render_signal
from v3.telegram import HELP_TEXT
from v3.tg import render as rv


def _signal(**kw) -> TradingSignal:
    now = int(time.time() * 1000)
    base = dict(
        uid="b1", symbol="BTCUSDT", ts_ms=now, direction="LONG", status="CONFIRMED",
        source="bybit", score=85, quality=85, tier="S", rr=2.5, confidence=0.9,
        risk_score=4, leverage=3, price=65000, entry_zone=(64800, 65200),
        stop_loss=63600, targets=[66200, 67500, 68900],
        regime="TRENDING_UP", horizon="15m-4h", invalidation="close below 63600",
        reasons=["trend align"], risk_brief=RiskBrief(risk_usd=10, max_deposit_pct=1.5),
        risks=["thin order book — widen entry zone"],
        features={"timeframes": [
            {"timeframe": "15m", "trend": "up", "adx": 33, "rsi": 61, "atr_pct": 0.8, "vol_z": 1.1, "structure_signal": "BOS_UP"},
            {"timeframe": "1h", "trend": "up", "adx": 28, "rsi": 58, "atr_pct": 0.6, "vol_z": 0.5, "structure_signal": "none"},
        ], "derivatives": {"funding_trend": "flat", "liq_count": 0},
            "orderflow": {"liquidity_grade": "ok", "spread_pct": 0.02}},
        data_age_seconds=4.0, created_ms=now,
    )
    base.update(kw)
    return TradingSignal(**base)


_INTERNAL_TOKENS = ("adx", "atr", "vol_z", "heat", "trend_score", "alignment", "rsi=", "adx=")


def test_beginner_card_has_no_engine_internals():
    cfg = SignalConfig()
    text = render_signal(_signal(), "beginner").lower()
    for token in _INTERNAL_TOKENS:
        assert token not in text.lower(), f"в beginner-тексте торчит {token}"


def test_beginner_card_blocks_and_words():
    text = render_signal(_signal(), "beginner")
    assert "Оценка сетапа: 85/100 (S — отличный)" in text
    assert "**Что делать:**" in text
    low = text.lower()
    assert "купить" in low and "стоп-лосс" in low
    assert "цели:" in low and "(+1.8%)" in text
    assert "плечо" in low and "риск" in low and "% депозита" in text
    assert "**Почему:**" in text
    assert "оценка — качество сетапа, а не вероятность прибыли" in text.lower()
    assert "📡 Bybit v5 · обновлено" in text and "возраст 4с" in text
    # короткое слово направления для шорта
    short = render_signal(_signal(direction="SHORT", targets=[63000, 61000, 59000],
                                  stop_loss=66400, entry_zone=(64800, 65200)), "beginner")
    assert "Продать в шорт" in short


def test_pro_keeps_internals():
    text = render_signal(_signal(), "pro")
    assert "ADX" in text and "Score breakdown" in text


def test_quality_label_legend():
    cfg = SignalConfig()
    assert rv.quality_label(90, "S", cfg) == "90/100 (S — отличный)"
    assert rv.quality_label(75, "A", cfg) == "75/100 (A — хороший)"
    assert rv.quality_label(65, "B", cfg) == "65/100 (B — средний, нужна дисциплина)"
    assert rv.quality_label(55, "C", cfg) == "55/100 (C — слабый, обычно не входим)"


def test_help_text_has_tier_legend_and_no_guarantee():
    assert "S 82–100" in HELP_TEXT and "ниже 50 — не входим" in HELP_TEXT
    assert "качество сетапа, а не вероятность прибыли" in HELP_TEXT.lower()


def test_plain_reasons_are_human_phrases():
    sig = _signal()
    why = rv.plain_reasons(sig)
    assert 2 <= len(why) <= 3
    assert any("растёт" in w for w in why)
    for w in why:
        for token in _INTERNAL_TOKENS:
            assert token not in w.lower(), f"в причине торчит {token}"


def test_setup_row_format():
    cfg = SignalConfig()
    row = rv.render_setup_row({"signal": _signal()}, 1, cfg)
    assert "BTCUSDT" in row and "Оценка сетапа: 85/100" in row
    assert "вход" in row and "стоп" in row and "цели" in row
    assert "Почему:" in row
    for token in ("adx=", "atr=", "vol_z", "heat"):
        assert token not in row.lower()


def test_scan_header_and_zero_setup_explanation():
    now = int(time.time() * 1000)
    cfg = SignalConfig()
    summary = rv.scan_summary(250, 40, 20, [{"signal": _signal()}], "bybit", 2.1, now)
    assert summary.startswith("Сканировано 250 · кандидатов 40 · сетапов 1 (S:1)")
    assert "источник: Bybit" in summary and "UTC" in summary

    hint = rv.empty_list_hint(
        [{"signal": TradingSignal(uid="e1", symbol="X", ts_ms=now, direction="NO_TRADE",
                                  no_trade_reasons=["R:R 1.1 < 1.8"] * 3 + ["конфликт ТФ"])}] * 5,
        12,
    )
    assert "не прошли гейт" in hint and "R:R" in hint


def test_market_overview_speaks_human():
    text = rv.render_market({
        "ts_ms": int(time.time() * 1000), "mode": "beginner", "btc_trend": "up",
        "btc": {"price": 65000, "price_24h_pct": 1.2},
        "btc_atr_pct": 1.23, "btc_funding_rate": 0.0001,
        "eth": {"price": 3000, "price_24h_pct": 0.5}, "eth_24h_pct": 0.5,
        "eth_funding_rate": 0.0001, "g": {}, "global": {"btc_dominance": 55.1},
        "fear_greed": {"value": 60, "classification": "Greed"},
        "universe_count": 250, "total_turnover_24h": 5e10, "avg_move_24h_pct": 1.0,
        "top_turnover": [], "gainers": [], "losers": [],
    })
    low = text.lower()
    assert "волатильность ≈ 1.23% за час" in low
    assert "фандинг" in low and "доля btc" in low and "тренд (1ч): растёт" in low
    for token in ("atr ", "funding ", "доминация", " btc |"):
        assert token not in low


def test_no_trade_card_human():
    text = render_no_trade(TradingSignal(
        uid="nt1", symbol="X", ts_ms=int(time.time() * 1000), direction="NO_TRADE",
        no_trade_reasons=["R:R 1.2 < 1.8", "no usable order-book liquidity"],
    ))
    assert "NO TRADE" in text and "Почему нет входа" in text
    assert "не гарантия" in text


def test_no_data_screen_has_retry_hint():
    text = rv.render_no_data(
        ["тикер недоступен"],
        [{"source": "bybit", "available": False, "attempts": 3, "last_error": "timeout"}],
    )
    assert "НЕТ РЕАЛЬНЫХ ДАННЫХ" in text
    assert "тикер недоступен" in text
    assert "bybit" in text
    assert "🔄 ПОПРОБОВАТЬ СНОВА" in text
