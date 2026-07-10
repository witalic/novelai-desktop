"""The shell's per-launch shared secret guards /api (via the nai_auth cookie) and rejects non-loopback hosts.
Offline; the guard is a no-op unless a token is provisioned, so the rest of the suite is unaffected."""
import httpx
import pytest

from app.main import app
from app.settings import Settings, VaultSettings, get_settings


@pytest.fixture
async def guarded(tmp_path):
    app.dependency_overrides[get_settings] = lambda: Settings(
        vault=VaultSettings(state_dir=str(tmp_path / "state"), default_dir=str(tmp_path / "v")), _env_file=None)
    app.state.auth_token = "s3cret"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as ac:
        yield ac
    app.state.auth_token = ""  # reset the shared module app for other tests
    app.dependency_overrides.clear()


async def test_api_requires_the_shared_secret(guarded):
    assert (await guarded.get("/api/settings")).status_code == 403                                  # no cookie
    assert (await guarded.get("/api/settings", cookies={"nai_auth": "wrong"})).status_code == 403   # bad token
    assert (await guarded.get("/api/settings", cookies={"nai_auth": "s3cret"})).status_code == 200  # ok


async def test_non_loopback_host_is_rejected(guarded):
    # DNS-rebinding: an attacker's domain resolving to 127.0.0.1 carries its own Host header.
    r = await guarded.get("/api/settings", cookies={"nai_auth": "s3cret"}, headers={"host": "evil.example.com"})
    assert r.status_code == 400


async def test_health_stays_open(guarded):
    assert (await guarded.get("/health")).status_code == 200  # not /api — Electron polls it before the UI loads
