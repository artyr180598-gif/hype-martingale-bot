"""
Глобальный контекст приложения: источник данных, хранилище, движки.
Создаётся лениво, чтобы тесты могли подменять компоненты.
"""

from __future__ import annotations

from src.config.settings import settings
from src.core.logging import get_logger
from src.core.store import Store

logger = get_logger("core.context")


class AppContext:
    def __init__(self) -> None:
        self.settings = settings
        self.store = Store(settings.db_path)
        self.source = None  # MarketDataSource
        self.engine = None  # AnalysisEngine
        self.scanner = None  # UniverseScanner
        self.watcher = None  # WatchlistEngine
        self.bot = None  # TelegramAdvisorBot
        self.mode = "unknown"
        self.started = False

    def ensure_services(self) -> None:
        """Инициализирует источник данных (live → demo fallback) и движки."""
        if self.source is not None:
            return
        from src.analysis.engine import AnalysisEngine
        from src.data.collector import build_source
        from src.universe.scanner import UniverseScanner, WatchlistEngine

        self.source, self.mode = build_source(self.settings)
        logger.info("Источник данных: mode=%s source=%s", self.mode, type(self.source).__name__)
        self.engine = AnalysisEngine(self.source, self.settings)
        self.scanner = UniverseScanner(self.source, self.engine, self.settings, self.store)
        self.watcher = WatchlistEngine(self.source, self.engine, self.settings, self.store)


ctx = AppContext()


def get_context() -> AppContext:
    return ctx
