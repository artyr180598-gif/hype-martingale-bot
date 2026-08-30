"""
Отчёт: формат обязан соответствовать ТЗ.

Проверяем структуру, а не «красиво выглядит»: первая строка — вердикт,
все пять разделов на месте, числа сопровождаются пояснениями, заглушки
помечены.
"""

from __future__ import annotations

import pytest

from v2.engine import AnalysisEngine
from v2.models import CoinReport, ScanResult, TokenCandidate
from v2.reporter import render_report, render_scan

REQUIRED_SECTIONS = [
    "## 🛡️ Безопасность",
    "## 📊 Рыночные данные",
    "## 📈 Технический анализ",
    "## 📣 Социальный фон",
    "## 💡 Рекомендации",
]


async def _report(config, provider, symbol="TITAN") -> CoinReport:
    engine = AnalysisEngine(config, provider)
    return await engine.analyze(symbol)


async def test_report_starts_with_verdict(config, provider):
    text = render_report(await _report(config, provider), config)
    first_line = text.split("\n", 1)[0]
    assert first_line.startswith("**Вердикт:")
    assert any(word in first_line for word in ("Входить", "Не входить", "Наблюдать"))


async def test_report_has_all_required_sections(config, provider):
    text = render_report(await _report(config, provider), config)
    for section in REQUIRED_SECTIONS:
        assert section in text, f"нет раздела {section}"


async def test_report_explains_atr_and_stop(config, provider):
    """Требование ТЗ: каждое число с пояснением."""
    report = await _report(config, provider)
    text = render_report(report, config)
    assert "ATR" in text
    if report.plan.direction != "WAIT":
        assert "Стоп-лосс" in text
        assert "% от входа" in text
        assert "·ATR" in text
        assert "R:R" in text


async def test_report_marks_stubbed_data(config, provider):
    text = render_report(await _report(config, provider), config)
    assert "Эмуляция" in text          # соцфон без X API
    assert "эмулирован" in text        # стакан DEX


async def test_report_shows_blockers_for_scam_token(config, provider):
    report = await _report(config, provider, "MOONX")
    text = render_report(report, config)
    assert "Не входить" in text.split("\n", 1)[0]
    assert "mint" in text.lower()
    assert "⛔" in text


async def test_report_json_roundtrip(config, provider):
    report = await _report(config, provider)
    data = report.to_dict()
    assert data["verdict_ru"] in ("Входить", "Не входить", "Наблюдать")
    assert 1 <= data["risk_score"] <= 10
    assert data["plan"]["rr"] >= 0
    assert data["security"]["score"] >= 0


async def test_scan_report_shows_funnel_and_filters(config, provider):
    from v2.scanner.pipeline import ScannerPipeline

    result = await ScannerPipeline(config, provider).run(limit=50, analyze_top=2)
    text = render_scan(result, config)
    assert "Уровень 1" in text and "Уровень 2" in text and "Уровень 3" in text
    assert "Активные фильтры" in text
    assert "вошло" in text


def test_scan_report_survives_empty_result(config):
    empty = ScanResult(mode="demo")
    text = render_scan(empty, config)
    assert "Ничего не прошло фильтры" in text


def test_usd_and_price_formatters():
    from v2.reporter import pct, price, usd

    assert usd(1_500_000) == "$1.50M"
    assert usd(2_500) == "$2.5K"
    assert usd(None) == "нет данных"
    assert price(0.0000123) == "0.0000123000"
    assert pct(1.234) == "+1.23%"
    assert price(None) == "нет данных"
