"""
Асинхронный HTTP-клиент для всех внешних провайдеров.

Что внутри (и зачем):
  * httpx.AsyncClient — неблокирующий I/O, один пул соединений на процесс;
  * token-bucket на хост — не ловим 429 от DexScreener/GoPlus при параллельном
    скане десятков токенов;
  * ретраи с экспоненциальной задержкой + джиттером — только на идемпотентных
    ошибках (таймаут, 5xx, 429);
  * circuit breaker на хост — если провайдер лёг, перестаём в него стучаться
    на CIRCUIT_COOLDOWN_SECONDS и сразу отдаём ProviderUnavailable, чтобы
    сканер деградировал быстро, а не висел по 10 секунд на каждом токене;
  * глобальный семафор HTTP_CONCURRENCY — защита своего же канала.

Все ошибки пишутся в ErrorMonitor: по ним видно, какой провайдер деградировал.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from v2.config import V2Config
from v2.core.errors import ProviderUnavailable, RateLimited
from v2.core.logging import get_logger
from v2.core.monitor import monitor

logger = get_logger("data.http")


class TokenBucket:
    """Простейший лимитер: N токенов в секунду, накопление до burst."""

    def __init__(self, rate: float, burst: int = 5) -> None:
        self.rate = max(0.1, rate)
        self.capacity = max(1, burst)
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(wait)


class CircuitBreaker:
    """Предохранитель на хост: closed → open → half-open."""

    def __init__(self, threshold: int, cooldown: float) -> None:
        self.threshold = threshold
        self.cooldown = cooldown
        self.failures = 0
        self.opened_at = 0.0

    @property
    def is_open(self) -> bool:
        if self.failures < self.threshold:
            return False
        return (time.monotonic() - self.opened_at) < self.cooldown

    def on_success(self) -> None:
        self.failures = 0

    def on_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.threshold and self.opened_at == 0.0:
            self.opened_at = time.monotonic()
            logger.warning("circuit open: %d ошибок подряд, пауза %.0fс", self.failures, self.cooldown)
        if self.failures >= self.threshold and (time.monotonic() - self.opened_at) >= self.cooldown:
            # half-open: даём один пробный запрос
            self.opened_at = time.monotonic()
            self.failures = max(0, self.threshold - 1)


class AsyncHttpClient:
    """Клиент с ретраями, лимитами и предохранителем."""

    def __init__(self, config: V2Config, name: str = "http") -> None:
        self.config = config
        self.name = name
        self._client: httpx.AsyncClient | None = None
        self._buckets: dict[str, TokenBucket] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._semaphore = asyncio.Semaphore(max(1, config.HTTP_CONCURRENCY))
        self.requests_made = 0
        self.retries_made = 0

    async def __aenter__(self) -> "AsyncHttpClient":
        self.client  # noqa: B018 — ленивая инициализация
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.config.HTTP_TIMEOUT_SECONDS),
                follow_redirects=True,
                headers={"User-Agent": f"{self.config.APP_NAME}/{self.config.APP_VERSION}"},
            )
        return self._client

    @staticmethod
    def _host(url: str) -> str:
        return urlparse(url).netloc or url

    def _bucket(self, host: str) -> TokenBucket:
        bucket = self._buckets.get(host)
        if bucket is None:
            bucket = TokenBucket(self.config.REQUESTS_PER_SECOND, burst=max(2, int(self.config.REQUESTS_PER_SECOND)))
            self._buckets[host] = bucket
        return bucket

    def _breaker(self, host: str) -> CircuitBreaker:
        breaker = self._breakers.get(host)
        if breaker is None:
            breaker = CircuitBreaker(self.config.CIRCUIT_FAILURE_THRESHOLD, self.config.CIRCUIT_COOLDOWN_SECONDS)
            self._breakers[host] = breaker
        return breaker

    async def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        component: str | None = None,
    ) -> Any:
        """GET с повторами. Возвращает распарсенный JSON (dict/list)."""
        component = component or self.name
        host = self._host(url)
        breaker = self._breaker(host)
        if breaker.is_open:
            monitor.record(component, f"circuit open для {host}")
            raise ProviderUnavailable(f"{host}: предохранитель открыт", provider=component)

        last_error: Exception | None = None
        for attempt in range(self.config.HTTP_MAX_RETRIES + 1):
            if attempt:
                delay = self.config.HTTP_BACKOFF_BASE * (2 ** (attempt - 1))
                delay += random.uniform(0, delay * 0.4)  # джиттер против синхронного шторма
                self.retries_made += 1
                await asyncio.sleep(delay)
            try:
                await self._bucket(host).acquire()
                async with self._semaphore:
                    self.requests_made += 1
                    response = await self.client.get(url, params=params, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                breaker.on_failure()
                logger.debug("%s: сетевая ошибка %s (попытка %d)", component, type(exc).__name__, attempt + 1)
                continue

            if response.status_code == 429:
                last_error = RateLimited(f"429 от {host}", provider=component)
                breaker.on_failure()
                continue
            if response.status_code >= 500:
                last_error = ProviderUnavailable(f"{response.status_code} от {host}", provider=component)
                breaker.on_failure()
                continue
            if response.status_code >= 400:
                breaker.on_success()
                monitor.record(component, f"HTTP {response.status_code} для {url}")
                raise ProviderUnavailable(
                    f"HTTP {response.status_code} для {url}", provider=component
                )
            try:
                payload = response.json()
            except ValueError as exc:
                last_error = exc
                breaker.on_failure()
                continue
            breaker.on_success()
            return payload

        monitor.record(component, last_error or "unknown error")
        raise ProviderUnavailable(f"{host}: {last_error}", provider=component)

    async def post_json(
        self,
        url: str,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        component: str | None = None,
    ) -> Any:
        """POST без ретраев: неидемпотентно (используется для AI/ордеров)."""
        component = component or self.name
        try:
            async with self._semaphore:
                self.requests_made += 1
                response = await self.client.post(url, json=json_body, headers=headers)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            monitor.record(component, exc)
            raise ProviderUnavailable(f"POST {url}: {exc}", provider=component) from exc

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def stats(self) -> dict[str, Any]:
        return {
            "requests": self.requests_made,
            "retries": self.retries_made,
            "open_circuits": [h for h, b in self._breakers.items() if b.is_open],
        }
