"""Vault store + index: save/load/list/serve/gallery/rebuild — fully offline (tmp vault)."""
import asyncio
import base64
import json
import os
from io import BytesIO

import httpx
import pytest
from PIL import Image

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
            "params": {"seed": 42, "model": "nai-diffusion-4-5-full"},  # recipe lives on the snapshot
            "components": [{"source": "library", "block_id": "b1", "name": "Char", "text": "1girl", "tags": tags}],
        }],
        "images": [{
            "id": iid, "snapshot_id": sid, "favorite": True, "tags": ["favorite"], "image_b64": _png_b64(),
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
    assert body["images"][0]["created_at"]  # stamped by the backend when missing
    assert body["snapshots"][0]["params"]["seed"] == 42  # recipe kept on the snapshot


async def test_stack_persists(client):
    ac, vault = client
    work = _work("w1")
    work["stack"] = [{"id": "img-draft-1", "snapshot_id": "s-w1", "image_b64": _png_b64()}]
    assert (await ac.put("/api/vault/works", json=work)).status_code == 200
    wdir = list((vault / "works").glob("*/work.json"))[0].parent
    assert (wdir / "images" / "img-draft-1.png").is_file()  # stack image written to disk

    body = (await ac.get("/api/vault/works/w1")).json()
    assert len(body["stack"]) == 1
    assert body["stack"][0]["file"] == "images/img-draft-1.png"
    assert body["stack"][0]["created_at"] and body["stack"][0].get("image_b64") is None
    assert (await ac.get("/api/vault/works/w1/images/img-draft-1")).status_code == 200  # served like any image


async def test_image_tags_inherited_from_snapshot(client):
    ac, _ = client
    # A block tag on the snapshot must land in image_tag (source 'block') and bubble up to work_tag —
    # exercised through the gallery tag filter.
    await ac.put("/api/vault/works", json=_work("w1", tags=["silver hair"]))
    hit = (await ac.get("/api/vault/gallery", params={"tags": ["silver hair"]})).json()
    assert hit["total"] == 1 and hit["items"][0]["work_id"] == "w1"


async def test_block_examples_require_all_tags(client):
    ac, _ = client
    await ac.put("/api/vault/works", json=_work("w1", tags=["yvonne", "arknights"]))
    await ac.put("/api/vault/works", json=_work("w2", tags=["yvonne"]))  # missing 'arknights'
    ex = (await ac.get("/api/vault/library/examples", params={"tags": ["yvonne", "arknights"]})).json()
    assert len(ex) == 1 and ex[0]["work_id"] == "w1"  # exact match: only the image carrying BOTH tags


async def test_scratch_images_persist_but_never_surface(client):
    """Zones define role, not survival: scratch images live on disk with their recipe, are served
    for the canvas, but stay invisible to the gallery, work counts, and examples."""
    ac, vault = client
    work = _work("w1")
    work["images"].append({
        "id": "img-scr", "snapshot_id": "s-w1", "role": "scratch", "image_b64": _png_b64(),
    })
    assert (await ac.put("/api/vault/works", json=work)).status_code == 200
    wdir = list((vault / "works").glob("*/work.json"))[0].parent
    assert (wdir / "images" / "img-scr.png").is_file()  # scratch bytes ARE on disk

    body = (await ac.get("/api/vault/works/w1")).json()
    assert {im["id"]: im["role"] for im in body["images"]} == {"img-w1": "gallery", "img-scr": "scratch"}
    assert (await ac.get("/api/vault/works/w1/images/img-scr")).status_code == 200  # served for the canvas

    works = (await ac.get("/api/vault/works")).json()
    assert works["items"][0]["image_count"] == 1  # gallery images only

    gal = (await ac.get("/api/vault/gallery")).json()
    assert gal["total"] == 1 and gal["items"][0]["image_id"] == "img-w1"

    # Examples exclude scratch too — img-scr shares the same snapshot (and thus its block tags).
    ex = (await ac.get("/api/vault/library/examples", params={"tags": ["silver hair", "night"]})).json()
    assert [e["image_id"] for e in ex] == ["img-w1"]


async def test_save_stamps_schema_version(client):
    """The server owns schema_version: a client sending a stale value must not trigger a re-migration."""
    ac, vault = client
    w = _work("w1")
    w["schema_version"] = 3  # wrong on purpose — the client never dictates the schema
    assert (await ac.put("/api/vault/works", json=w)).status_code == 200
    raw = json.loads(list((vault / "works").glob("*/work.json"))[0].read_text(encoding="utf-8"))
    assert raw["schema_version"] == 7  # stamped to CURRENT on disk, not left at 3


async def test_combos_persist_and_get_stamped(client):
    """Saved block combinations travel with the work; the server stamps their times once, like snapshots."""
    ac, _ = client
    w = _work("w1")
    w["combos"] = [{"id": "combo-1", "name": "Raiden — vampire · tease", "blocks": [
        {"source": "library", "block_id": "b1", "version": 1, "name": "Char", "text": "1girl", "polarity": "positive",
         "category": "character", "tags": [], "frozen": False},
        {"source": "custom", "name": "Sig", "text": "signature", "polarity": "negative", "frozen": True},
    ]}]
    assert (await ac.put("/api/vault/works", json=w)).status_code == 200
    body = (await ac.get("/api/vault/works/w1")).json()
    combo = body["combos"][0]
    assert combo["name"] == "Raiden — vampire · tease" and combo["origin"] == "user"
    assert [b["text"] for b in combo["blocks"]] == ["1girl", "signature"]  # order kept
    assert combo["blocks"][1]["frozen"] is True and combo["blocks"][1]["polarity"] == "negative"
    assert combo["created_at"] and combo["updated_at"] == combo["created_at"]

    # A re-save keeps the original creation time.
    body["combos"][0]["name"] = "renamed"
    assert (await ac.put("/api/vault/works", json=body)).status_code == 200
    again = (await ac.get("/api/vault/works/w1")).json()["combos"][0]
    assert again["name"] == "renamed" and again["created_at"] == combo["created_at"]


async def test_save_rejects_future_schema_version(client):
    """A doc claiming a newer schema than this build is refused, not silently downgraded (data-loss)."""
    ac, _ = client
    w = _work("w1")
    w["schema_version"] = 999
    assert (await ac.put("/api/vault/works", json=w)).status_code == 400


async def test_save_optimistic_lock_rejects_stale_base(client):
    """H3: saving with an older base than the stored copy is a 409; a re-based save still works."""
    ac, _ = client
    assert (await ac.put("/api/vault/works", json=_work("w1"))).status_code == 200
    base = (await ac.get("/api/vault/works/w1")).json()["updated_at"]
    await asyncio.sleep(0.01)
    w2 = _work("w1"); w2["updated_at"] = base  # another editor saves from the same base → bumps updated_at
    assert (await ac.put("/api/vault/works", json=w2)).status_code == 200
    w3 = _work("w1"); w3["updated_at"] = base  # our save still carries the stale base → conflict
    assert (await ac.put("/api/vault/works", json=w3)).status_code == 409
    latest = (await ac.get("/api/vault/works/w1")).json()["updated_at"]
    w4 = _work("w1"); w4["updated_at"] = latest  # re-based on the latest → succeeds
    assert (await ac.put("/api/vault/works", json=w4)).status_code == 200


async def test_saves_in_the_same_millisecond_still_move_the_lock_base(client, monkeypatch):
    """Two editors saving within one clock tick must not share a timestamp — the second one's base would equal
    the stored copy and its stale save would slip past the check."""
    from app.vault import service
    ac, _ = client
    monkeypatch.setattr(service, "_now", lambda: "2026-10-25T07:00:00.000+00:00")  # a frozen clock
    assert (await ac.put("/api/vault/works", json=_work("w1"))).status_code == 200
    base = (await ac.get("/api/vault/works/w1")).json()["updated_at"]
    w2 = _work("w1"); w2["updated_at"] = base
    assert (await ac.put("/api/vault/works", json=w2)).status_code == 200
    assert (await ac.get("/api/vault/works/w1")).json()["updated_at"] == "2026-10-25T07:00:00.001+00:00"
    w3 = _work("w1"); w3["updated_at"] = base  # the same base again: now it is stale
    assert (await ac.put("/api/vault/works", json=w3)).status_code == 409


def test_a_new_stamp_passes_old_and_odd_stored_values(monkeypatch):
    from app.vault import service
    monkeypatch.setattr(service, "_now", lambda: "2026-10-25T07:00:00.000+00:00")
    assert service._stamp_after(None) == "2026-10-25T07:00:00.000+00:00"
    # seconds precision (works saved by older builds), a clock that stepped back
    assert service._stamp_after("2026-10-25T07:00:05+00:00") == "2026-10-25T07:00:05.001+00:00"
    assert service._stamp_after("not a date") == "2026-10-25T07:00:00.000+00:00"  # hand-edited: replaced


async def test_list_works_search_and_sort(client):
    ac, _ = client
    for wid, title, n in [("w1", "Alpha", 1), ("w2", "Beta", 3), ("w3", "alpha two", 2)]:
        w = _work(wid)
        w["title"] = title
        base = w["images"][0]
        w["images"] = [dict(base, id=f"img-{wid}-{i}") for i in range(n)]  # n gallery images
        w["preview_image_id"] = f"img-{wid}-0"
        assert (await ac.put("/api/vault/works", json=w)).status_code == 200

    # search by title (case-insensitive substring): "Alpha" + "alpha two"
    hit = (await ac.get("/api/vault/works", params={"search": "alpha"})).json()
    assert hit["total"] == 2 and {i["id"] for i in hit["items"]} == {"w1", "w3"}

    # sort by name ascending (COLLATE NOCASE)
    names = (await ac.get("/api/vault/works", params={"sort": "name", "direction": "asc"})).json()
    assert [i["title"] for i in names["items"]] == ["Alpha", "alpha two", "Beta"]

    # sort by gallery image count, descending
    counts = (await ac.get("/api/vault/works", params={"sort": "image_count", "direction": "desc"})).json()
    assert [i["image_count"] for i in counts["items"]] == [3, 2, 1]

    # an out-of-whitelist sort is rejected by the router
    assert (await ac.get("/api/vault/works", params={"sort": "bogus"})).status_code == 422


async def test_image_thumbnail_resized_and_capped(client):
    ac, vault = client
    work = _work("w1")
    work["images"][0]["image_b64"] = base64.b64encode(solid_png(800, 1200)).decode("ascii")
    await ac.put("/api/vault/works", json=work)

    thumb = await ac.get("/api/vault/works/w1/images/img-w1", params={"w": 400})
    assert thumb.status_code == 200
    from io import BytesIO

    from PIL import Image
    assert Image.open(BytesIO(thumb.content)).size == (400, 600)  # width-capped, aspect kept
    assert list((vault / "works").glob("*/.thumbs/img-w1@400.png"))  # cached on disk (glob is lazy → list it)
    # Requesting wider than the source never upscales — the original is served.
    big = await ac.get("/api/vault/works/w1/images/img-w1", params={"w": 4096})
    assert Image.open(BytesIO(big.content)).size == (800, 1200)


async def test_thumbnail_cache_reuse_then_invalidate(client):
    ac, vault = client
    work = _work("w1")
    work["images"][0]["image_b64"] = base64.b64encode(solid_png(800, 1200)).decode("ascii")
    await ac.put("/api/vault/works", json=work)

    r1 = await ac.get("/api/vault/works/w1/images/img-w1", params={"w": 400})
    cache = next((vault / "works").glob("*/.thumbs/img-w1@400.png"))
    mtime1 = cache.stat().st_mtime_ns
    r2 = await ac.get("/api/vault/works/w1/images/img-w1", params={"w": 400})
    assert r2.content == r1.content and cache.stat().st_mtime_ns == mtime1  # served from cache, not rebuilt

    # A newer source (re-generated pixels) must invalidate the cached thumbnail (mtime-based).
    src = next((vault / "works").glob("*/images/img-w1.png"))
    os.utime(src, ns=(cache.stat().st_mtime_ns + 10**9, cache.stat().st_mtime_ns + 10**9))
    await ac.get("/api/vault/works/w1/images/img-w1", params={"w": 400})
    assert cache.stat().st_mtime_ns > mtime1  # regenerated because the source is now newer


async def test_thumbnail_handles_non_rgb_source(client):
    ac, _ = client
    buf = BytesIO()
    Image.new("L", (600, 900), 128).save(buf, "PNG")  # grayscale → exercises the convert("RGBA") branch
    work = _work("w1")
    work["images"][0]["image_b64"] = base64.b64encode(buf.getvalue()).decode("ascii")
    await ac.put("/api/vault/works", json=work)
    resp = await ac.get("/api/vault/works/w1/images/img-w1", params={"w": 300})
    assert resp.status_code == 200 and Image.open(BytesIO(resp.content)).size == (300, 450)


async def test_untitled_works_with_shared_id_prefix_dont_collide(client):
    ac, vault = client
    # Two ids that share their first 8 chars (as real "work-<uuid>" ids can) + same empty title/day: a dir
    # name built from an id *prefix* would collapse both into one folder and overwrite the first work.json.
    a, b = _work("work-aaaa-1"), _work("work-aaaa-2")
    a["title"] = b["title"] = ""
    await ac.put("/api/vault/works", json=a)
    await ac.put("/api/vault/works", json=b)
    assert len(list((vault / "works").glob("*/work.json"))) == 2  # distinct dirs, neither overwrote the other
    assert (await ac.get("/api/vault/works/work-aaaa-1")).json()["id"] == "work-aaaa-1"
    assert (await ac.get("/api/vault/works/work-aaaa-2")).json()["id"] == "work-aaaa-2"


async def test_rebuild_skips_a_corrupt_work(client):
    ac, vault = client
    await ac.put("/api/vault/works", json=_work("w1"))
    bad = vault / "works" / "0000__bad__work-bad"
    bad.mkdir(parents=True)
    (bad / "work.json").write_text("{ not valid json", "utf-8")  # a garbage sibling work
    (vault / ".index.sqlite").unlink()  # force a full rebuild from disk
    page = (await ac.get("/api/vault/works")).json()
    assert page["total"] == 1 and page["items"][0]["title"]  # valid work indexed, bad one skipped, no 500


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


async def test_concurrent_saves_share_a_tag_without_racing_the_index(client):
    ac, _ = client
    # 8 saves land in parallel (sync handlers run in Starlette's threadpool): the first calls race to build
    # the index, and all introduce the SAME new tag. Without the per-vault build lock (+ INSERT OR IGNORE)
    # this races to "table exists" / a UNIQUE IntegrityError → 500s. All must succeed and share one tag row.
    works = [_work(f"w{i}", tags=["shared", f"uniq{i}"]) for i in range(8)]
    results = await asyncio.gather(*[ac.put("/api/vault/works", json=w) for w in works])
    assert [r.status_code for r in results] == [200] * 8
    hit = (await ac.get("/api/vault/gallery", params={"tags": ["shared"]})).json()
    assert hit["total"] == 8  # every work indexed under the one shared tag, no dupes/errors


async def test_delete_work_removes_dir_and_index(client):
    ac, vault = client
    await ac.put("/api/vault/works", json=_work("w1"))
    assert (await ac.delete("/api/vault/works/w1")).status_code == 200
    assert not list((vault / "works").glob("*/work.json"))          # folder gone
    assert (await ac.get("/api/vault/works/w1")).status_code == 404  # dropped from the index too
    assert (await ac.get("/api/vault/works")).json()["total"] == 0


def _held_open(monkeypatch, *names: str) -> None:
    """These files are open in another program (Explorer's Thumbs.db on a share): Windows refuses to delete them."""
    real = os.unlink

    def unlink(path, *a, **kw):
        if os.path.basename(path) in names:
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "unlink", unlink)


async def test_a_file_another_program_holds_never_half_deletes_a_work(client, monkeypatch, caplog):
    ac, vault = client
    await ac.put("/api/vault/works", json=_work("w1"))
    wdir = next((vault / "works").glob("*/work.json")).parent
    (wdir / "Thumbs.db").write_bytes(b"explorer")
    _held_open(monkeypatch, "Thumbs.db")
    assert (await ac.delete("/api/vault/works/w1")).status_code == 200  # the work is gone…
    assert (await ac.get("/api/vault/works")).json()["total"] == 0
    assert sorted(p.name for p in wdir.iterdir()) == ["Thumbs.db"]  # …only the held file stays, and it is logged
    assert "Thumbs.db" in caplog.text
    (vault / ".index.sqlite").unlink()  # a rebuild doesn't bring it back: no manifest, no work
    assert (await ac.get("/api/vault/works")).json()["total"] == 0


async def test_a_held_manifest_refuses_the_delete_with_nothing_lost(client, monkeypatch):
    ac, vault = client
    await ac.put("/api/vault/works", json=_work("w1"))
    _held_open(monkeypatch, "work.json")
    res = await ac.delete("/api/vault/works/w1")
    assert res.status_code == 409 and "in use by another program" in res.json()["detail"]
    assert (await ac.get("/api/vault/works/w1")).json()["images"][0]["id"] == "img-w1"  # whole and listed


async def test_a_resave_leaves_os_files_and_held_orphans_alone(client, monkeypatch):
    ac, vault = client
    work = _work("w1")
    work["images"].append({"id": "img-extra", "snapshot_id": "s-w1", "favorite": False, "tags": [],
                           "image_b64": _png_b64()})
    await ac.put("/api/vault/works", json=work)
    imgs = next((vault / "works").glob("*/images"))
    (imgs / "Thumbs.db").write_bytes(b"explorer")
    (imgs / "desktop.ini").write_text("[.ShellClassInfo]")
    _held_open(monkeypatch, "img-extra.png")  # open in a viewer
    work["images"] = [work["images"][0]]
    assert (await ac.put("/api/vault/works", json=work)).status_code == 200  # the save lands regardless
    assert sorted(p.name for p in imgs.iterdir()) == ["Thumbs.db", "desktop.ini", "img-extra.png", "img-w1.json", "img-w1.png"]


async def test_resave_gcs_removed_image_files(client):
    ac, vault = client
    work = _work("w1")
    work["images"].append({"id": "img-extra", "snapshot_id": "s-w1", "favorite": False, "tags": [],
                           "image_b64": base64.b64encode(solid_png(64, 64)).decode("ascii")})
    await ac.put("/api/vault/works", json=work)
    await ac.get("/api/vault/works/w1/images/img-extra", params={"w": 32})  # make a thumbnail for it too
    imgs = next((vault / "works").glob("*/images"))
    assert (imgs / "img-extra.png").is_file()
    # Re-save without the extra image → its png, sidecar, and thumbnail must be garbage-collected.
    work["images"] = [work["images"][0]]
    await ac.put("/api/vault/works", json=work)
    assert not (imgs / "img-extra.png").exists() and not (imgs / "img-extra.json").exists()
    assert not list((imgs.parent / ".thumbs").glob("img-extra@*"))


async def test_rejects_garbage_base64_and_traversal_image_id(client):
    ac, _ = client
    bad_data = _work("w1")
    bad_data["images"][0]["image_b64"] = "!!! not base64 !!!"
    assert (await ac.put("/api/vault/works", json=bad_data)).status_code == 400  # 400, not a 500

    traversal = _work("w2")
    traversal["images"][0]["id"] = "../escape"  # ids become filenames → must be rejected before any write
    assert (await ac.put("/api/vault/works", json=traversal)).status_code == 400


async def test_save_blocked_while_a_move_is_in_progress(client):
    ac, _ = client
    from app.vault import manager
    manager._move["active"] = True  # simulate an in-flight vault move
    try:
        assert (await ac.put("/api/vault/works", json=_work("w1"))).status_code == 409
    finally:
        manager._move["active"] = False


async def test_bad_id_and_missing_image(client):
    ac, _ = client
    await ac.put("/api/vault/works", json=_work())
    assert (await ac.get("/api/vault/works/a.b/images/x")).status_code == 400
    assert (await ac.get("/api/vault/works/w1/images/nope")).status_code == 404
