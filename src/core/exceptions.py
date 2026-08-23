"""
Domain Exceptions for Quantitative Crypto Futures Platform.
"""


class QuantPlatformException(Exception):
    """Base exception for all system errors."""


class ExchangeAPIError(QuantPlatformException):
    """Raised when an exchange API call fails."""
    def __init__(self, exchange: str, message: str, status_code: int = 0):
        super().__init__(f"[{exchange}] API Error: {message} (status: {status_code})")
        self.exchange = exchange
        self.status_code = status_code


class RateLimitExceededError(ExchangeAPIError):
    """Raised when API rate limits are hit."""


class CircuitBreakerOpenError(QuantPlatformException):
    """Raised when circuit breaker trips to protect the exchange connection."""


class MarketDataError(QuantPlatformException):
    """Raised when market data is unavailable or corrupted."""


class StaleDataError(MarketDataError):
    """Raised when candles or orderbook feeds are too old."""


class DataQualityDegradedError(MarketDataError):
    """Raised when data gaps or integrity issues violate thresholds."""


class FeatureComputationError(QuantPlatformException):
    """Raised when feature calculations fail."""


class SignalGenerationError(QuantPlatformException):
    """Raised when signal pipeline encounters an unrecoverable failure."""


class RiskLimitViolationError(QuantPlatformException):
    """Raised when a potential trade violates account risk constraints."""


class InsufficientMarginError(RiskLimitViolationError):
    """Raised when account equity cannot satisfy required margin."""


class BacktestExecutionError(QuantPlatformException):
    """Raised during backtest simulation failures."""


class PaperTradingError(QuantPlatformException):
    """Raised during paper trading state operations."""


class DatabaseError(QuantPlatformException):
    """Raised on persistence failures."""
