"""
Переключение движка v1/v2 без Telegram.

Проверяем handle_message, handle_callback / handle_engine_callback
и то, что v1 не принимает DEX/ончейн-адреса.
"""

from __future__ import annotations

import pytest

from v2.bot import (
    DEX_ONLY_IN_V2,
    ENGINE_V1,
    ENGINE_V2,
    AssistantCore,
    handle_engine_callback,
    is_onchain_query,
    kb_engine,
    to_cex_symbol,
)


@pytest.fixture()
def core(config, provider):
    return AssistantCore(config, provider=provider)


def test_kb_engine_matches_v1_main_pattern():
    kb = kb_engine()
    data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    assert data == ["engine:v2", "engine:v1"]
    assert "🆕 Движок: v2" in texts
    assert "🧮 Движок: v1" in texts
    for row in kb.inline_keyboard:
        for btn in row:
            assert btn.callback_data is None or len(btn.callback_data) <= 64


def test_is_onchain_query_detects_evm_and_mint():
    assert is_onchain_query("проанализируй 0x1f9840a85d5af5bf1d1762f925bdaddc4201f984")
    assert is_onchain_query("0x717A00000000000000000000000000000000000C")
    assert is_onchain_query("So11111111111111111111111111111111111111112")
    assert not is_onchain_query("SOLUSDT")
    assert not is_onchain_query("проанализируй SOL")
    assert not is_onchain_query("AURORA")


def test_to_cex_symbol_normalizes_pair():
    assert to_cex_symbol("sol") == "SOLUSDT"
    assert to_cex_symbol("SOLUSDT") == "SOLUSDT"
    assert to_cex_symbol("SOL/USDT") == "SOLUSDT"
    assert to_cex_symbol("") == ""


def test_engine_defaults_to_v2(core: AssistantCore):
    assert core.get_engine() == ENGINE_V2
    assert core.get_engine(101) == ENGINE_V2


async def test_engine_command_shows_current(core: AssistantCore):
    text = await core.handle_message("/engine", chat_id=11)
    assert "v2" in text
    assert "движок" in text.lower()


async def test_callback_switches_engine_per_chat(core: AssistantCore):
    a = await handle_engine_callback(core, "engine:v1", chat_id=1)
    b = await core.handle_callback("engine:v2", chat_id=2)
    assert "v1" in a
    assert "v2" in b
    assert core.get_engine(1) == ENGINE_V1
    assert core.get_engine(2) == ENGINE_V2
    # чужой чат не затронут
    assert core.get_engine(3) == ENGINE_V2


async def test_handle_message_can_switch_engine(core: AssistantCore):
    switched = await core.handle_message("/engine v1", chat_id=21)
    assert core.get_engine(21) == ENGINE_V1
    assert "v1" in switched
    shown = await core.handle_message("/engine", chat_id=21)
    assert "**v1**" in shown or "v1" in shown
    back = await core.handle_message("/engine v2", chat_id=21)
    assert core.get_engine(21) == ENGINE_V2
    assert "v2" in back


async def test_v1_engine_is_lazy_until_cex_analyze(core: AssistantCore):
    assert core._v1_engine is None
    await core.handle_callback("engine:v1", chat_id=7)
    assert core._v1_engine is None
    text = await core.handle_message("SOLUSDT", chat_id=7)
    assert core._v1_engine is not None
    assert "SOLUSDT" in text
    assert "Движок v1" in text
    assert "/100" in text
    assert "Цена" in text
    assert "## 🛡️ Безопасность" not in text


async def test_v1_rejects_evm_address_without_switching(core: AssistantCore):
    await core.handle_callback("engine:v1", chat_id=42)
    text = await core.handle_message(
        "проанализируй 0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", chat_id=42
    )
    assert text == DEX_ONLY_IN_V2 or "DEX/ончейн" in text
    assert "только в движке v2" in text
    assert core.get_engine(42) == ENGINE_V1
    assert core._v1_engine is None


async def test_v1_rejects_solana_mint_without_switching(core: AssistantCore):
    await handle_engine_callback(core, "engine:v1", chat_id=43)
    mint = "So11111111111111111111111111111111111111112"
    text = await core.handle_message(mint, chat_id=43)
    assert "DEX/ончейн" in text
    assert core.get_engine(43) == ENGINE_V1


async def test_v2_still_analyzes_after_switch_back(core: AssistantCore):
    await core.handle_callback("engine:v1", chat_id=8)
    await core.handle_callback("engine:v2", chat_id=8)
    text = await core.handle_message("проанализируй TITAN", chat_id=8)
    assert text.split("\n", 1)[0].startswith("**Вердикт:")
    assert "## 🛡️ Безопасность" in text


async def test_unknown_callback_does_not_change_engine(core: AssistantCore):
    text = await core.handle_callback("menu:home", chat_id=9)
    assert "Неизвестный" in text
    assert core.get_engine(9) == ENGINE_V2
