"""Offline test fixtures — no network, no real OS keychain (``rules/security.md``; tests stay offline)."""
import httpx
import pytest

from app.main import app


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
