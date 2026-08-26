"""Настройка логирования."""

import logging
import sys


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def setup_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S")
    )
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Не даём сторонним библиотекам заливать лог
    for noisy in ("httpx", "httpcore", "aiohttp.access", "aiogram"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
