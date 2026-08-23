"""
Tests for Async Rate Limiter and Circuit Breaker.
"""
import asyncio

import pytest

from src.core.exceptions import CircuitBreakerOpenError
from src.core.rate_limiter import CircuitBreaker, CircuitState, TokenBucketRateLimiter


@pytest.mark.asyncio
async def test_token_bucket_acquire():
    limiter = TokenBucketRateLimiter(rate_per_second=100.0, capacity=10.0)
    # Fast acquire within capacity
    await limiter.acquire(1.0)
    assert limiter.tokens < 10.0


@pytest.mark.asyncio
async def test_circuit_breaker_tripping():
    cb = CircuitBreaker("test_circuit", failure_threshold=2, recovery_timeout=0.1)
    assert cb.state == CircuitState.CLOSED

    # First failure
    await cb.record_failure(Exception("error 1"))
    assert cb.state == CircuitState.CLOSED

    # Second failure trips breaker
    await cb.record_failure(Exception("error 2"))
    assert cb.state == CircuitState.OPEN

    # Breaker blocks calls when open
    with pytest.raises(CircuitBreakerOpenError):
        await cb.check_state()

    # Wait for recovery timeout
    await asyncio.sleep(0.15)
    await cb.check_state()
    assert cb.state == CircuitState.HALF_OPEN

    # Success closes breaker
    await cb.record_success()
    await cb.record_success()
    assert cb.state == CircuitState.CLOSED
