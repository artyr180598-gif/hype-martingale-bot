"""Доменные исключения."""


class AdvisorError(Exception):
    """Базовая ошибка советника."""


class DataSourceError(AdvisorError):
    """Ошибка источника данных (биржа недоступна и т.п.)."""


class AnalysisError(AdvisorError):
    """Недостаточно данных / ошибка анализа."""


class NotEnoughData(AnalysisError):
    """Не хватает свечей для анализа."""
