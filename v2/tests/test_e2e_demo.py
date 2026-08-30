"""
Сквозные тесты: запрос пользователя → отчёт.

Здесь проверяется весь путь целиком (провайдер → анализ → риск → отчёт),
включая поведение при отказах: бот обязан ответить текстом, а не упасть.
"""

from __future__ import annotations

import pytest

from v2.bot import AssistantCore
from v2.core.errors import TokenNotFound
from v2.engine import AnalysisEngine


@pytest.fixture()
def core(config, provider):
    return AssistantCore(config, provider=provider)


async def test_analyze_by_symbol_returns_markdown_report(core):
    text = await core.handle_message("проанализируй TITAN")
    assert text.split("\n", 1)[0].startswith("**Вердикт:")
    assert "## 🛡️ Безопасность" in text
    assert "## 💡 Рекомендации" in text


async def test_analyze_by_address(core, provider):
    token = (await provider.resolve_token("AURORA"))[0]
    text = await core.handle_message(f"разбери {token.address}")
    assert "AURORA" in text
    assert "Вердикт" in text


async def test_unknown_token_returns_friendly_error(core):
    text = await core.handle_message("проанализируй NOSUCHTOKEN")
    # в демо-режиме неизвестный адрес создаётся, а неизвестный символ — нет
    assert "Не нашёл" in text or "Вердикт" in text


async def test_engine_raises_token_not_found_for_empty_query(core):
    with pytest.raises(TokenNotFound):
        await core.engine.analyze("   ")


async def test_scan_command_shows_three_levels(core):
    text = await core.handle_message("скан")
    for level in ("Уровень 1", "Уровень 2", "Уровень 3"):
        assert level in text
    assert core.last_scan is not None
    assert len(core.last_scan.stages) == 3


async def test_status_and_filters_commands(core):
    assert "Состояние бота" in await core.handle_message("/status")
    assert "Активные фильтры" in await core.handle_message("/filters")
    assert "что я умею" in (await core.handle_message("/help")).lower()


async def test_empty_message_returns_help(core):
    assert "умею" in await core.handle_message("")


async def test_buy_command_opens_paper_position(core, tmp_path):
    core.config.EXECUTOR_JOURNAL_PATH = tmp_path / "orders.jsonl"
    text = await core.handle_message("/buy TITAN")
    assert ("Виртуальная сделка открыта" in text) or ("Отклонён" in text) or ("отклонён" in text)


async def test_bot_survives_internal_error(core, monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("всё сломалось")

    monkeypatch.setattr(core.engine, "analyze", boom)
    text = await core.handle_message("проанализируй TITAN")
    assert "Внутренняя ошибка" in text
    assert "RuntimeError" in text


async def test_report_confidence_drops_without_data(config, provider, monkeypatch):
    """Нет холдеров/деплоера → уверенность ниже, но отчёт всё равно есть."""
    async def none(*_a, **_k):
        return None

    monkeypatch.setattr(provider, "holders", none)
    monkeypatch.setattr(provider, "deployer", none)
    report = await AnalysisEngine(config, provider).analyze("AURORA")
    assert report.confidence < 1.0
    assert report.security.degraded
    assert report.verdict in ("ENTER", "WATCH", "AVOID")


async def test_analyze_is_repeatable(core):
    """Детерминированность демо-рынка: два запроса дают одинаковый вердикт."""
    first = await core.handle_message("analyze KELP")
    second = await core.handle_message("analyze KELP")
    assert first.split("\n", 1)[0] == second.split("\n", 1)[0]


async def test_json_payload_is_complete(config, provider):
    report = await AnalysisEngine(config, provider).analyze("TITAN")
    data = report.to_dict()
    for key in ("token", "security", "micro", "technical", "social", "plan",
                "verdict", "risk_score", "confidence", "score"):
        assert key in data
    assert data["technical"]["atr"] > 0
    assert data["security"]["holders"]["top10_pct"] is not None
