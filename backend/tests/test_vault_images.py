"""The gallery's actions on images of many works: favourite, delete, move or copy (into an album too), download —
each a logged backend edit an open copy catches up on. Fully offline (tmp vault, tmp downloads)."""
import asyncio
import base64
import shutil
from collections import OrderedDict

import httpx
import pytest

from app import downloads
from app.agent import changes as notices
from app.main import app
from app.novelai._png import solid_png
from app.settings import Settings, VaultSettings, get_settings
from app.vault import gallery_layout, service, store, worklog
from app.vault.models import Image, Snapshot, WorkDoc

PNG = base64.b64encode(solid_png(8, 12)).decode("ascii")


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(worklog, "_log", OrderedDict())
    # Downloads too: a test must never write into the real one.
    return Settings(vault=VaultSettings(dir=str(tmp_path / "vault"), state_dir=str(tmp_path / "state")),
                    download_dir=str(tmp_path / "downloads"), _env_file=None)


@pytest.fixture
async def api(settings):
    app.dependency_overrides[get_settings] = lambda: settings
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def seed(settings: Settings) -> None:
    service.save_work(settings, WorkDoc(
        id="work-a", title="Moon studies", snapshots=[Snapshot(id="s-a", assembled_positive="moon")],
        images=[Image(id=f"a-{i}", snapshot_id="s-a", image_b64=PNG, created_at=f"2026-10-2{i}T00:00:00Z")
                for i in range(1, 4)] + [Image(id="a-scr", role="scratch", image_b64=PNG)],
        canvas={"nodes": [{"id": "gallery", "type": "zone", "data": {"role": "gallery", "blocks": [
            {"id": "sec", "type": "section", "title": "Night", "collapsed": False},
            {"id": "g-a", "type": "grid", "imageIds": ["a-1", "a-2"], "cols": 3}]}}]}))
    service.save_work(settings, WorkDoc(id="work-b", title="Rainy city",
                                        images=[Image(id="b-1", image_b64=PNG), Image(id="b-2", image_b64=PNG)]))
    service.save_work(settings, WorkDoc(id="work-t", title="Picks", images=[Image(id="t-1", image_b64=PNG)],
                                        canvas={"nodes": [{"id": "gallery", "type": "zone", "data": {
                                            "role": "gallery", "blocks": [
                                                {"id": "g-t", "type": "grid", "imageIds": ["t-1"], "cols": 3},
                                                {"id": "g-empty", "type": "grid", "imageIds": [], "cols": 3}]}}]}))


async def changes(api: httpx.AsyncClient, work_id: str, since: str) -> list[dict]:
    res = await api.get(f"/api/vault/works/{work_id}/changes", params={"since": since})
    assert res.status_code == 200, res.text
    return res.json()["ops"]


async def test_a_favourite_is_set_and_logged_for_the_open_copy(api, settings):
    seed(settings)
    base = service.load_work(settings, "work-a").updated_at
    url = "/api/vault/works/work-a/images/a-2/favorite"
    assert (await api.put(url, json={"favorite": True})).json() == {"changed": True}
    assert (await api.put(url, json={"favorite": True})).json() == {"changed": False}  # nothing to save
    assert next(im for im in service.load_work(settings, "work-a").images if im.id == "a-2").favorite
    assert await changes(api, "work-a", base) == [{"op": "image", "id": "a-2", "set": {"favorite": True}}]
    res = await api.put("/api/vault/works/work-a/images/a-scr/favorite", json={"favorite": True})
    assert res.status_code == 404  # scratch is no gallery image


async def test_images_of_many_works_are_deleted_work_by_work(api, settings):
    seed(settings)
    base = service.load_work(settings, "work-a").updated_at
    res = await api.post("/api/vault/images/delete", json={"images": [
        {"work_id": "work-a", "image_id": "a-1"}, {"work_id": "work-b", "image_id": "b-2"},
        {"work_id": "work-a", "image_id": "a-scr"}, {"work_id": "work-gone", "image_id": "x"}]})
    assert res.json() == [
        {"work_id": "work-a", "deleted": ["a-1"], "error": None},  # never the scratch one
        {"work_id": "work-b", "deleted": ["b-2"], "error": None},
        {"work_id": "work-gone", "deleted": [], "error": "Work not found."}]
    a = service.load_work(settings, "work-a")
    assert [im.id for im in a.images] == ["a-2", "a-3", "a-scr"]
    assert gallery_layout.grids(gallery_layout.stack(a))[0]["imageIds"] == ["a-2"]  # out of its album too
    assert await changes(api, "work-a", base) == [{"op": "images_deleted", "ids": ["a-1"]}]


async def test_a_selection_is_bounded(api, settings):
    too_many = [{"work_id": "work-a", "image_id": f"a-{i}"} for i in range(201)]
    assert (await api.post("/api/vault/images/delete", json={"images": too_many})).status_code == 422
    assert (await api.post("/api/vault/images/delete", json={"images": []})).status_code == 422
    spread = [{"work_id": f"work-{i}", "image_id": "x"} for i in range(51)]
    res = await api.post("/api/vault/images/delete", json={"images": spread})
    assert res.status_code == 422 and "more than 50 works" in res.json()["detail"]


async def test_images_move_into_an_album_of_another_work(api, settings):
    seed(settings)
    t_base, a_base = (service.load_work(settings, w).updated_at for w in ("work-t", "work-a"))
    with notices.subscribe() as told:
        res = (await api.post("/api/vault/images/transfer", json={
            "images": [{"work_id": "work-a", "image_id": "a-1"}, {"work_id": "work-b", "image_id": "b-1"}],
            "to_work_id": "work-t", "grid_id": "g-empty"})).json()
        notice = await asyncio.wait_for(told.get(), 1)
    by_work = {r["work_id"]: r for r in res}
    new_a, new_b = by_work["work-a"]["ids"]["a-1"], by_work["work-b"]["ids"]["b-1"]
    assert not by_work["work-a"]["copied"] and by_work["work-a"]["not_removed"] is None
    t = service.load_work(settings, "work-t")
    assert [g["imageIds"] for g in gallery_layout.grids(gallery_layout.stack(t))] == [["t-1"], [new_a, new_b]]
    assert "a-1" not in {im.id for im in service.load_work(settings, "work-a").images}
    ops = await changes(api, "work-t", t_base)
    assert [o["op"] for o in ops] == ["images_added", "gallery_place", "images_added", "gallery_place"]
    assert ops[1] == {"op": "gallery_place", "grid": "g-empty", "image_ids": [new_a], "order": []}
    assert ops[0]["images"][0]["file"] == f"images/{new_a}.png"  # on disk when the open copy replays it
    assert await changes(api, "work-a", a_base) == [{"op": "images_deleted", "ids": ["a-1"]}]
    # The open copies of all three catch up now — the app's own doing, so no toast.
    assert (notice["kind"], notice["work_id"], notice["also"], notice["local"]) == (
        "work", "work-t", ["work-a", "work-b"], True)


async def test_the_targets_own_images_move_within_it(api, settings):
    seed(settings)
    base = service.load_work(settings, "work-a").updated_at
    body = {"images": [{"work_id": "work-a", "image_id": "a-3"}], "to_work_id": "work-a", "grid_id": "g-a"}
    assert (await api.post("/api/vault/images/transfer", json=body)).json() == [
        {"work_id": "work-a", "ids": {"a-3": "a-3"}, "copied": False, "not_removed": None, "error": None}]
    body = {"images": [{"work_id": "work-a", "image_id": "a-1"}], "to_work_id": "work-a"}  # into its Quick access
    await api.post("/api/vault/images/transfer", json=body)
    a = service.load_work(settings, "work-a")
    assert gallery_layout.grids(gallery_layout.stack(a))[0]["imageIds"] == ["a-2", "a-3"]
    assert await changes(api, "work-a", base) == [
        {"op": "gallery_place", "grid": "g-a", "image_ids": ["a-3"], "order": []},
        {"op": "gallery_unplace", "image_ids": ["a-1"]}]
    res = await api.post("/api/vault/images/transfer", json={**body, "copy": True})
    assert res.json()[0]["error"] == "They are in that work already."
    res = await api.post("/api/vault/images/transfer", json={**body, "to_work_id": "work-nope"})
    assert res.status_code == 404  # no target: nothing goes anywhere


async def test_one_work_failing_to_save_never_stops_the_rest(api, settings, monkeypatch):
    seed(settings)
    real_save, real_copy = service.save_work, store._copy_in

    def held(settings, doc, adopt=None):
        if doc.id == "work-b":
            raise PermissionError("held by another program")
        return real_save(settings, doc, adopt=adopt)

    monkeypatch.setattr(service, "save_work", held)
    res = (await api.post("/api/vault/images/delete", json={"images": [
        {"work_id": "work-b", "image_id": "b-1"}, {"work_id": "work-a", "image_id": "a-3"}]})).json()
    assert res == [{"work_id": "work-b", "deleted": [], "error": "Saving it failed: held by another program"},
                   {"work_id": "work-a", "deleted": ["a-3"], "error": None}]
    monkeypatch.setattr(service, "save_work", real_save)
    calls: list[int] = []

    def full_disk(images_dir, doc, adopt):
        if adopt:  # the target's saves — the second one meets a full disk
            calls.append(1)
            if len(calls) == 2:
                raise OSError("disk full")
        real_copy(images_dir, doc, adopt)

    monkeypatch.setattr(store, "_copy_in", full_disk)
    res = (await api.post("/api/vault/images/transfer", json={"images": [
        {"work_id": "work-a", "image_id": "a-1"}, {"work_id": "work-b", "image_id": "b-1"}],
        "to_work_id": "work-t"})).json()
    assert res[0]["error"] is None and res[1]["error"] == "Saving into the target failed — nothing changed: disk full"
    assert {im.id for im in service.load_work(settings, "work-b").images} == {"b-1", "b-2"}  # b-1 stayed put
    assert len(service.load_work(settings, "work-t").images) == 2  # t-1 and the moved a-1


async def test_a_copy_leaves_them_and_a_gone_album_moves_nothing(api, settings):
    seed(settings)
    res = (await api.post("/api/vault/images/transfer", json={
        "images": [{"work_id": "work-b", "image_id": "b-1"}], "to_work_id": "work-t", "copy": True})).json()
    assert res[0]["copied"] and res[0]["error"] is None
    assert {im.id for im in service.load_work(settings, "work-b").images} == {"b-1", "b-2"}
    t = service.load_work(settings, "work-t")
    assert len(t.images) == 2 and gallery_layout.placements(t).keys() == {"t-1"}  # into Quick access
    res = (await api.post("/api/vault/images/transfer", json={
        "images": [{"work_id": "work-b", "image_id": "b-2"}], "to_work_id": "work-t", "grid_id": "g-nope"})).json()
    assert res[0]["error"] == "That album is no longer in “Picks”." and res[0]["ids"] == {}
    assert {im.id for im in service.load_work(settings, "work-b").images} == {"b-1", "b-2"}


async def test_a_works_albums_in_order_with_what_they_show(api, settings):
    seed(settings)
    odd = service.load_work(settings, "work-b")
    gallery_layout.blocks(odd).extend([{"type": "grid", "imageIds": ["b-1"]},
                                       {"id": "g-b", "type": "grid", "imageIds": ["b-2"]}])
    service.save_work(settings, odd)
    albums = (await api.get("/api/vault/works/work-b/albums")).json()
    assert [(a["grid_id"], a["number"], a["count"]) for a in albums] == [("g-b", 2, 1)]  # one with no id takes nothing
    assert (await api.get("/api/vault/works/work-a/albums")).json() == [
        {"grid_id": "g-a", "number": 1, "heading": None, "section": "Night", "count": 2}]
    assert [(a["grid_id"], a["count"]) for a in (await api.get("/api/vault/works/work-t/albums")).json()] == [
        ("g-t", 1), ("g-empty", 0)]


async def test_downloads_never_overwrite_one_another(api, settings, tmp_path, monkeypatch):
    seed(settings)
    monkeypatch.setattr(downloads.time, "time", lambda: 1700000000)  # every save in the same second
    body = {"images": [{"work_id": "work-a", "image_id": "a-1"}, {"work_id": "work-b", "image_id": "b-1"},
                       {"work_id": "work-a", "image_id": "a-1"}]}  # a pick twice is saved once
    for _ in range(2):
        res = (await api.post("/api/vault/images/download", json=body)).json()
        assert res == {"count": 2, "dir": str(tmp_path / "downloads"), "failed": 0}
    await api.post("/api/download", json={"images": [PNG]})  # the canvas's downloads share the rule
    assert sorted(p.name for p in (tmp_path / "downloads").iterdir()) == [
        "novelai-1700000000-1-2.png", "novelai-1700000000-1-3.png", "novelai-1700000000-1.png",
        "novelai-1700000000-2-2.png", "novelai-1700000000-2.png"]
    for bad in ("nope", "a-scr"):  # not there, or no gallery image
        res = await api.post("/api/vault/images/download", json={"images": [{"work_id": "work-a", "image_id": bad}]})
        assert res.status_code == 404


async def test_a_download_that_fails_leaves_nothing_behind(api, settings, tmp_path, monkeypatch):
    seed(settings)
    real = shutil.copyfile

    def gone_meanwhile(src, dst):
        if "b-1" in str(src):
            raise FileNotFoundError(src)
        return real(src, dst)

    monkeypatch.setattr(downloads.shutil, "copyfile", gone_meanwhile)
    body = {"images": [{"work_id": "work-a", "image_id": "a-1"}, {"work_id": "work-b", "image_id": "b-1"}]}
    assert (await api.post("/api/vault/images/download", json=body)).json()["failed"] == 1
    saved = list((tmp_path / "downloads").iterdir())
    assert len(saved) == 1 and saved[0].stat().st_size > 0  # no empty file where b-1 would have gone
