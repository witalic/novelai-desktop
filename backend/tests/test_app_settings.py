"""Persisted app settings (config dir): defaults, patch/persist, and resilience to a corrupt file."""
import httpx
import pytest

from app.main import app
from app.settings import Settings, VaultSettings, get_settings


@pytest.fixture
async def client(tmp_path):
    app.dependency_overrides[get_settings] = lambda: Settings(
        vault=VaultSettings(dir=None, state_dir=str(tmp_path / "state"),
                            default_dir=str(tmp_path / "default-vault")), _env_file=None
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, tmp_path
    app.dependency_overrides.clear()


async def test_defaults_and_patch_persist(client):
    ac, _ = client
    body = (await ac.get("/api/settings")).json()
    assert body["autosave_interval_s"] == 300 and body["theme"] == "dark"
    patched = (await ac.patch("/api/settings", json={"theme": "light", "autosave_interval_s": 600})).json()
    assert patched["theme"] == "light" and patched["autosave_interval_s"] == 600
    assert (await ac.get("/api/settings")).json()["theme"] == "light"  # persisted to disk


async def test_corrupt_file_falls_back_to_defaults(client):
    ac, tmp = client
    (tmp / "state").mkdir(parents=True, exist_ok=True)
    (tmp / "state" / "settings.json").write_text("{ not valid json", "utf-8")
    body = (await ac.get("/api/settings")).json()
    assert body["theme"] == "dark" and body["autosave_interval_s"] == 300


async def test_out_of_range_interval_ignored(client):
    ac, _ = client
    patched = (await ac.patch("/api/settings", json={"autosave_interval_s": 5})).json()
    assert patched["autosave_interval_s"] == 300  # too small → coerced back to default


async def test_novelai_token_lifecycle(client, monkeypatch):
    ac, _ = client
    from app import keychain
    store: dict[str, str] = {}
    monkeypatch.setattr(keychain, "has_novelai_token", lambda: bool(store.get("t")))
    monkeypatch.setattr(keychain, "set_novelai_token", lambda tok: store.__setitem__("t", tok))
    monkeypatch.setattr(keychain, "delete_novelai_token", lambda: store.pop("t", None))

    assert (await ac.get("/api/settings/novelai-token")).json()["set"] is False
    assert (await ac.put("/api/settings/novelai-token", json={"token": "  "})).status_code == 400
    assert (await ac.put("/api/settings/novelai-token", json={"token": "secret"})).json()["set"] is True
    assert (await ac.get("/api/settings/novelai-token")).json()["set"] is True
    assert (await ac.delete("/api/settings/novelai-token")).json()["set"] is False
    assert "t" not in store
