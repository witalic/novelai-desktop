"""Offline test fixtures — no network, no real OS keychain (``rules/security.md``; tests stay offline)."""
import httpx
import keyring
import pytest

from app.main import app


@pytest.fixture(autouse=True)
def _mock_keychain(monkeypatch):
    """Back the keychain with an in-memory store for EVERY test, so no test can read/write the real OS
    keychain (a forgotten override would otherwise leak the developer's actual NovelAI token)."""
    store: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(keyring, "get_password", lambda s, u: store.get((s, u)))
    monkeypatch.setattr(keyring, "set_password", lambda s, u, p: store.__setitem__((s, u), p))
    monkeypatch.setattr(keyring, "delete_password", lambda s, u: store.pop((s, u), None))


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
