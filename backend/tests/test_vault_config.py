"""Vault config: choose/report the vault folder, offline (tmp_path, no real OS config dir)."""
import httpx
import pytest

from app.main import app
from app.settings import Settings, VaultSettings, get_settings


@pytest.fixture
async def client(tmp_path):
    # state_dir pins the runtime pointer into tmp so the real OS config dir is never touched.
    app.dependency_overrides[get_settings] = lambda: Settings(
        vault=VaultSettings(dir=None, state_dir=str(tmp_path / "state")), _env_file=None
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, tmp_path
    app.dependency_overrides.clear()


async def test_config_uninitialized(client):
    ac, _ = client
    resp = await ac.get("/api/vault/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["vault_dir"] is None
    assert body["initialized"] is False
    assert body["proposed_default"]


async def test_set_config_initializes_and_persists(client):
    ac, tmp = client
    vdir = tmp / "myvault"
    resp = await ac.put("/api/vault/config", json={"vault_dir": str(vdir)})
    assert resp.status_code == 200
    assert resp.json()["initialized"] is True
    assert (vdir / ".vault.json").is_file()
    assert (vdir / "works").is_dir()
    assert (vdir / "library" / "blocks").is_dir()

    # The choice persists (pointer): a fresh GET now reports it.
    again = await ac.get("/api/vault/config")
    assert again.json()["vault_dir"] == str(vdir)
    assert again.json()["initialized"] is True


async def test_set_config_rejects_relative_path(client):
    ac, _ = client
    resp = await ac.put("/api/vault/config", json={"vault_dir": "relative/path"})
    assert resp.status_code == 400
