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

        self.source, self._mode = build_source(self.settings)
        self.engine = AnalysisEngine(self.source, self.settings)
        self.scanner = UniverseScanner(self.source, self.engine, self.settings, self.store)
        self.watcher = WatchlistEngine(self.source, self.engine, self.settings, self.store)
        logger.info("Источник данных создан: mode=%s", self._mode)

    @property
    def mode(self) -> str:
        """Актуальный режим источника (уточняется после первого обращения к бирже)."""
        return getattr(self.source, "mode", self._mode) if self.source is not None else self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        self._mode = value

    async def ensure_ready(self) -> str:
        """
        Асинхронно выбирает живой источник данных (Bybit → Binance → MEXC → demo).
        Безопасно вызывать из event loop — вызывается при старте приложения.
        """
        self.ensure_services()
        probe = getattr(self.source, "probe", None)
        if probe is not None:
            try:
                self._mode = await probe()
            except Exception as e:  # noqa: BLE001
                logger.error("Не удалось выбрать источник данных: %s", e)
        else:
            self._mode = getattr(self.source, "mode", self._mode)
        logger.info("Источник данных активен: mode=%s", self._mode)
        return self._mode


ctx = AppContext()


def get_context() -> AppContext:
    return ctx
