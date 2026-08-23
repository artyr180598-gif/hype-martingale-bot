"""
Tests for FastAPI REST Endpoints.
"""
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import app


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["app_name"] == "CryptoFuturesQuantPlatform"


@pytest.mark.asyncio
async def test_dashboard_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "QUANTITATIVE FUTURES PLATFORM" in resp.text


@pytest.mark.asyncio
async def test_metrics_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert "tracked_symbols_count" in data


@pytest.mark.asyncio
async def test_paper_portfolio_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/paper/portfolio")
        assert resp.status_code == 200
        data = resp.json()
        assert "cash_balance" in data
        assert "total_equity" in data


@pytest.mark.asyncio
async def test_ai_query_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/ai/query", json={"query": "Как работает риск-менеджмент?"})
        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data
        assert len(data["response"]) > 0


@pytest.mark.asyncio
async def test_bot_execute_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/bot/execute", json={"command": "/start"})
        assert resp.status_code == 200
        data = resp.json()
        assert "Quantitative Crypto Futures" in data["reply_text"]
