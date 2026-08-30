"""
Логирование v2.

Два режима:
  * человекочитаемый (по умолчанию) — для запуска в консоли/Telegram-хосте;
  * JSON (LOG_JSON=true) — для агрегаторов (Loki/CloudWatch/Datadog).

Каждая запись обязательно несёт ``component`` — по нему видно, какой модуль
уронил запрос: data.dex, scanner.l2, executor...
"""

from __future__ import annotations

import json
import logging
import sys
import time
from typing import Any

_CONFIGURED = False

# Модули, которые любят засыпать лог техническим мусором.
_NOISY = ("httpx", "httpcore", "aiohttp", "aiohttp.access", "aiogram", "asyncio", "urllib3")


class JsonFormatter(logging.Formatter):
    """Одна строка = один JSON-объект. Удобно грепать и отправлять в стек."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "component": record.name,
            "msg": record.getMessage(),
        }
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extra = getattr(record, "ctx", None)
        if isinstance(extra, dict) and extra:
            tail = " ".join(f"{k}={v}" for k, v in extra.items())
            return f"{base} | {tail}"
        return base


def setup_logging(level: str = "INFO", as_json: bool = False, force: bool = False) -> None:
    """Настраивает root-логгер. Повторные вызовы игнорируются (кроме force)."""
    global _CONFIGURED
    root = logging.getLogger()
    if _CONFIGURED and not force:
        root.setLevel(level.upper())
        return

    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if as_json
        else HumanFormatter("%(asctime)s | %(levelname)-7s | %(name)-18s | %(message)s", "%H:%M:%S")
    )
    root.addHandler(handler)
    root.setLevel(level.upper())

    for noisy in _NOISY:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(component: str) -> logging.Logger:
    """Логгер модуля. Имя обязательно в стиле ``scanner.l2``."""
    return logging.getLogger(component)


def bind(logger: logging.Logger, **ctx: Any) -> dict[str, Any]:
    """Вспомогательный способ передать контекст в запись: logger.info(msg, extra={'ctx': bind(...)})."""
    return {"ctx": ctx}
