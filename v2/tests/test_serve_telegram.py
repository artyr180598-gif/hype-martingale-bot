"""Команда serve должна поднимать HTTP и Telegram одновременно."""

from __future__ import annotations

import inspect

import v2.cli as cli


def test_cmd_serve_starts_telegram_transport():
    """serve запускает TelegramTransport рядом с HTTP (один токен — один поллер)."""
    src = inspect.getsource(cli.cmd_serve)
    assert "TelegramTransport" in src
    assert "asyncio.gather" in src
    assert "transport.enabled" in src
    assert "transport.start()" in src


def test_cmd_serve_keeps_http_healthcheck_alive():
    """HTTP (uvicorn) остаётся в serve и не заменяется на bot."""
    src = inspect.getsource(cli.cmd_serve)
    assert "config.serve()" in src
    assert "create_app" in src
    # separate bot-команда не используется в serve
    assert "cmd_bot" not in src or "cmd_bot(" not in src
