"""Vault management: first-run default, add/switch/delete/move — offline (tmp dirs only, no real home/config)."""
import asyncio
import os
import shutil
import threading
import time
from pathlib import Path

import httpx
import pytest

from app import appconfig
from app.main import app
from app.settings import Settings, VaultSettings, get_settings
from app.vault import index, manager


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


async def test_add_rejects_non_empty_non_vault_folder(client):
    ac, tmp = client
    busy = tmp / "busy"
    busy.mkdir()
    (busy / "stuff.txt").write_text("x", "utf-8")  # a folder with unrelated content → don't scatter a vault in it
    assert (await ac.post("/api/vault/vaults", json={"dir": str(busy)})).status_code == 400
    assert (await ac.post("/api/vault/vaults", json={"dir": str(tmp / "fresh")})).status_code == 200  # empty is fine


async def test_an_unreachable_vault_is_waited_for_never_switched_away_from(client):
    ac, tmp = client
    await ac.get("/api/vault/config")
    nas = tmp / "nas-vault"
    await ac.post("/api/vault/vaults", json={"dir": str(nas)})  # the active vault, on a share
    await ac.put("/api/vault/works", json={"id": "w1", "title": "On the share"})
    parked = tmp / "nas-offline"
    nas.rename(parked)  # the share drops off the network
    appconfig._probes.clear()  # (each folder's last answer is remembered for a couple of seconds)
    res = await ac.get("/api/vault/works")
    assert res.status_code == 503 and res.json()["detail"] == f"The vault folder {nas} can't be reached."
    assert (await ac.put("/api/vault/works", json={"id": "w2", "title": "Meanwhile"})).status_code == 503  # no stray save
    body = (await ac.get("/api/vault/config")).json()  # Settings → Vaults still opens, and says which one is out
    assert body["active"] == str(nas)
    assert {v["dir"]: (v["active"], v["reachable"]) for v in body["vaults"]} == {
        str(tmp / "default-vault"): (False, True), str(nas): (True, False)}
    assert not list((tmp / "default-vault" / "works").glob("*/work.json"))  # nothing landed in another vault
    nas.mkdir()  # the share's empty mount point is there again, but not the share
    appconfig._probes.clear()
    assert (await ac.get("/api/vault/works")).status_code == 503  # waited for, never filled as a new vault
    assert not (nas / ".vault.json").exists()
    nas.rmdir()
    parked.rename(nas)  # back online: it just works again, on the same vault
    appconfig._probes.clear()
    assert [w["title"] for w in (await ac.get("/api/vault/works")).json()["items"]] == ["On the share"]


async def test_a_slow_share_is_given_up_on_and_asked_once(client, monkeypatch):
    calls = []
    release = threading.Event()

    def hung_is_dir(self):  # an SMB host that's down: the answer takes ages
        calls.append(self)
        release.wait(5)
        return False

    monkeypatch.setattr(appconfig, "_PROBE_TIMEOUT_S", 0.2)
    monkeypatch.setattr(Path, "is_dir", hung_is_dir)
    share = Path("//nas/never")
    started = time.monotonic()
    answers = [appconfig.reachable(share) for _ in range(3)]
    assert answers == [False] * 3 and time.monotonic() - started < 2  # given up on, not waited out
    assert len(calls) == 1  # one question in flight for everyone
    release.set()


async def test_a_dead_vault_can_be_dropped_from_the_list_without_touching_it(client):
    ac, tmp = client
    await ac.get("/api/vault/config")
    gone = tmp / "old-share"
    await ac.post("/api/vault/vaults", json={"dir": str(gone)})
    await ac.put("/api/vault/active", json={"dir": str(tmp / "default-vault")})
    body = (await ac.delete("/api/vault/vaults", params={"dir": str(gone), "keep_files": "true"})).json()
    assert gone.is_dir() and (gone / ".vault.json").is_file()  # only the list changed
    assert not any(v["dir"] == str(gone) for v in body["vaults"])


async def test_an_unreadable_settings_file_is_never_taken_for_a_first_run(client, monkeypatch):
    ac, tmp = client
    await ac.post("/api/vault/vaults", json={"dir": str(tmp / "mine")})
    real = Path.read_text

    def held(self, *a, **kw):  # another program has settings.json open for a moment too long
        if self.name == "settings.json":
            raise PermissionError(32, "being used by another process")
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", held)
    with pytest.raises(PermissionError):
        await ac.get("/api/vault/works")
    monkeypatch.undo()
    assert (await ac.get("/api/vault/config")).json()["active"] == str(tmp / "mine")  # nothing was written over


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


async def test_move_rejects_bad_requests(client):
    ac, tmp = client
    await ac.get("/api/vault/config")
    v2 = tmp / "second"
    await ac.post("/api/vault/vaults", json={"dir": str(v2)})  # v2 added + active, on disk
    # source not in the list → 404
    assert (await ac.post("/api/vault/move", json={"src": str(tmp / "ghost"), "dst": str(tmp / "x")})).status_code == 404
    # target == source → 400
    assert (await ac.post("/api/vault/move", json={"src": str(v2), "dst": str(v2)})).status_code == 400
    # target nested inside source → 400 (else the copy is written into a tree that rmtree then deletes)
    assert (await ac.post("/api/vault/move", json={"src": str(v2), "dst": str(v2 / "sub")})).status_code == 400
    # target folder is non-empty → 400
    busy = tmp / "busy"
    busy.mkdir()
    (busy / "f.txt").write_text("x", "utf-8")
    assert (await ac.post("/api/vault/move", json={"src": str(v2), "dst": str(busy)})).status_code == 400
    # a move already in progress → 409
    from app.vault import manager
    manager._move["active"] = True
    try:
        resp = await ac.post("/api/vault/move", json={"src": str(v2), "dst": str(tmp / "z")})
        assert resp.status_code == 409
    finally:
        manager._move["active"] = False


async def test_delete_active_vault_reassigns_active(client):
    ac, tmp = client
    await ac.get("/api/vault/config")  # creates + registers the default
    default = str(tmp / "default-vault")
    v2 = str(tmp / "second")
    await ac.post("/api/vault/vaults", json={"dir": v2})  # v2 becomes active
    body = (await ac.delete("/api/vault/vaults", params={"dir": v2})).json()  # delete the ACTIVE vault
    assert body["active"] == default  # active reassigned to the remaining vault
    assert not any(x["dir"] == v2 for x in body["vaults"])


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


async def test_a_moved_or_deleted_vault_leaves_no_index_behind(client):
    ac, tmp = client
    await ac.get("/api/vault/works")  # creates the default vault and builds its index
    state, src, dst = tmp / "state", tmp / "default-vault", tmp / "moved"
    assert index.location(state, src).is_file()
    assert (await _moved(ac, src, dst))["error"] is None
    assert not index.location(state, src).exists()  # the old path's index went with the move
    await ac.get("/api/vault/works")
    assert index.location(state, dst).is_file()  # the moved vault built its own
    await ac.delete("/api/vault/vaults", params={"dir": str(dst)})
    assert not index.location(state, dst).exists()


async def test_indexes_of_vaults_gone_from_the_list_are_swept_at_start(client):
    ac, tmp = client
    await ac.get("/api/vault/works")  # the default vault and its index
    state = tmp / "state"
    listed, orphan = index.location(state, tmp / "default-vault"), index.location(state, tmp / "renamed-elsewhere")
    orphan.write_bytes(b"prompts of a vault no longer listed")
    manager.sweep_index_cache(Settings(vault=VaultSettings(state_dir=str(state)), _env_file=None))
    assert listed.is_file() and not orphan.exists()


async def _moved(ac: httpx.AsyncClient, src, dst) -> dict:
    assert (await ac.post("/api/vault/move", json={"src": str(src), "dst": str(dst)})).status_code == 200
    for _ in range(200):
        status = (await ac.get("/api/vault/move/status")).json()
        if not status["active"]:
            return status
        await asyncio.sleep(0.02)
    raise AssertionError("the move never finished")


async def test_a_file_another_program_holds_never_leaves_a_half_moved_vault(client, monkeypatch):
    ac, tmp = client
    await ac.get("/api/vault/config")
    src = tmp / "default-vault"
    (src / "works" / "w").mkdir(parents=True)
    (src / "works" / "w" / "work.json").write_text("{}", "utf-8")
    (src / "works" / "w" / "Thumbs.db").write_bytes(b"explorer")  # open in Explorer on the share
    real = os.unlink

    def unlink(path, *a, **kw):
        if os.path.basename(path) == "Thumbs.db":
            raise PermissionError(32, "The process cannot access the file because it is being used by another process")
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "unlink", unlink)
    dst = tmp / "moved"
    status = await _moved(ac, src, dst)
    assert status["error"] is None  # the move landed…
    assert (await ac.get("/api/vault/config")).json()["active"] == str(dst)  # …the copy is the vault now…
    assert (dst / "works" / "w" / "work.json").is_file() and not (dst / "works" / "w" / "Thumbs.db").exists()
    assert [p.name for p in src.rglob("*") if p.is_file()] == ["Thumbs.db"]  # …and only the held litter stayed behind


async def test_a_failed_copy_leaves_the_vault_where_it_was(client, monkeypatch):
    ac, tmp = client
    await ac.get("/api/vault/config")
    src = tmp / "default-vault"
    (src / "keep.txt").write_text("hi", "utf-8")

    def broken(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copy2", broken)
    status = await _moved(ac, src, tmp / "moved")
    assert "disk full" in status["error"]
    assert (src / "keep.txt").is_file()  # nothing removed
    assert (await ac.get("/api/vault/config")).json()["active"] == str(src)
    assert not (tmp / "moved").exists()  # the partial copy is gone too — the same move can be retried
    monkeypatch.undo()
    assert (await _moved(ac, src, tmp / "moved"))["error"] is None
