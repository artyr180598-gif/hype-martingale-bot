"""
Слой отказоустойчивости HTTP: ретраи, предохранитель, лимит запросов.

Это то, из-за чего «бот не падает при сбоях API». Проверяем на
httpx.MockTransport — без реальной сети.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from v2.config import V2Config
from v2.core.errors import ProviderUnavailable
from v2.data.http_client import AsyncHttpClient, CircuitBreaker, TokenBucket


def _config(**overrides) -> V2Config:
    base = dict(
        _env_file=None,
        HTTP_TIMEOUT_SECONDS=2.0,
        HTTP_MAX_RETRIES=2,
        HTTP_BACKOFF_BASE=0.001,
        HTTP_CONCURRENCY=4,
        REQUESTS_PER_SECOND=1000.0,
        CIRCUIT_FAILURE_THRESHOLD=3,
        CIRCUIT_COOLDOWN_SECONDS=0.2,
    )
    base.update(overrides)
    return V2Config(**base)


def _client_with(handler, **overrides) -> AsyncHttpClient:
    client = AsyncHttpClient(_config(**overrides), name="test")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


async def test_success_returns_json():
    client = _client_with(lambda request: httpx.Response(200, json={"ok": True}))
    assert await client.get_json("https://example.com/data") == {"ok": True}
    await client.close()


async def test_retries_on_500_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500) if calls["n"] < 3 else httpx.Response(200, json={"ok": 1})

    client = _client_with(handler)
    payload = await client.get_json("https://example.com/flaky")
    assert payload == {"ok": 1}
    assert calls["n"] == 3
    assert client.retries_made == 2
    await client.close()


async def test_retries_exhausted_raises_provider_unavailable():
    client = _client_with(lambda request: httpx.Response(503))
    with pytest.raises(ProviderUnavailable):
        await client.get_json("https://example.com/down")
    await client.close()


async def test_429_is_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429) if calls["n"] == 1 else httpx.Response(200, json=[])

    client = _client_with(handler)
    assert await client.get_json("https://example.com/limited") == []
    await client.close()


async def test_4xx_fails_fast_without_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, json={"error": "not found"})

    client = _client_with(handler)
    with pytest.raises(ProviderUnavailable):
        await client.get_json("https://example.com/missing")
    assert calls["n"] == 1          # нет смысла повторять 404
    await client.close()


async def test_circuit_breaker_opens_after_threshold():
    client = _client_with(lambda request: httpx.Response(500), CIRCUIT_FAILURE_THRESHOLD=2,
                          CIRCUIT_COOLDOWN_SECONDS=30.0)
    for _ in range(2):
        with pytest.raises(ProviderUnavailable):
            await client.get_json("https://example.com/broken")
    # предохранитель открыт — следующий вызов не идёт в сеть вовсе
    with pytest.raises(ProviderUnavailable):
        await client.get_json("https://example.com/broken")
    assert client.stats()["open_circuits"] == ["example.com"]
    await client.close()


def test_circuit_breaker_half_open_after_cooldown():
    breaker = CircuitBreaker(threshold=2, cooldown=0.01)
    breaker.on_failure()
    breaker.on_failure()
    assert breaker.is_open
    import time

    time.sleep(0.02)
    assert not breaker.is_open


async def test_token_bucket_limits_rate():
    bucket = TokenBucket(rate=50.0, burst=2)
    await bucket.acquire()
    await bucket.acquire()
    started = asyncio.get_running_loop().time()
    await bucket.acquire()          # третий токен нужно заработать
    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed >= 0.01


async def test_concurrency_semaphore_caps_parallel_requests():
    active = {"now": 0, "max": 0}

    async def handler(request):
        active["now"] += 1
        active["max"] = max(active["max"], active["now"])
        await asyncio.sleep(0.01)
        active["now"] -= 1
        return httpx.Response(200, json={})

    client = AsyncHttpClient(_config(HTTP_CONCURRENCY=2), name="test")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await asyncio.gather(*(client.get_json(f"https://example.com/{i}") for i in range(8)))
    assert active["max"] <= 2
    await client.close()
