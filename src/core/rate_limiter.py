"""
Async Rate Limiter and Circuit Breaker for Exchange APIs.
"""
import asyncio
import random
import time
from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar

from src.core.exceptions import CircuitBreakerOpenError, QuantPlatformException
from src.core.logging import get_logger

logger = get_logger("core.rate_limiter")
T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "CLOSED"         # Normal operation
    OPEN = "OPEN"             # Failing, block calls
    HALF_OPEN = "HALF_OPEN"   # Testing if service recovered


class TokenBucketRateLimiter:
    """
    Async Token Bucket Rate Limiter.
    Enforces a sustained request rate with burst capacity.
    """

    def __init__(self, rate_per_second: float, capacity: float):
        self.rate_per_second = max(0.1, rate_per_second)
        self.capacity = max(1.0, capacity)
        self.tokens = self.capacity
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens_needed: float = 1.0) -> None:
        """Wait until enough tokens are available."""
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self.last_update
                self.last_update = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_second)

                if self.tokens >= tokens_needed:
                    self.tokens -= tokens_needed
                    return

                # Calculate needed sleep time
                deficit = tokens_needed - self.tokens
                sleep_time = deficit / self.rate_per_second

            await asyncio.sleep(sleep_time)


class CircuitBreaker:
    """
    Circuit Breaker pattern to protect against failing remote endpoints.
    Transitions: CLOSED -> OPEN (on failure threshold) -> HALF_OPEN (after recovery timeout) -> CLOSED.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_successes: int = 2,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_successes = half_open_successes

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_state_change = time.monotonic()
        self._lock = asyncio.Lock()

    async def check_state(self) -> None:
        """Check if circuit allows execution."""
        async with self._lock:
            now = time.monotonic()
            if self.state == CircuitState.OPEN:
                if now - self.last_state_change >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0
                    self.last_state_change = now
                    logger.info(
                        "Circuit breaker half-open: attempting test calls",
                        circuit=self.name,
                    )
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is OPEN. Failing fast."
                    )

    async def record_success(self) -> None:
        """Record successful call."""
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.half_open_successes:
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    self.last_state_change = time.monotonic()
                    logger.info("Circuit breaker closed: connection restored", circuit=self.name)
            elif self.state == CircuitState.CLOSED:
                self.failure_count = max(0, self.failure_count - 1)

    async def record_failure(self, error: Exception) -> None:
        """Record failed call."""
        async with self._lock:
            self.failure_count += 1
            now = time.monotonic()
            if self.state in (CircuitState.CLOSED, CircuitState.HALF_OPEN):
                if self.failure_count >= self.failure_threshold or self.state == CircuitState.HALF_OPEN:
                    self.state = CircuitState.OPEN
                    self.last_state_change = now
                    logger.error(
                        "Circuit breaker tripped OPEN",
                        circuit=self.name,
                        failure_count=self.failure_count,
                        error=str(error),
                    )


async def execute_with_retry(
    func: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    backoff_base: float = 1.0,
    circuit_breaker: CircuitBreaker | None = None,
    rate_limiter: TokenBucketRateLimiter | None = None,
    **kwargs: Any,
) -> Any:
    """
    Execute an async function with rate limiting, circuit breaker, and exponential backoff retry.
    """
    if circuit_breaker:
        await circuit_breaker.check_state()

    if rate_limiter:
        await rate_limiter.acquire()

    attempt = 0
    last_error: Exception | None = None

    while attempt < max_retries:
        try:
            res = await func(*args, **kwargs)
            if circuit_breaker:
                await circuit_breaker.record_success()
            return res
        except Exception as e:
            attempt += 1
            last_error = e
            if circuit_breaker:
                await circuit_breaker.record_failure(e)

            if attempt >= max_retries:
                break

            # Calculate exponential backoff with jitter
            delay = (backoff_base * (2 ** (attempt - 1))) + random.uniform(0.05, 0.25)
            logger.warning(
                "Operation failed, retrying...",
                attempt=attempt,
                max_retries=max_retries,
                delay=round(delay, 2),
                error=str(e),
            )
            await asyncio.sleep(delay)

            if circuit_breaker:
                await circuit_breaker.check_state()
            if rate_limiter:
                await rate_limiter.acquire()

    if last_error:
        raise last_error
    raise QuantPlatformException("Retry loop exited without result or exception")
