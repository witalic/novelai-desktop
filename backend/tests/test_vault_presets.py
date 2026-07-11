"""Presets: built-ins + user CRUD, exclusive default with fallback, favourites, immutability — all
offline (tmp vault, ASGITransport)."""
import json

import httpx
import pytest

from app.main import app
from app.settings import Settings, VaultSettings, get_settings


@pytest.fixture
async def client(tmp_path):
    vault = tmp_path / "vault"
    app.dependency_overrides[get_settings] = lambda: Settings(
        vault=VaultSettings(dir=str(vault), state_dir=str(tmp_path / "state")), _env_file=None
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, vault
    app.dependency_overrides.clear()


def _user_preset(pid="p1", name="My portrait", **params) -> dict:
    p = {"model": "nai-diffusion-4-5-full", "width": 832, "height": 1216, "steps": 30, "scale": 5.5,
         "sampler": "k_dpmpp_2s_ancestral", "n_samples": 1, "noise_schedule": "karras",
         "cfg_rescale": 0.0, "quality_toggle": True, "uc_preset": 4}
    p.update(params)
    return {"id": pid, "name": name, "params": p}


async def test_builtins_present_with_default(client):
    ac, _ = client
    items = (await ac.get("/api/vault/presets")).json()
    assert len(items) == 5 and all(p["builtin"] for p in items)
    default = [p for p in items if p["is_default"]]
    assert len(default) == 1 and default[0]["id"] == "builtin-anime-full"
    anime = default[0]["params"]
    assert anime["model"] == "nai-diffusion-4-5-full" and anime["width"] == 832 and anime["uc_preset"] == 4
    assert "seed" not in anime and "prompt" not in anime  # params-only invariant


async def test_create_update_delete_user_preset(client):
    ac, vault = client
    assert (await ac.post("/api/vault/presets", json=_user_preset())).status_code == 200
    assert (vault / "presets" / "p1.json").is_file()

    items = (await ac.get("/api/vault/presets")).json()
    mine = next(p for p in items if p["id"] == "p1")
    assert mine["builtin"] is False and mine["params"]["steps"] == 30 and mine["created_at"]
    created = mine["created_at"]

    # update overwrites params, keeps created_at, bumps updated_at
    await ac.post("/api/vault/presets", json=_user_preset(steps=42))
    mine = next(p for p in (await ac.get("/api/vault/presets")).json() if p["id"] == "p1")
    assert mine["params"]["steps"] == 42 and mine["created_at"] == created and mine["updated_at"]

    assert (await ac.delete("/api/vault/presets/p1")).status_code == 200
    assert not (vault / "presets" / "p1.json").exists()
    assert all(p["id"] != "p1" for p in (await ac.get("/api/vault/presets")).json())


async def test_default_is_exclusive_and_falls_back_on_delete(client):
    ac, _ = client
    await ac.post("/api/vault/presets", json=_user_preset("p1", "A"))
    assert (await ac.put("/api/vault/presets/default", json={"id": "p1"})).status_code == 200
    items = (await ac.get("/api/vault/presets")).json()
    assert [p["id"] for p in items if p["is_default"]] == ["p1"]  # exactly one, the new one

    # deleting the default preset falls the default back to the built-in Anime · Full
    await ac.delete("/api/vault/presets/p1")
    items = (await ac.get("/api/vault/presets")).json()
    assert [p["id"] for p in items if p["is_default"]] == ["builtin-anime-full"]


async def test_favourite_a_builtin_persists(client):
    ac, _ = client
    await ac.put("/api/vault/presets/builtin-wallpaper/favorite", json={"favorite": True})
    fav = next(p for p in (await ac.get("/api/vault/presets")).json() if p["id"] == "builtin-wallpaper")
    assert fav["favorite"] is True
    await ac.put("/api/vault/presets/builtin-wallpaper/favorite", json={"favorite": False})
    fav = next(p for p in (await ac.get("/api/vault/presets")).json() if p["id"] == "builtin-wallpaper")
    assert fav["favorite"] is False


async def test_builtins_are_immutable(client):
    ac, _ = client
    # can't create/overwrite a builtin- id, nor delete one
    assert (await ac.post("/api/vault/presets", json=_user_preset("builtin-anime-full", "hijack"))).status_code == 400
    assert (await ac.delete("/api/vault/presets/builtin-anime-full")).status_code == 400
    # but a built-in CAN be made the default (overlay only)
    assert (await ac.put("/api/vault/presets/default", json={"id": "builtin-wallpaper"})).status_code == 200
    assert next(p for p in (await ac.get("/api/vault/presets")).json() if p["is_default"])["id"] == "builtin-wallpaper"


async def test_invalid_and_missing_ids(client):
    ac, _ = client
    assert (await ac.post("/api/vault/presets", json=_user_preset("bad id!", "x"))).status_code == 400
    assert (await ac.post("/api/vault/presets", json=_user_preset("p1", "  "))).status_code == 400  # name required
    assert (await ac.put("/api/vault/presets/default", json={"id": "ghost"})).status_code == 404
    assert (await ac.put("/api/vault/presets/ghost/favorite", json={"favorite": True})).status_code == 404
    assert (await ac.delete("/api/vault/presets/ghost")).status_code == 404


async def test_corrupt_state_is_tolerated(client):
    ac, vault = client
    await ac.get("/api/vault/presets")  # inits the vault tree
    (vault / "presets" / ".state.json").write_text("{ not json", "utf-8")
    items = (await ac.get("/api/vault/presets")).json()  # never 500s
    assert next(p for p in items if p["is_default"])["id"] == "builtin-anime-full"
