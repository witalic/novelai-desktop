"""The vault index lives in this machine's cache, not the vault: where it goes, how a new run brings it in line with the
files, what happens to the old in-vault one, and recovery from a corrupt file — fully offline (tmp dirs)."""
import base64
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

import httpx
import pytest

from app.main import app
from app.novelai._png import solid_png
from app.settings import Settings, VaultSettings, get_settings
from app.vault import config as vaultcfg
from app.vault import index, migrate


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


def _db(vault: Path) -> Path:
    return index.location(vault.parent / "state", vault)  # the fixture's state dir doubles as the cache dir


def _new_run() -> None:
    """What the next start of the app sees: every index is synced with its files again on its first open."""
    index._synced.clear()


def _touch(path: Path) -> None:
    """Move a file's or folder's modification time on, as a later write would — explicit, so no clock tick decides."""
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def _work(work_id: str = "w1", title: str = "A work") -> dict:
    return {
        "id": work_id, "title": title,
        "snapshots": [{"id": f"s-{work_id}", "hash": "h", "params": {"seed": 1},
                       "components": [{"source": "custom", "name": "c", "text": "1girl", "tags": ["night"]}]}],
        "images": [{"id": f"img-{work_id}", "snapshot_id": f"s-{work_id}",
                    "image_b64": base64.b64encode(solid_png(16, 16)).decode("ascii")}],
        "preview_image_id": f"img-{work_id}",
    }


def _block(bid: str = "b1", text: str = "1girl") -> dict:
    return {"id": bid, "category": "character", "name": bid.upper(), "text": text, "tags": ["girl"]}


def _work_dir(vault: Path, work_id: str) -> Path:
    return next(p for p in (vault / "works").iterdir() if p.name.endswith(f"__{work_id}"))


async def _titles(ac: httpx.AsyncClient) -> set[str]:
    return {w["title"] for w in (await ac.get("/api/vault/works")).json()["items"]}


async def test_the_index_lives_in_the_machine_cache_not_the_vault(client):
    ac, vault = client
    assert (await ac.put("/api/vault/works", json=_work())).status_code == 200
    assert (await ac.get("/api/vault/works")).json()["total"] == 1
    assert _db(vault).is_file()
    assert not list(vault.glob(".index.sqlite*"))  # nothing of SQLite's on the (possibly network) vault


def test_each_vault_has_its_own_index_file(tmp_path):
    cache = tmp_path / "cache"
    a, b = index.location(cache, Path("D:/Art/NovelAI")), index.location(cache, Path("D:/Art/Other"))
    assert a != b and a.parent == b.parent == cache / "index"
    assert a.name.startswith("novelai-") and a.suffix == ".sqlite"  # readable for whoever opens the cache dir
    if sys.platform == "win32":  # one folder, however its path is spelled
        assert index.location(cache, Path("d:/art/novelai")) == a


async def test_a_new_run_picks_up_what_changed_outside_the_app(client):
    ac, vault = client
    for wid in ("w1", "w2", "w4"):
        await ac.put("/api/vault/works", json=_work(wid, title=wid))
    for bid in ("b1", "b3"):
        await ac.post("/api/vault/library/blocks", json=_block(bid))
    _new_run()
    assert await _titles(ac) == {"w1", "w2", "w4"}  # (the app's own saves are read back once here)

    # Meanwhile, without the app: w1 saved by another build, w2 deleted, w3 dropped in, a block edited, one added.
    w1 = _work_dir(vault, "w1")
    raw = json.loads((w1 / "work.json").read_text("utf-8"))
    raw["title"] = "w1 renamed"
    (w1 / "work.json.tmp").write_text(json.dumps(raw), "utf-8")
    os.replace(w1 / "work.json.tmp", w1 / "work.json")
    _touch(w1)
    shutil.rmtree(_work_dir(vault, "w2"))
    w3 = vault / "works" / "2026-01-01__by-hand__w3"
    w3.mkdir()
    (w3 / "work.json").write_text(json.dumps({"schema_version": migrate.CURRENT, "id": "w3", "title": "w3"}), "utf-8")
    b1 = vault / "library" / "blocks" / "character" / "b1.json"
    b1.write_text(json.dumps({**json.loads(b1.read_text("utf-8")), "text": "2girls"}), "utf-8")  # edited in place
    _touch(b1)
    (b1.parent / "b2.json").write_text(json.dumps(_block("b2")), "utf-8")
    (b1.parent / "b3.json").unlink()
    w4 = _work_dir(vault, "w4")
    (w4 / "work.json").unlink()  # the folder stays, with its images: no manifest, no work
    _touch(w4)

    assert await _titles(ac) == {"w1", "w2", "w4"}  # this run trusts its index…
    _new_run()
    assert await _titles(ac) == {"w1 renamed", "w3"}  # …the next one brings it in line
    blocks = {b["id"]: b["text"] for b in (await ac.get("/api/vault/library/blocks")).json()["items"]}
    assert blocks == {"b1": "2girls", "b2": "1girl"}


async def test_files_that_did_not_change_are_not_read_again(client, monkeypatch):
    ac, vault = client
    await ac.put("/api/vault/works", json=_work())
    await ac.post("/api/vault/library/blocks", json=_block())
    _new_run()
    await ac.get("/api/vault/works")  # reads the app's own saves back once, with their times
    read: list[str] = []
    work, block = index.upsert_work, index.upsert_block
    monkeypatch.setattr(index, "upsert_work", lambda conn, doc, *a: (read.append(doc.id), work(conn, doc, *a)))
    monkeypatch.setattr(index, "upsert_block", lambda conn, data, *a: (read.append(data["id"]), block(conn, data, *a)))
    _new_run()
    assert (await ac.get("/api/vault/works")).json()["total"] == 1
    assert read == []  # one listing per folder, no work or block read
    _touch(_work_dir(vault, "w1"))
    _new_run()
    await ac.get("/api/vault/works")
    assert read == ["w1"]  # …and only what changed is


async def test_a_work_folder_renamed_by_hand_keeps_its_work(client):
    ac, vault = client
    await ac.put("/api/vault/works", json=_work())
    _new_run()
    await ac.get("/api/vault/works")
    _work_dir(vault, "w1").rename(vault / "works" / "renamed__w1")
    _new_run()
    page = (await ac.get("/api/vault/works")).json()
    assert page["total"] == 1
    assert (await ac.get("/api/vault/works/w1/images/img-w1")).status_code == 200  # found in its new folder


async def test_the_old_index_in_the_vault_is_removed(client):
    ac, vault = client
    await ac.get("/api/vault/works")
    for name in (".index.sqlite", ".index.sqlite-wal"):  # what earlier builds left in every vault
        (vault / name).write_bytes(b"old")
    index.discard(_db(vault))
    assert (await ac.get("/api/vault/works")).status_code == 200
    assert not list(vault.glob(".index.sqlite*"))


async def test_an_old_index_another_app_holds_is_left_for_later(client, monkeypatch):
    ac, vault = client
    await ac.get("/api/vault/works")
    (vault / ".index.sqlite").write_bytes(b"old")
    index.discard(_db(vault))
    real = os.unlink

    def unlink(path, *a, **kw):
        if os.path.basename(path) == ".index.sqlite":  # an older build on another computer still has it open
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "unlink", unlink)
    assert (await ac.get("/api/vault/works")).status_code == 200
    assert (vault / ".index.sqlite").is_file()


async def test_a_corrupt_index_costs_one_request_then_is_rebuilt(client):
    ac, vault = client
    await ac.put("/api/vault/works", json=_work())
    db = _db(vault)
    page_size = int.from_bytes(db.read_bytes()[16:18], "big")
    with db.open("r+b") as f:  # the header (and its schema version) stays sound: only the tables are damaged
        f.seek(page_size)
        f.write(b"\xff" * (db.stat().st_size - page_size))
    with pytest.raises(sqlite3.DatabaseError):
        await ac.get("/api/vault/works")
    assert (await ac.get("/api/vault/works")).json()["total"] == 1  # thrown away and rebuilt from the works


async def test_a_save_checks_the_stored_manifest_not_the_index(client):
    ac, vault = client
    base = (await ac.put("/api/vault/works", json=_work())).json()["updated_at"]
    wj = _work_dir(vault, "w1") / "work.json"
    raw = json.loads(wj.read_text("utf-8"))
    raw["updated_at"] = "2999-01-01T00:00:00.000+00:00"  # saved since, by something this run's index didn't see
    wj.write_text(json.dumps(raw), "utf-8")
    res = await ac.put("/api/vault/works", json={**_work(), "updated_at": base})
    assert res.status_code == 409


async def test_a_manifest_gone_outside_the_app_is_a_deleted_work(client):
    ac, vault = client
    base = (await ac.put("/api/vault/works", json=_work())).json()["updated_at"]
    (_work_dir(vault, "w1") / "work.json").unlink()
    res = await ac.put("/api/vault/works", json={**_work(), "updated_at": base})  # the copy still open in the app
    assert res.status_code == 404 and res.json()["detail"] == "This work was deleted."
    assert (await ac.get("/api/vault/works")).json()["total"] == 0  # the index learnt it on the spot
    assert (await ac.put("/api/vault/works", json=_work())).status_code == 200  # a fresh one (no base) still saves


async def test_a_cache_file_that_is_no_database_is_simply_rebuilt(client):
    ac, vault = client
    await ac.put("/api/vault/works", json=_work())
    _db(vault).write_bytes(b"not a database at all" * 64)  # a disk fault, a half-copied file
    assert (await ac.get("/api/vault/works")).json()["total"] == 1  # no request fails for it


def _share_dropped(monkeypatch, vault: Path) -> None:
    """The NAS drops away: its folders and files read as missing (Windows reports a lost share that way), and the vault
    marker can't be seen either."""
    real_scandir, real_read = os.scandir, Path.read_text

    def scandir(path="."):
        if str(path).startswith(str(vault)):
            raise FileNotFoundError(2, "The network path was not found")
        return real_scandir(path)

    def read_text(self, *a, **kw):
        if str(self).startswith(str(vault)):
            raise FileNotFoundError(2, "The network path was not found")
        return real_read(self, *a, **kw)

    monkeypatch.setattr(os, "scandir", scandir)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(vaultcfg, "is_initialized", lambda _v: False)


async def test_a_share_that_drops_mid_sync_never_empties_the_index(client, monkeypatch):
    ac, vault = client
    for wid in ("w1", "w2"):
        await ac.put("/api/vault/works", json=_work(wid, title=wid))
    await ac.post("/api/vault/library/blocks", json=_block())
    _new_run()
    _share_dropped(monkeypatch, vault)
    assert (await ac.get("/api/vault/works")).status_code == 503  # waited for, never taken for "all deleted"
    monkeypatch.undo()
    assert await _titles(ac) == {"w1", "w2"}
    assert (await ac.get("/api/vault/library/blocks")).json()["total"] == 1


async def test_a_save_while_the_share_is_away_waits_rather_than_deleting(client, monkeypatch):
    ac, vault = client
    base = (await ac.put("/api/vault/works", json=_work())).json()["updated_at"]
    _share_dropped(monkeypatch, vault)
    res = await ac.put("/api/vault/works", json={**_work(), "updated_at": base})
    assert res.status_code == 503  # not a 404, which the app would answer by saving a duplicate
    monkeypatch.undo()
    assert (await ac.put("/api/vault/works", json={**_work(), "updated_at": base})).status_code == 200


async def test_a_work_copied_to_a_second_folder_stays_put(client):
    ac, vault = client
    await ac.put("/api/vault/works", json=_work())
    original = _work_dir(vault, "w1")
    shutil.copytree(original, original.with_name(original.name + " - Copy"))  # Explorer's copy of the folder
    for _ in range(3):
        _new_run()
        assert (await ac.get("/api/vault/works")).json()["total"] == 1
        with index.connection(vault, _db(vault)) as conn:
            assert index.find_work_dir(conn, "w1") == original.name  # every run: edits keep landing in one place


async def test_a_block_in_two_folders_keeps_one_text(client):
    ac, vault = client
    await ac.post("/api/vault/library/blocks", json=_block(text="first"))
    copy = vault / "library" / "blocks" / "style" / "b1.json"
    copy.parent.mkdir()
    copy.write_text(json.dumps({**_block(text="second"), "category": "style"}), "utf-8")
    texts = set()
    for _ in range(3):
        _new_run()
        texts |= {b["text"] for b in (await ac.get("/api/vault/library/blocks")).json()["items"]}
    assert texts == {"first"}  # the first file by path, every run — never swapping


async def test_a_hand_broken_manifest_is_saved_over(client):
    ac, vault = client
    base = (await ac.put("/api/vault/works", json=_work())).json()["updated_at"]
    (_work_dir(vault, "w1") / "work.json").write_text("{ broken", "utf-8")
    assert (await ac.put("/api/vault/works", json={**_work(), "updated_at": base})).status_code == 200
