"""Vault management: first-run default, add/switch/delete/move — offline (tmp dirs only, no real home/config)."""
import asyncio

import httpx
import pytest

from app.main import app
from app.settings import Settings, VaultSettings, get_settings


@pytest.fixture
async def client(tmp_path):
    # state_dir → settings.json in tmp; default_dir → the auto-created first-run vault in tmp.
    app.dependency_overrides[get_settings] = lambda: Settings(
        vault=VaultSettings(dir=None, state_dir=str(tmp_path / "state"),
                            default_dir=str(tmp_path / "default-vault")), _env_file=None
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, tmp_path
    app.dependency_overrides.clear()


async def test_first_run_creates_default(client):
    ac, tmp = client
    body = (await ac.get("/api/vault/config")).json()
    default = tmp / "default-vault"
    assert body["active"] == str(default)
    assert (default / ".vault.json").is_file() and (default / "works").is_dir()
    assert any(v["dir"] == str(default) and v["active"] for v in body["vaults"])


async def test_add_and_switch_vault(client):
    ac, tmp = client
    await ac.get("/api/vault/config")  # creates the default
    v2 = tmp / "second"
    body = (await ac.post("/api/vault/vaults", json={"dir": str(v2)})).json()
    assert body["active"] == str(v2) and (v2 / ".vault.json").is_file()
    default = str(tmp / "default-vault")
    body2 = (await ac.put("/api/vault/active", json={"dir": default})).json()
    assert body2["active"] == default


async def test_add_rejects_relative_path(client):
    ac, _ = client
    resp = await ac.post("/api/vault/vaults", json={"dir": "relative/path"})
    assert resp.status_code == 400


async def test_switch_to_unknown_vault_404(client):
    ac, tmp = client
    await ac.get("/api/vault/config")
    resp = await ac.put("/api/vault/active", json={"dir": str(tmp / "nope")})
    assert resp.status_code == 404


async def test_delete_vault_is_physical(client):
    ac, tmp = client
    await ac.get("/api/vault/config")
    v2 = tmp / "second"
    await ac.post("/api/vault/vaults", json={"dir": str(v2)})
    assert v2.is_dir()
    body = (await ac.delete("/api/vault/vaults", params={"dir": str(v2)})).json()
    assert not v2.exists()  # folder physically removed
    assert not any(x["dir"] == str(v2) for x in body["vaults"])


async def test_move_vault_with_progress(client):
    ac, tmp = client
    await ac.get("/api/vault/config")
    src = tmp / "default-vault"
    (src / "keep.txt").write_text("hi", "utf-8")
    dst = tmp / "moved"
    assert (await ac.post("/api/vault/move", json={"src": str(src), "dst": str(dst)})).status_code == 200
    for _ in range(200):
        status = (await ac.get("/api/vault/move/status")).json()
        if not status["active"]:
            break
        await asyncio.sleep(0.02)
    assert status["error"] is None and status["done"] == status["total"]
    assert not src.exists() and (dst / "keep.txt").is_file()
    assert (await ac.get("/api/vault/config")).json()["active"] == str(dst)
