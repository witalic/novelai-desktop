"""Vault store + index: save/load/list/serve/gallery/rebuild — fully offline (tmp vault)."""
import base64

import httpx
import pytest

from app.main import app
from app.novelai._png import solid_png
from app.settings import Settings, VaultSettings, get_settings


@pytest.fixture
async def client(tmp_path):
    vault = tmp_path / "vault"
    app.dependency_overrides[get_settings] = lambda: Settings(
        vault=VaultSettings(dir=str(vault), state_dir=str(tmp_path / "state")), _env_file=None
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        await ac.put("/api/vault/config", json={"vault_dir": str(vault)})
        yield ac, vault
    app.dependency_overrides.clear()


def _png_b64() -> str:
    return base64.b64encode(solid_png(16, 16)).decode("ascii")


def _work(work_id: str = "w1", tags: list[str] | None = None) -> dict:
    tags = tags if tags is not None else ["silver hair", "night"]
    sid, iid = f"s-{work_id}", f"img-{work_id}"  # snapshot/image ids are globally unique (ULIDs in prod)
    return {
        "id": work_id, "title": "Park — Guweiz",
        "params": {"seed": 42, "model": "nai-diffusion-4-5-full"},
        "snapshots": [{
            "id": sid, "hash": "h1", "assembled_positive": "1girl", "assembled_negative": "lowres",
            "components": [{"source": "library", "block_id": "b1", "name": "Char", "text": "1girl", "tags": tags}],
        }],
        "images": [{
            "id": iid, "snapshot_id": sid, "favorite": True, "tags": ["favorite"],
            "params": {"seed": 42, "model": "nai-diffusion-4-5-full"}, "image_b64": _png_b64(),
        }],
        "preview_image_id": iid,
    }


async def test_save_load_roundtrip(client):
    ac, vault = client
    assert (await ac.put("/api/vault/works", json=_work())).status_code == 200
    work_jsons = list((vault / "works").glob("*/work.json"))
    assert len(work_jsons) == 1
    wdir = work_jsons[0].parent
    assert (wdir / "images" / "img-w1.png").is_file()
    assert (wdir / "images" / "img-w1.json").is_file()
    assert (wdir / "preview.png").is_file()

    body = (await ac.get("/api/vault/works/w1")).json()
    assert body["title"] == "Park — Guweiz"
    assert body["images"][0]["file"] == "images/img-w1.png"
    assert body["images"][0].get("image_b64") is None  # write-only, never persisted


async def test_list_image_preview(client):
    ac, _ = client
    await ac.put("/api/vault/works", json=_work())
    page = (await ac.get("/api/vault/works")).json()
    assert page["total"] == 1 and page["items"][0]["preview_url"]
    assert (await ac.get("/api/vault/works/w1/images/img-w1")).status_code == 200
    assert (await ac.get("/api/vault/works/w1/preview")).status_code == 200


async def test_gallery_tag_and_favorite_filters(client):
    ac, _ = client
    await ac.put("/api/vault/works", json=_work("w1", tags=["genshin", "hu tao"]))
    await ac.put("/api/vault/works", json=_work("w2", tags=["landscape"]))
    only_genshin = (await ac.get("/api/vault/gallery", params={"tags": ["genshin"]})).json()
    assert only_genshin["total"] == 1 and only_genshin["items"][0]["work_id"] == "w1"
    favs = (await ac.get("/api/vault/gallery", params={"favorite": "true"})).json()
    assert favs["total"] == 2


async def test_index_rebuilds_when_deleted(client):
    ac, vault = client
    await ac.put("/api/vault/works", json=_work())
    (vault / ".index.sqlite").unlink()
    assert (await ac.get("/api/vault/works")).json()["total"] == 1


async def test_bad_id_and_missing_image(client):
    ac, _ = client
    await ac.put("/api/vault/works", json=_work())
    assert (await ac.get("/api/vault/works/a.b/images/x")).status_code == 400
    assert (await ac.get("/api/vault/works/w1/images/nope")).status_code == 404
