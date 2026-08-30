"""
Доменные исключения v2.

Правило: любое исключение, прилетающее из сети/биржи/блокчейна, обязано быть
обёрнуто в один из этих типов. Тогда вызывающий код может отличить
«провайдер временно лёг» (ProviderUnavailable → ретрай/деградация) от
«токен не существует» (TokenNotFound → мгновенный отказ без ретраев).
"""

from __future__ import annotations


class V2Error(Exception):
    """Базовое исключение v2."""


class ConfigError(V2Error):
    """Некорректная конфигурация (несовместимые флаги, пустой ключ и т.п.)."""


class ProviderError(V2Error):
    """Ошибка внешнего провайдера данных."""

    def __init__(self, message: str, provider: str = "", retryable: bool = False) -> None:
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable


class ProviderUnavailable(ProviderError):
    """Провайдер недоступен (таймаут, 5xx, DNS, circuit breaker открыт)."""

    def __init__(self, message: str, provider: str = "") -> None:
        super().__init__(message, provider=provider, retryable=True)


class RateLimited(ProviderError):
    """Провайдер ответил 429 — нужно замедлиться."""

    def __init__(self, message: str = "rate limit", provider: str = "") -> None:
        super().__init__(message, provider=provider, retryable=True)


class TokenNotFound(ProviderError):
    """Токен/пул не найден ни у одного провайдера."""

    def __init__(self, message: str, provider: str = "") -> None:
        super().__init__(message, provider=provider, retryable=False)


class InsufficientData(V2Error):
    """Данных недостаточно для расчёта (мало свечей, пустой стакан)."""


class RiskRejected(V2Error):
    """Сделка отклонена риск-менеджером до отправки ордера."""
