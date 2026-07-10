"""Read-time schema migrations: v1 -> v2 (ar backfill, transient-flag scrub), lazy on-disk,
and the index-rebuild path going through the same choke point. Fully offline."""
import base64
import json

import httpx
import pytest

from app.main import app
from app.novelai._png import solid_png
from app.settings import Settings, VaultSettings, get_settings
from app.vault.migrate import CURRENT, load_doc


def _v1_doc() -> dict:
    return {
        "schema_version": 1, "id": "w1", "title": "Old work",
        "params": {"seed": 1},
        "canvas": {"viewport": {"x": 0, "y": 0, "zoom": 1}, "nodes": [
            {"id": "block-1", "type": "block", "position": {"x": 0, "y": 0},
             "data": {"name": "B", "text": "1girl", "_cw": "176px", "_ch": "34px"}},  # leaked by old builds
            {"id": "img-1", "type": "image", "position": {"x": 0, "y": 0}, "data": {}},
        ]},
        "snapshots": [{"id": "s1", "hash": "h", "params": {"width": 832, "height": 1216, "seed": 42}}],
        "images": [
            {"id": "img-1", "snapshot_id": "s1", "file": "images/img-1.png"},
            {"id": "img-2", "snapshot_id": "s-missing", "file": "images/img-2.png"},  # dangling ref
        ],
        "stack": [], "preview_image_id": "img-1",
    }


def test_v1_migrates_to_current_with_ar_backfill():
    doc = load_doc(json.dumps(_v1_doc()))
    assert doc.schema_version == CURRENT == 2
    assert doc.images[0].ar == pytest.approx(832 / 1216)  # backfilled from the snapshot params
    assert doc.images[0].role == "gallery"                # model default covers v1 (no migration needed)
    assert doc.images[1].ar is None                       # dangling snapshot ref -> no backfill, no crash
    block = next(n for n in doc.canvas["nodes"] if n["id"] == "block-1")
    assert "_cw" not in block["data"] and "_ch" not in block["data"]
    assert block["data"]["text"] == "1girl"               # domain data untouched


def test_migration_is_idempotent_on_v2():
    once = load_doc(json.dumps(_v1_doc()))
    twice = load_doc(once.model_dump_json())
    assert twice == once


def test_future_version_loads_as_is_with_warning(caplog):
    raw = _v1_doc() | {"schema_version": 99}
    with caplog.at_level("WARNING"):
        doc = load_doc(json.dumps(raw))
    assert doc.schema_version == 99   # preserved — an older app degrades, never crashes
    assert doc.images[0].ar is None   # and no migration ran
    assert any("newer build" in r.message for r in caplog.records)


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


async def test_v1_on_disk_loads_lazily_and_reindexes(client):
    ac, vault = client
    # Save through the API (initialises the vault), then regress the on-disk file to v1.
    work = {
        "id": "w1", "title": "Old",
        "snapshots": [{"id": "s1", "hash": "h", "params": {"width": 832, "height": 1216, "seed": 42},
                       "components": [{"source": "custom", "name": "c", "text": "1girl", "tags": ["t"]}]}],
        "images": [{"id": "img-1", "snapshot_id": "s1",
                    "image_b64": base64.b64encode(solid_png(16, 16)).decode("ascii")}],
        "preview_image_id": "img-1",
    }
    assert (await ac.put("/api/vault/works", json=work)).status_code == 200
    wj = next((vault / "works").glob("*/work.json"))
    raw = json.loads(wj.read_text("utf-8"))
    raw["schema_version"] = 1
    for im in raw["images"]:
        im.pop("ar", None)
        im.pop("role", None)
    wj.write_text(json.dumps(raw), "utf-8")

    body = (await ac.get("/api/vault/works/w1")).json()
    assert body["schema_version"] == 2
    assert body["images"][0]["ar"] == pytest.approx(832 / 1216)
    # Lazy migration: reads never rewrite the file — only the next save will.
    assert json.loads(wj.read_text("utf-8"))["schema_version"] == 1

    # The rebuild path migrates through the same choke point: drop the index and list.
    for f in vault.glob(".index.sqlite*"):
        f.unlink()
    works = (await ac.get("/api/vault/works")).json()
    assert works["total"] == 1 and works["items"][0]["image_count"] == 1
