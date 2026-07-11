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


async def test_edit_bumps_version_and_never_mutates_past_snapshots(client):
    """The freeze invariant (ROADMAP Phase 1): a snapshot is a frozen recipe — resolved text +
    params + block refs at generation time. Editing the Library block bumps its server-owned
    version; works saved earlier keep the old text and version ref, byte for byte."""
    ac, _ = client
    # A v1 block, used (as a frozen copy) in a saved work's snapshot.
    assert (await ac.post("/api/vault/library/blocks", json=_block())).status_code == 200
    work = {
        "id": "w1", "title": "T",
        "snapshots": [{"id": "s1", "hash": "h", "params": {"seed": 1},
                       "assembled_positive": "1girl, silver hair",
                       "components": [{"source": "library", "block_id": "b1", "version": 1,
                                       "name": "Silver-haired girl", "text": "1girl, silver hair"}]}],
    }
    assert (await ac.put("/api/vault/works", json=work)).status_code == 200

    # Content edit → version 2 (server-owned, whatever the client sent).
    edited = _block(text="1girl, silver hair, red eyes")
    assert (await ac.post("/api/vault/library/blocks", json=edited)).status_code == 200
    assert (await ac.get("/api/vault/library/blocks")).json()["items"][0]["version"] == 2
    # An identical re-save must NOT bump.
    assert (await ac.post("/api/vault/library/blocks", json=edited)).status_code == 200
    assert (await ac.get("/api/vault/library/blocks")).json()["items"][0]["version"] == 2

    # The earlier work still holds the v1 recipe — the edit never leaked into it.
    snap = (await ac.get("/api/vault/works/w1")).json()["snapshots"][0]
    assert snap["components"][0]["text"] == "1girl, silver hair"
    assert snap["components"][0]["version"] == 1
    assert snap["assembled_positive"] == "1girl, silver hair"


async def test_resolve_blocks_reports_current_version(client):
    """The widget diffs a pinned copy's frozen version against the live block — resolve returns the
    current rows for specific ids (bumped on edit), and omits ids that no longer exist."""
    ac, _ = client
    await ac.post("/api/vault/library/blocks", json=_block("b1", text="1girl"))
    await ac.post("/api/vault/library/blocks", json=_block("b1", text="1girl, red eyes"))  # edit → v2

    got = (await ac.get("/api/vault/library/blocks/resolve", params={"ids": ["b1", "gone"]})).json()
    assert len(got) == 1
    assert got[0]["id"] == "b1" and got[0]["version"] == 2 and got[0]["text"] == "1girl, red eyes"

    assert (await ac.get("/api/vault/library/blocks/resolve")).json() == []  # no ids → empty


async def test_multi_category_filter_and_sort(client):
    ac, _ = client
    await ac.post("/api/vault/library/blocks", json=_block("b1", "character", name="Zed"))
    await ac.post("/api/vault/library/blocks", json=_block("b2", "style", name="Aria", text="cinematic"))
    await ac.post("/api/vault/library/blocks", json=_block("b3", "pose", name="Mid", text="standing"))

    two = (await ac.get("/api/vault/library/blocks", params={"category": ["character", "pose"]})).json()
    assert {i["id"] for i in two["items"]} == {"b1", "b3"}  # repeated category params = multi-select

    by_cat = (await ac.get("/api/vault/library/blocks", params={"sort": "category"})).json()
    assert [i["category"] for i in by_cat["items"]] == ["character", "pose", "style"]  # alphabetical

    assert (await ac.get("/api/vault/library/blocks", params={"sort": "nope"})).status_code == 422


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


async def test_default_categories_and_restore(client):
    ac, _ = client
    # The defaults endpoint lists the whole built-in set (incl. the prefixed groups).
    slugs = {c["slug"] for c in (await ac.get("/api/vault/library/categories/defaults")).json()}
    assert {"body-skin", "outfit-fabric", "scene-lighting", "nsfw-act"} <= slugs
    # Delete a built-in → it drops out of the live list…
    await ac.delete("/api/vault/library/categories/pose")
    present = {c["slug"] for c in (await ac.get("/api/vault/library/categories")).json()}
    assert "pose" not in present
    # …restoring it brings it back (and ignores non-built-in / already-present slugs).
    res = (await ac.post("/api/vault/library/categories/restore", json={"slugs": ["pose", "style", "made-up"]})).json()
    assert res["restored"] == ["pose"]
    present2 = {c["slug"] for c in (await ac.get("/api/vault/library/categories")).json()}
    assert "pose" in present2
