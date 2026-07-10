"""Library: blocks (save/list/filter/search/delete), categories (+custom colors), scoped tags."""
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


def _block(bid="b1", category="character", name="Silver-haired girl",
           text="1girl, silver hair", polarity="positive", tags=None) -> dict:
    return {
        "id": bid, "category": category, "name": name, "text": text,
        "polarity": polarity, "tags": tags if tags is not None else ["silver hair", "1girl"],
    }


async def test_save_and_list_block(client):
    ac, vault = client
    assert (await ac.post("/api/vault/library/blocks", json=_block())).status_code == 200
    assert (vault / "library" / "blocks" / "character" / "b1.json").is_file()

    page = (await ac.get("/api/vault/library/blocks")).json()
    assert page["total"] == 1
    item = page["items"][0]
    assert item["category"] == "character" and item["polarity"] == "positive"
    assert set(item["tags"]) == {"silver hair", "1girl"}


async def test_block_text_required(client):
    ac, _ = client
    resp = await ac.post("/api/vault/library/blocks", json=_block("b1", "custom", text="  "))
    assert resp.status_code == 400


async def test_filter_by_category_tags_search(client):
    ac, _ = client
    await ac.post("/api/vault/library/blocks", json=_block("b1", "character", tags=["a", "b"]))
    await ac.post("/api/vault/library/blocks", json=_block("b2", "style", name="Cinematic",
                                                           text="cinematic lighting", tags=["a"]))
    by_cat = (await ac.get("/api/vault/library/blocks", params={"category": "style"})).json()
    assert by_cat["total"] == 1 and by_cat["items"][0]["id"] == "b2"

    both_tags = (await ac.get("/api/vault/library/blocks", params={"tags": ["a", "b"]})).json()
    assert both_tags["total"] == 1 and both_tags["items"][0]["id"] == "b1"

    found = (await ac.get("/api/vault/library/blocks", params={"search": "cinematic"})).json()
    assert found["total"] == 1 and found["items"][0]["id"] == "b2"


async def test_categories_defaults_counts_and_custom(client):
    ac, _ = client
    await ac.post("/api/vault/library/blocks", json=_block("b1", "character"))
    cats = (await ac.get("/api/vault/library/categories")).json()
    char = next(c for c in cats if c["slug"] == "character")
    assert char["color"] == "#0c66e4" and char["count"] == 1

    made = (await ac.post("/api/vault/library/categories",
                          json={"name": "Line Art", "color": "#2fb8c6"})).json()
    # id is opaque (name-independent), not "line-art".
    assert made["slug"].startswith("cat-") and made["color"] == "#2fb8c6"
    cats2 = (await ac.get("/api/vault/library/categories")).json()
    assert any(c["slug"] == made["slug"] and c["name"] == "Line Art" and c["color"] == "#2fb8c6" for c in cats2)


async def test_category_counts_scoped_by_tags(client):
    ac, _ = client
    await ac.post("/api/vault/library/blocks", json=_block("b1", "character", tags=["x"]))
    await ac.post("/api/vault/library/blocks", json=_block("b2", "style", tags=["x", "y"]))
    await ac.post("/api/vault/library/blocks", json=_block("b3", "style", tags=["z"]))
    plain = {c["slug"]: c["count"] for c in (await ac.get("/api/vault/library/categories")).json()}
    assert plain["character"] == 1 and plain["style"] == 2
    scoped = {c["slug"]: c["count"] for c in
              (await ac.get("/api/vault/library/categories", params={"tags": ["x"]})).json()}
    assert scoped["character"] == 1 and scoped["style"] == 1  # only tag-x blocks counted


async def test_recolor_builtin_category(client):
    ac, _ = client
    made = (await ac.post("/api/vault/library/categories",
                          json={"name": "Style", "color": "#123456", "slug": "style"})).json()
    assert made["slug"] == "style"
    style = next(c for c in (await ac.get("/api/vault/library/categories")).json() if c["slug"] == "style")
    assert style["color"] == "#123456" and style["builtin"] is True  # recolored but still built-in


async def test_delete_category_reassigns_and_protects_custom(client):
    ac, _ = client
    # The Custom fallback can't be deleted.
    assert (await ac.delete("/api/vault/library/categories/custom")).status_code == 400
    # An empty built-in deletes cleanly (tombstoned).
    assert (await ac.delete("/api/vault/library/categories/style")).status_code == 200
    assert not any(c["slug"] == "style" for c in (await ac.get("/api/vault/library/categories")).json())
    # A non-empty category deletes too — its blocks move to Custom (never a hard block).
    made = (await ac.post("/api/vault/library/categories", json={"name": "Line Art", "color": "#2fb8c6"})).json()
    slug = made["slug"]
    await ac.post("/api/vault/library/blocks", json=_block("b1", slug))
    r = await ac.delete(f"/api/vault/library/categories/{slug}")
    assert r.status_code == 200 and r.json()["moved"] == 1
    cats = {c["slug"]: c["count"] for c in (await ac.get("/api/vault/library/categories")).json()}
    assert slug not in cats and cats["custom"] == 1
    moved = (await ac.get("/api/vault/library/blocks", params={"category": "custom"})).json()
    assert moved["total"] == 1 and moved["items"][0]["id"] == "b1"


async def test_unicode_category_name(client):
    ac, _ = client
    # A non-Latin name gets a usable opaque id (not "untitled") and works end-to-end.
    made = (await ac.post("/api/vault/library/categories",
                          json={"name": "лложло", "color": "#123456"})).json()
    assert made["slug"].startswith("cat-") and made["name"] == "лложло"
    await ac.post("/api/vault/library/blocks", json=_block("b1", made["slug"]))
    assert (await ac.delete(f"/api/vault/library/categories/{made['slug']}")).status_code == 200


async def test_deleted_builtin_can_be_recreated(client):
    ac, _ = client
    assert (await ac.delete("/api/vault/library/categories/pose")).status_code == 200
    assert not any(c["slug"] == "pose" for c in (await ac.get("/api/vault/library/categories")).json())
    # Re-creating with the same name un-hides it.
    await ac.post("/api/vault/library/categories", json={"name": "Pose", "color": "#ae4787", "slug": "pose"})
    assert any(c["slug"] == "pose" for c in (await ac.get("/api/vault/library/categories")).json())


async def test_tags_scoped_to_category(client):
    ac, _ = client
    await ac.post("/api/vault/library/blocks", json=_block("b1", "character", tags=["x", "y"]))
    await ac.post("/api/vault/library/blocks", json=_block("b2", "style", tags=["x"]))
    allt = {t["name"]: t["count"] for t in (await ac.get("/api/vault/library/tags")).json()}
    assert allt == {"x": 2, "y": 1}
    styled = (await ac.get("/api/vault/library/tags", params={"category": "style"})).json()
    assert {t["name"]: t["count"] for t in styled} == {"x": 1}


async def test_delete_block(client):
    ac, _ = client
    await ac.post("/api/vault/library/blocks", json=_block())
    assert (await ac.delete("/api/vault/library/blocks/b1")).status_code == 200
    assert (await ac.get("/api/vault/library/blocks")).json()["total"] == 0
    assert (await ac.delete("/api/vault/library/blocks/b1")).status_code == 404


async def test_recategorize_removes_stale_file(client):
    ac, vault = client
    await ac.post("/api/vault/library/blocks", json=_block("b1", "character"))
    await ac.post("/api/vault/library/blocks", json=_block("b1", "style"))
    assert not (vault / "library" / "blocks" / "character" / "b1.json").exists()
    assert (vault / "library" / "blocks" / "style" / "b1.json").is_file()
    page = (await ac.get("/api/vault/library/blocks")).json()
    assert page["total"] == 1 and page["items"][0]["category"] == "style"


async def test_blocks_survive_index_rebuild(client):
    ac, vault = client
    await ac.post("/api/vault/library/blocks", json=_block())
    (vault / ".index.sqlite").unlink()
    assert (await ac.get("/api/vault/library/blocks")).json()["total"] == 1
