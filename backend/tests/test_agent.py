"""Agent access (MCP): config persistence, the listener's auth gate, the JSON-RPC surface, read tools and
per-call permissions, and that agent access can never stop the app from starting. Offline: the MCP app is
driven through ASGITransport; a few tests bind real loopback ports to prove the listener starts, serves,
moves and stops."""
import asyncio
import base64
import io
import json
import logging
import socket
import threading
import time
from collections import OrderedDict, deque

import httpx
import keyring
import pytest
from fastapi import HTTPException
from PIL import Image as PILImage

from app import appconfig, keychain
from app import main as app_main
from app.agent import activity, changes, gallery, generation, store
from app.agent import server as agent_server
from app.agent.config import AgentConfig, AgentPermissions, coerce
from app.agent.server import _Config, build_app, listener
from app.routers import agent as agent_router
from app.main import app
from app.novelai import MockNovelAIClient
from app.novelai._png import solid_png
from app.novelai.errors import NovelAIRateLimitError
from app.settings import Settings, VaultSettings, get_settings
from app.vault import service
from app.vault import store as vault_store
from app.vault import presets
from app.vault.models import BlockDoc, Component, Image, PresetDoc, PresetParams, Snapshot, WorkDoc

TOKEN = "test-agent-token"
BASE = "http://127.0.0.1:8790"


@pytest.fixture
def settings(tmp_path):
    # Downloads too: a test must never write into the real one (rescued agent images land there).
    return Settings(vault=VaultSettings(dir=str(tmp_path / "vault"), state_dir=str(tmp_path / "state")),
                    download_dir=str(tmp_path / "downloads"), _env_file=None)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.setattr(activity, "_entries", deque(maxlen=activity.MAX_ENTRIES))
    monkeypatch.setattr(activity, "_sessions", {})
    monkeypatch.setattr(activity, "_seq", 0)
    monkeypatch.setattr(changes, "_log", OrderedDict())
    monkeypatch.setattr(changes, "_subscribers", set())
    monkeypatch.setattr(listener, "_lock", asyncio.Lock())  # each test runs on its own event loop
    listener.error = None


def mcp_client(settings: Settings, config: AgentConfig | None = None, base_url: str = BASE) -> httpx.AsyncClient:
    cfg = config or AgentConfig(enabled=True)
    transport = httpx.ASGITransport(app=build_app(settings, lambda: TOKEN, lambda: cfg))
    return httpx.AsyncClient(transport=transport, base_url=base_url, headers={"Authorization": f"Bearer {TOKEN}"})


async def rpc(client: httpx.AsyncClient, method: str, params: dict | None = None, msg_id: int = 1) -> dict:
    res = await client.post("/mcp", json={"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})
    assert res.status_code == 200, res.text
    return res.json()


async def call(client: httpx.AsyncClient, name: str, args: dict | None = None) -> dict:
    return (await rpc(client, "tools/call", {"name": name, "arguments": args or {}}))["result"]


async def call_json(client: httpx.AsyncClient, name: str, args: dict | None = None) -> object:
    result = await call(client, name, args)
    assert not result["isError"], result
    return json.loads(result["content"][0]["text"])


def seed(settings: Settings) -> None:
    service.save_block(settings, BlockDoc(id="block-raiden", category="character", name="Raiden", text="raiden shogun",
                                          tags=["genshin"]))
    service.save_block(settings, BlockDoc(id="block-sig", category="negative", name="No signature", text="signature",
                                          polarity="negative"))
    png = base64.b64encode(solid_png(64, 96)).decode("ascii")
    service.save_work(settings, WorkDoc(id="work-1", title="Halloween", images=[
        Image(id="img-1", image_b64=png, tags=["vampire"], description="first", favorite=True),
        Image(id="img-scratch", role="scratch", image_b64=png)]))


# ---- config ----
def test_off_by_default_and_everything_but_deleting_allowed():
    cfg = AgentConfig()
    assert not cfg.enabled
    assert cfg.permissions.model_dump() == {"read": True, "blocks_write": True, "blocks_delete": False,
                                            "works_write": True, "combos_write": True, "works_delete": False,
                                            "generate": True}
    assert cfg.spending.paid is False  # an agent spends Anlas only once the user allows it


def test_coerce_keeps_good_fields_and_drops_bad_ones():
    out = coerce({"enabled": True, "port": 9001, "permissions": "junk"})
    # the port is no setting — an old one is dropped
    assert out == {**AgentConfig().model_dump(), "enabled": True}
    # a config saved before the write switches existed gets their defaults
    assert coerce({"permissions": {"read": False}})["permissions"] == {**AgentPermissions().model_dump(), "read": False}
    assert coerce(None) == AgentConfig().model_dump()


def test_agent_section_survives_other_settings_writes(settings):
    store.update_config(settings, {"enabled": True, "permissions": {"read": False}})
    appconfig.patch(settings, {"theme": "light"})
    cfg = store.load_config(settings)
    assert cfg.enabled and not cfg.permissions.read


# ---- the auth gate ----
async def test_requests_need_the_agent_token(settings):
    async with mcp_client(settings) as c:
        for auth in ("", "Bearer wrong", f"Basic {TOKEN}"):
            res = await c.post("/mcp", json={}, headers={"Authorization": auth})
            assert res.status_code == 401
            assert res.headers["www-authenticate"] == "Bearer"


async def test_browser_origins_and_foreign_hosts_are_refused(settings):
    async with mcp_client(settings) as c:
        assert (await c.post("/mcp", json={}, headers={"Origin": "https://evil.example"})).status_code == 403
    async with mcp_client(settings, base_url="http://evil.example") as c:
        assert (await c.post("/mcp", json={})).status_code == 400


async def test_no_token_issued_means_nobody_gets_in(settings):
    transport = httpx.ASGITransport(app=build_app(settings, lambda: None, lambda: AgentConfig(enabled=True)))
    async with httpx.AsyncClient(transport=transport, base_url=BASE, headers={"Authorization": "Bearer "}) as c:
        assert (await c.post("/mcp", json={})).status_code == 401


# ---- protocol ----
async def test_initialize_negotiates_and_opens_a_session(settings):
    async with mcp_client(settings) as c:
        res = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                         "params": {"protocolVersion": "2025-03-26", "capabilities": {}}})
        body = res.json()["result"]
        assert body["protocolVersion"] == "2025-03-26"
        assert body["capabilities"] == {"tools": {"listChanged": False}}
        assert res.headers["mcp-session-id"]
        assert activity.connected() == 1
        # an unknown version gets our newest; a notification gets 202 and no body
        newer = await rpc(c, "initialize", {"protocolVersion": "2099-01-01"})
        assert newer["result"]["protocolVersion"] == "2025-06-18"
        note = await c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert note.status_code == 202 and note.content == b""
        # the client ending its session
        assert (await c.delete("/mcp", headers={"Mcp-Session-Id": res.headers["mcp-session-id"]})).status_code == 204
        assert (await c.get("/mcp")).status_code == 405


async def test_errors_follow_json_rpc(settings):
    async with mcp_client(settings) as c:
        assert (await rpc(c, "nope"))["error"]["code"] == -32601
        assert (await rpc(c, "tools/call", {"name": "nope"}))["error"]["code"] == -32602
        bad = await c.post("/mcp", content=b"{not json", headers={"Content-Type": "application/json"})
        assert bad.status_code == 400 and bad.json()["error"]["code"] == -32700
        batch = await c.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                           {"jsonrpc": "2.0", "method": "notifications/initialized"}])
        assert batch.json() == [{"jsonrpc": "2.0", "id": 1, "result": {}}]


async def test_tools_list_describes_every_tool(settings):
    async with mcp_client(settings) as c:
        tools = (await rpc(c, "tools/list"))["result"]["tools"]
    names = {t["name"] for t in tools}
    assert {"search_blocks", "get_work", "get_image", "list_categories"} <= names
    for t in tools:
        assert t["inputSchema"]["type"] == "object" and t["description"]


# ---- read tools ----
async def test_search_and_get_blocks(settings):
    seed(settings)
    async with mcp_client(settings) as c:
        found = await call_json(c, "search_blocks", {"query": "raiden"})
        assert found["total"] == 1 and found["items"][0]["id"] == "block-raiden"
        neg = await call_json(c, "search_blocks", {"categories": ["negative"]})
        assert [b["polarity"] for b in neg["items"]] == ["negative"]
        got = await call_json(c, "get_blocks", {"ids": ["block-sig", "block-missing"]})
        assert [b["id"] for b in got] == ["block-sig"]
        cats = await call_json(c, "list_categories")
        assert {"slug": "character", "name": "Character", "color": "#0c66e4", "blocks": 1} in cats


_RECIPE = {"model": "nai-diffusion-5-full", "width": 832, "height": 1216, "steps": 28, "scale": 5.0, "sampler": "k_euler",
           "seed": 7, "n_samples": 1, "noise_schedule": "karras", "cfg_rescale": 0.2, "quality_toggle": False,
           "uc_preset": 3, "dedupe": True}


def seed_recipe_image(settings: Settings) -> None:
    """A second gallery image in work-1, with the full recipe it was generated from — the newest one."""
    doc = service.load_work(settings, "work-1")
    doc.snapshots.append(Snapshot(
        id="snap-2", hash="h", assembled_positive="raiden shogun", assembled_negative="lowres", params=_RECIPE,
        components=[Component(source="library", block_id="block-raiden", version=1, name="Raiden", text="raiden shogun",
                              tags=["genshin"])]))
    doc.images.append(Image(id="img-2", snapshot_id="snap-2", image_b64=base64.b64encode(solid_png(256, 384)).decode(),
                            tags=["night"], created_at="2099-01-01T00:00:00.000+00:00"))
    service.save_work(settings, doc)


async def test_get_work_is_an_overview_and_list_images_pages_the_gallery(settings):
    seed(settings)
    seed_recipe_image(settings)
    async with mcp_client(settings) as c:
        works = await call_json(c, "list_works")
        assert works["items"][0]["title"] == "Halloween"
        work = await call_json(c, "get_work", {"work_id": "work-1"})
        assert work["image_count"] == 2 and "images" not in work and "canvas" not in work  # the scratch one isn't counted
        page = await call_json(c, "list_images", {"work_id": "work-1", "per_page": 1})
        assert page["total"] == 2 and [im["id"] for im in page["items"]] == ["img-2"]  # newest first
        assert page["items"][0]["params"] == _RECIPE  # the whole recipe, quality tags and UC preset included
        assert "prompt" not in page["items"][0] and "group" not in page["items"][0]
        full = await call_json(c, "list_images", {"work_id": "work-1", "tags": ["night"], "include_prompt": True})
        assert full["items"][0]["prompt"] == {"positive": "raiden shogun", "negative": "lowres"}
        assert full["items"][0]["blocks"] == [{"name": "Raiden", "block_id": "block-raiden", "version": 1,
                                               "polarity": "positive"}]
        favs = await call_json(c, "list_images", {"work_id": "work-1", "favorite": True})
        assert [im["id"] for im in favs["items"]] == ["img-1"]
        found = await call_json(c, "search_images", {"tags": ["vampire"]})
        assert [(i["image_id"], i["work_id"], i["work_title"]) for i in found["items"]] == [("img-1", "work-1", "Halloween")]
        # a tag from the blocks an image was made of matches in both, as it does in the app's gallery
        assert [i["image_id"] for i in (await call_json(c, "search_images", {"tags": ["genshin"]}))["items"]] == ["img-2"]
        by_block = await call_json(c, "list_images", {"work_id": "work-1", "tags": ["genshin"]})
        assert [(im["id"], im["tags"], im["block_tags"]) for im in by_block["items"]] == [("img-2", ["night"], ["genshin"])]
        # paged one by one, every image comes once
        pages = [await call_json(c, "search_images", {"page": p, "per_page": 1}) for p in (1, 2)]
        assert sorted(p["items"][0]["image_id"] for p in pages) == ["img-1", "img-2"]


async def test_images_are_seen_whole_close_up_or_side_by_side(settings):
    seed(settings)
    seed_recipe_image(settings)
    async with mcp_client(settings) as c:
        image = await call(c, "get_image", {"work_id": "work-1", "image_id": "img-1", "width": 64})
        block = image["content"][0]
        assert block["type"] == "image" and block["mimeType"] == "image/png"
        assert base64.b64decode(block["data"])[:8] == b"\x89PNG\r\n\x1a\n"
        crop = await call(c, "get_image", {"work_id": "work-1", "image_id": "img-1",
                                           "crop": {"x": 8, "y": 8, "width": 32, "height": 40}})
        with PILImage.open(io.BytesIO(base64.b64decode(crop["content"][0]["data"]))) as im:
            assert im.size == (32, 40)  # the source's own pixels, not a scaled copy
        off = await call(c, "get_image", {"work_id": "work-1", "image_id": "img-1",
                                          "crop": {"x": 60, "y": 0, "width": 32, "height": 32}})
        assert off["isError"] and "64×96" in off["content"][0]["text"]  # past the edge — and it says how big it is
        small = await call(c, "get_image", {"work_id": "work-1", "image_id": "img-1", "width": 64,
                                            "crop": {"x": 0, "y": 0, "width": 64, "height": 96}})
        with PILImage.open(io.BytesIO(base64.b64decode(small["content"][0]["data"]))) as im:
            assert im.size == (64, 96)  # within 'width': sent as it is
        wide = await call(c, "get_image", {"work_id": "work-1", "image_id": "img-2", "width": 64,
                                           "crop": {"x": 0, "y": 0, "width": 256, "height": 128}})
        with PILImage.open(io.BytesIO(base64.b64decode(wide["content"][0]["data"]))) as im:
            assert im.size == (64, 32)  # wider than 'width': scaled down to it
        sheet = await call(c, "contact_sheet", {"work_id": "work-1", "cols": 2, "tile": 64})
        assert sheet["content"][1] == {"type": "text", "text": "1: img-2\n2: img-1"}
        with PILImage.open(io.BytesIO(base64.b64decode(sheet["content"][0]["data"]))) as im:
            assert im.size == (128, 96)  # two 64 px tiles in a row, 2:3 cells
        assert (await call(c, "contact_sheet", {"work_id": "work-1", "image_ids": ["img-scratch"]}))["isError"]


async def test_reads_are_logged_and_folded(settings):
    seed(settings)
    async with mcp_client(settings) as c:
        await call(c, "search_blocks", {"query": "raiden"})
        await call(c, "search_blocks", {"query": "yae"})
        await call(c, "get_image", {"work_id": "work-1", "image_id": "img-1"})
    rows = activity.recent()
    assert [(r["action"], r["count"]) for r in rows] == [("View an image", 1), ("Search Library blocks", 2)]
    assert rows[0]["detail"] == "work id “work-1”, image id “img-1”"
    assert rows[1]["detail"] == "query “yae”"
    assert {r["kind"] for r in rows} == {"read"}
    assert rows[0]["seq"] > rows[1]["seq"] == 2  # the fold counted as news: seq 1 became 2


async def test_activity_rows_say_what_kind_of_call_they_were(settings):
    seed(settings)
    async with mcp_client(settings) as c:
        await call_json(c, "create_work", {"title": "Autumn"})
        await call(c, "delete_blocks", {"ids": ["block-raiden"]})  # off by default
        await call(c, "search_blocks", {"query": "raiden"})
    assert [(r["kind"], r["outcome"]) for r in activity.recent()] == [("read", "ok"), ("write", "denied"),
                                                                      ("write", "ok")]


async def test_agent_mistakes_come_back_as_tool_errors(settings):
    seed(settings)
    async with mcp_client(settings) as c:
        missing = await call(c, "get_work", {"work_id": "work-nope"})
        assert missing["isError"] and "not found" in missing["content"][0]["text"].lower()
        bad = await call(c, "search_blocks", {"per_page": 5000})
        assert bad["isError"] and "per_page" in bad["content"][0]["text"]
        assert (await call(c, "get_blocks", {"ids": "block-sig"}))["isError"]


async def test_a_switched_off_permission_refuses_and_is_logged(settings):
    seed(settings)
    cfg = AgentConfig(enabled=True)
    cfg.permissions.read = False
    async with mcp_client(settings, cfg) as c:
        res = await call(c, "search_blocks", {"query": "raiden"})
    assert res["isError"] and "Browse the Library and works" in res["content"][0]["text"]
    assert activity.recent()[0]["outcome"] == "denied"


# ---- hostile / malformed requests ----
async def test_malformed_requests_are_refused_cleanly(settings):
    async with mcp_client(settings) as c:
        for host in ("[", "evil.example@127.0.0.1"):
            assert (await c.post("/mcp", json={}, headers={"Host": host})).status_code == 400
        for host in ("localhost:8790", "[::1]:8790"):
            assert (await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                 headers={"Host": host})).status_code == 200
        assert (await c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                             headers={"MCP-Protocol-Version": "1999-01-01"})).status_code == 400
        assert (await rpc(c, "tools/call", {"name": ["get_work"]}))["error"]["code"] == -32602
        no_method = await c.post("/mcp", json={"jsonrpc": "2.0", "id": 7})
        assert no_method.json()["error"]["code"] == -32600
        assert (await c.post("/mcp", json=[])).status_code == 400
        ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        assert (await c.post("/mcp", json=[ping] * 17)).status_code == 400
        deep = b"[" * 100_000 + b"]" * 100_000
        assert (await c.post("/mcp", content=deep, headers={"Content-Type": "application/json"})).status_code == 400
        big = await c.post("/mcp", content=b" " * 1_000_001, headers={"Content-Type": "application/json"})
        assert big.status_code == 413


# ---- write tools ----
def allowing(**switches: bool) -> AgentConfig:
    """Agent access on, with these permission switches set (e.g. a delete one turned on)."""
    cfg = AgentConfig(enabled=True)
    for key, value in switches.items():
        setattr(cfg.permissions, key, value)
    return cfg


async def test_new_blocks_follow_the_import_rules(settings):
    seed(settings)
    with changes.subscribe() as notices:
        async with mcp_client(settings) as c:
            out = await call_json(c, "create_blocks", {"blocks": [
                {"category": "Character", "name": "Yae", "text": "yae miko", "tags": ["genshin", " genshin "]},
                {"text": "Raiden   SHOGUN"},  # the seeded block, spelled differently
                {"text": "  "},
                {"text": "yae miko", "polarity": "positive"},  # repeats the first one
                {"category": "Jewelry", "text": "pearl necklace"},  # a new category, as the import makes one
                {"category": "jewelry", "text": "gold ring"},  # the same one, by its name in another case
                {"category": "Never made", "text": "raiden shogun"},  # a duplicate: no category made for it
            ]})
        notice = await asyncio.wait_for(notices.get(), 1)
    made, pearl, ring = out["created"]
    assert made["category"] == "character" and made["name"] == "Yae"
    assert [(d["index"], d["existing_id"]) for d in out["duplicates"]] == [(1, "block-raiden"), (3, made["id"]),
                                                                          (6, "block-raiden")]
    assert [e["index"] for e in out["errors"]] == [2]
    assert out["new_categories"] == ["Jewelry"] and pearl["category"] == ring["category"] != "custom"
    cats = {c.slug: c.name for c in service.list_categories(settings)}
    assert cats[pearl["category"]] == "Jewelry" and "Never made" not in cats.values()
    [stored] = service.resolve_blocks(settings, [made["id"]])
    assert (stored.text, stored.tags, stored.version) == ("yae miko", ["genshin"], 1)
    row = activity.recent()[0]
    assert (row["action"], row["detail"]) == ("Added 3 blocks",
                                              "Library → 2 categories (3 duplicates skipped) · new category “Jewelry”")
    assert notice["message"] == f"Agent added 3 blocks — {row['detail']}"


async def test_new_categories_get_a_free_colour_and_a_stale_id_makes_none(settings):
    seed(settings)
    async with mcp_client(settings) as c:
        moved = await call_json(c, "update_block", {"id": "block-raiden", "category": "Characters - Genshin"})
        stale = await call(c, "create_blocks", {"blocks": [
            {"category": "cat-3f2a6b1e-0000-4000-8000-000000000000", "text": "a"}]})
    cats = {c.slug: c for c in service.list_categories(settings)}
    made = cats[moved["category"]]
    assert (made.name, moved["new_categories"]) == ("Characters - Genshin", ["Characters - Genshin"])
    assert made.color not in {c.color for slug, c in cats.items() if slug != made.slug}  # the first hue not in use
    assert "list_categories" in json.loads(stale["content"][0]["text"])["errors"][0]["error"]
    assert len(service.list_categories(settings)) == len(cats)


async def test_blocks_are_edited_and_deleted_only_when_allowed(settings):
    seed(settings)
    async with mcp_client(settings) as c:
        edited = await call_json(c, "update_block", {"id": "block-raiden", "text": "raiden shogun, electro"})
        assert edited["version"] == 2 and edited["name"] == "Raiden"  # only the given field changed
        refused = await call(c, "delete_blocks", {"ids": ["block-raiden"]})
        assert refused["isError"] and "Delete blocks" in refused["content"][0]["text"]
    assert activity.recent()[0]["outcome"] == "denied"
    async with mcp_client(settings, allowing(blocks_delete=True)) as c:
        out = await call_json(c, "delete_blocks", {"ids": ["block-raiden", "block-nope"]})
    assert out == {"deleted": ["block-raiden"], "missing": ["block-nope"]}
    assert service.resolve_blocks(settings, ["block-raiden"]) == []
    assert activity.recent()[0]["detail"] == "“Raiden”"


async def test_works_are_created_renamed_and_their_images_described(settings):
    seed(settings)
    async with mcp_client(settings) as c:
        made = await call_json(c, "create_work", {"title": "Autumn set"})
        assert (await call_json(c, "get_work", {"work_id": made["id"]}))["title"] == "Autumn set"
        await call_json(c, "rename_work", {"work_id": made["id"], "title": "Autumn"})
        out = await call_json(c, "update_images", {"work_id": "work-1", "images": [
            {"id": "img-1", "tags": ["vampire", "night"], "description": "moonlit", "favorite": False}]})
        assert out == {"updated": ["img-1"]}
        scratch = await call(c, "update_images", {"work_id": "work-1", "images": [{"id": "img-scratch", "tags": []}]})
        assert scratch["isError"] and "No gallery image" in scratch["content"][0]["text"]
    assert service.load_work(settings, made["id"]).title == "Autumn"
    im = next(i for i in service.load_work(settings, "work-1").images if i.id == "img-1")
    assert (im.tags, im.description, im.favorite) == (["vampire", "night"], "moonlit", False)
    assert [r["action"] for r in activity.recent()] == ["Updated 1 image", "Renamed a work", "Get a work", "Created a work"]
    assert activity.recent()[1]["detail"] == "“Autumn set” → “Autumn”"


async def test_deleted_images_leave_every_structure(settings):
    png = base64.b64encode(solid_png(8, 8)).decode("ascii")
    service.save_work(settings, WorkDoc(
        id="work-2", title="Two", preview_image_id="img-a",
        images=[Image(id="img-a", image_b64=png), Image(id="img-b", image_b64=png)],
        canvas={"nodes": [{"id": "gallery", "type": "zone", "data": {"role": "gallery", "blocks": [
                    {"id": "gb-1", "type": "grid", "imageIds": ["img-a", "img-b"]}]}},
                {"id": "img-a", "type": "image", "parentNode": "gallery"},
                {"id": "img-b", "type": "image", "parentNode": "gallery"}]}))
    async with mcp_client(settings, allowing(works_delete=True)) as c:
        out = await call_json(c, "delete_images", {"work_id": "work-2", "image_ids": ["img-a", "img-zz"]})
    assert out == {"deleted": ["img-a"], "missing": ["img-zz"]}
    doc = service.load_work(settings, "work-2")
    assert [im.id for im in doc.images] == ["img-b"] and doc.preview_image_id == "img-b"
    assert [n["id"] for n in doc.canvas["nodes"]] == ["gallery", "img-b"]
    assert doc.canvas["nodes"][0]["data"]["blocks"][0]["imageIds"] == ["img-b"]
    work_dir = next((appconfig.active_vault(settings) / "works").iterdir())
    assert sorted(p.name for p in (work_dir / "images").iterdir()) == ["img-b.json", "img-b.png"]


async def test_a_work_is_deleted_only_when_allowed_and_the_app_is_told(settings):
    seed(settings)
    async with mcp_client(settings) as c:
        refused = await call(c, "delete_work", {"work_id": "work-1"})
        assert refused["isError"] and "“Delete works, images and combinations” is turned off" in refused["content"][0]["text"]
    with changes.subscribe() as notices:
        async with mcp_client(settings, allowing(works_delete=True)) as c:
            assert await call_json(c, "delete_work", {"work_id": "work-1"}) == {"deleted": "work-1"}
            assert (await call(c, "get_work", {"work_id": "work-1"}))["isError"]
        notice = await asyncio.wait_for(notices.get(), 1)
    assert (notice["kind"], notice["work_id"]) == ("work_deleted", "work-1")  # the app closes it if it's open
    assert (activity.recent()[0]["action"], activity.recent()[0]["detail"]) == ("Deleted a work", "“Halloween” with 1 image")
    assert not list((appconfig.active_vault(settings) / "works").iterdir())


async def test_images_move_or_copy_to_another_work_with_their_recipes(api, settings):
    seed(settings)
    seed_recipe_image(settings)  # work-1: img-1 (favourite, no recipe) and img-2 (with its recipe)
    src_base = service.load_work(settings, "work-1").updated_at
    with changes.subscribe() as notices:
        async with mcp_client(settings) as c:
            other = await call_json(c, "create_work", {"title": "Picks"})
            copied = await call_json(c, "move_images", {"from_work_id": "work-1", "to_work_id": other["id"],
                                                        "image_ids": ["img-2"], "copy": True})
            moved = await call_json(c, "move_images", {"from_work_id": "work-1", "to_work_id": other["id"],
                                                       "image_ids": ["img-1"]})
            for args, message in [({"image_ids": ["img-scratch"]}, "No gallery image “img-scratch”"),
                                  ({"to_work_id": "work-1"}, "the work they are in already"),
                                  ({"to_work_id": "work-nope"}, "not found")]:
                res = await call(c, "move_images", {"from_work_id": "work-1", "to_work_id": other["id"],
                                                    "image_ids": ["img-2"], **args})
                assert res["isError"] and message in res["content"][0]["text"], (args, res)
        *_, move_notice = [notices.get_nowait() for _ in range(notices.qsize())]
    assert (move_notice["work_id"], move_notice["also"]) == (other["id"], ["work-1"])  # both open copies catch up
    new2, new1 = copied["images"][0]["id"], moved["images"][0]["id"]
    assert copied["copied"] and not moved["copied"] and new2 != "img-2"
    src, dst = service.load_work(settings, "work-1"), service.load_work(settings, other["id"])
    assert [im.id for im in src.images if im.role == "gallery"] == ["img-2"]  # the copied one stays, the moved one left
    assert {im.id for im in dst.images} == {new1, new2} and dst.preview_image_id == new2
    im2, im1 = (next(im for im in dst.images if im.id == i) for i in (new2, new1))
    recipe = next(s for s in dst.snapshots if s.id == im2.snapshot_id)
    assert (recipe.params, recipe.id != "snap-2", im2.tags, im2.created_at) == (
        _RECIPE, True, ["night"], "2099-01-01T00:00:00.000+00:00")
    assert (im1.favorite, im1.description, im1.tags, im1.snapshot_id) == (True, "first", ["vampire"], None)
    assert (service.image_path(settings, other["id"], new2).read_bytes()
            == service.image_path(settings, "work-1", "img-2").read_bytes())
    ops = (await api.get("/api/agent/works/work-1/changes", params={"since": src_base})).json()["ops"]
    assert ops == [{"op": "images_deleted", "ids": ["img-1"]}]
    assert [r["action"] for r in activity.recent() if r["kind"] == "write"][:2] == ["Moved images", "Copied images"]


async def test_blocks_are_starred_in_a_work(settings):
    seed(settings)
    async with mcp_client(settings) as c:
        out = await call_json(c, "star_blocks", {"work_id": "work-1", "block_ids": ["block-raiden", "block-sig"]})
        assert out["favorite_block_ids"] == ["block-raiden", "block-sig"]
        assert (await call_json(c, "star_blocks", {"work_id": "work-1", "block_ids": ["block-raiden"]}))[
            "favorite_block_ids"] == ["block-raiden", "block-sig"]  # starred already: nothing to write
        off = await call_json(c, "star_blocks", {"work_id": "work-1", "block_ids": ["block-raiden"], "starred": False})
        assert off["favorite_block_ids"] == ["block-sig"]
        bad = await call(c, "star_blocks", {"work_id": "work-1", "block_ids": ["block-nope"]})
        assert bad["isError"] and "No Library block “block-nope”" in bad["content"][0]["text"]
        assert (await call_json(c, "get_work", {"work_id": "work-1"}))["favorite_block_ids"] == ["block-sig"]
    writes = [(r["action"], r["detail"]) for r in activity.recent() if r["kind"] == "write"]
    assert writes == [("Unstarred 1 block", "“Raiden” in “Halloween”"),
                      ("Starred 2 blocks", "“Raiden”, “No signature” in “Halloween”")]


async def test_combinations_are_made_from_library_and_custom_blocks(settings):
    seed(settings)
    async with mcp_client(settings, allowing(works_delete=True)) as c:
        made = await call_json(c, "create_combination", {"work_id": "work-1", "name": "Raiden — night", "blocks": [
            {"block_id": "block-raiden"}, {"text": "night sky", "frozen": True}]})
        again = await call_json(c, "create_combination", {"work_id": "work-1", "name": "Raiden — night",
                                                          "blocks": [{"text": "moon"}]})
        assert again["name"] == "Raiden — night (2)"
        repeat = await call(c, "create_combination", {"work_id": "work-1", "name": "x",
                                                      "blocks": [{"text": "moon"}, {"text": "Moon "}]})
        assert repeat["isError"] and "repeats blocks[0]" in repeat["content"][0]["text"]
        unknown = await call(c, "create_combination", {"work_id": "work-1", "name": "x", "blocks": [{"block_id": "b-0"}]})
        assert unknown["isError"]
        listed = await call_json(c, "list_combinations", {"work_id": "work-1"})
        assert [x["id"] for x in listed] == [again["id"], made["id"]]  # newest first
        lib, custom = listed[1]["blocks"]
        assert (lib["source"], lib["block_id"], lib["version"], lib["text"]) == ("library", "block-raiden", 1, "raiden shogun")
        assert (custom["source"], custom["category"], custom["frozen"]) == ("custom", "custom", True)
        assert listed[1]["origin"] == "agent" and listed[1]["created_at"]

        await call_json(c, "update_combination", {"work_id": "work-1", "combination_id": made["id"],
                                                  "name": "Raiden — dusk", "blocks": [{"text": "dusk"}]})
        await call_json(c, "delete_combination", {"work_id": "work-1", "combination_id": again["id"]})
    [left] = service.load_work(settings, "work-1").combos
    assert (left.name, [b.text for b in left.blocks]) == ("Raiden — dusk", ["dusk"])
    assert left.updated_at > left.created_at


# ---- the gallery layout ----
def seed_gallery(settings: Settings) -> WorkDoc:
    """work-g as the app saved it: img-a … img-d (oldest first) in the gallery, one scratch image; a heading, then a
    section "Picks" holding a grid of img-a and img-b (and a stale id) with a summary of it, then a collapsed section
    "Rest" holding a divider. img-c and img-d are in no grid."""
    png = base64.b64encode(solid_png(8, 12)).decode("ascii")
    ids = ["img-a", "img-b", "img-c", "img-d"]
    blocks = [
        {"id": "h-1", "type": "heading", "text": "Intro", "level": 1},
        {"id": "s-1", "type": "section", "title": "Picks", "collapsed": False},
        {"id": "g-1", "type": "grid", "imageIds": ["img-a", "img-gone", "img-b"], "cols": 4},
        {"id": "m-1", "type": "meta", "fields": [{"key": "Tags", "kind": "chips", "auto": "tags"}], "gridId": "g-1"},
        {"id": "s-2", "type": "section", "title": "Rest", "collapsed": True},
        {"id": "d-1", "type": "divider"},
    ]
    service.save_work(settings, WorkDoc(
        id="work-g", title="G", preview_image_id="img-a",
        images=[Image(id=i, image_b64=png, created_at=f"2026-10-0{k + 1}T00:00:00.000+00:00") for k, i in enumerate(ids)]
        + [Image(id="img-s", role="scratch", image_b64=png)],
        canvas={"viewport": {"x": 0, "y": 0, "zoom": 1}, "nodes": [
            {"id": "gallery", "type": "zone", "position": {"x": 1600, "y": 40}, "data": {"role": "gallery", "blocks": blocks}},
            *({"id": i, "type": "image", "parentNode": "gallery", "position": {"x": 0, "y": 0}, "data": {}} for i in ids),
            {"id": "img-s", "type": "image", "position": {"x": 900, "y": 900}, "data": {}}]}))
    return service.load_work(settings, "work-g")


async def test_the_gallery_reads_as_the_app_shows_it(settings):
    seed_gallery(settings)
    async with mcp_client(settings) as c:
        view = await call_json(c, "get_gallery", {"work_id": "work-g"})
    assert (view["cover_image_id"], view["image_count"]) == ("img-a", 4)
    assert view["blocks"] == [
        {"id": "h-1", "type": "heading", "text": "Intro", "level": 1},
        {"id": "s-1", "type": "section", "title": "Picks", "collapsed": False},
        {"id": "g-1", "type": "grid", "section": "s-1", "cols": 4, "collapsed": False, "image_ids": ["img-a", "img-b"]},
        {"id": "m-1", "type": "meta", "section": "s-1", "grid": "g-1"},
        {"id": "s-2", "type": "section", "title": "Rest", "collapsed": True},
        {"id": "d-1", "type": "divider", "section": "s-2"},
    ]
    assert view["quick_access"] == ["img-d", "img-c"]  # newest first; the scratch image is never in the gallery


async def test_an_agent_arranges_the_gallery_and_the_app_replays_it(api, settings):
    base = seed_gallery(settings)
    async with mcp_client(settings) as c:
        out = await call_json(c, "edit_gallery", {"work_id": "work-g", "ops": [
            {"op": "add", "type": "section", "title": "Night", "ref": "night", "after": "s-1"},  # after Picks' blocks
            {"op": "add", "type": "grid", "cols": 3, "ref": "album", "after": "night", "image_ids": ["img-d", "img-c"]},
            {"op": "place", "image_ids": ["img-b"], "grid": "album", "index": 1},  # out of g-1, between d and c
            {"op": "update", "block": "g-1", "cols": 2, "collapsed": True},
            {"op": "move", "block": "s-2", "before": "s-1"},  # with the divider under it
            {"op": "remove", "block": "h-1"},
            {"op": "add", "type": "meta", "grid": "album", "after": "album"},
            {"op": "cover", "image_id": "img-c"},
        ]})
        assert out["gallery"] == await call_json(c, "get_gallery", {"work_id": "work-g"})
    night, album, meta = (x["id"] for x in out["created"])
    assert [x.get("ref") for x in out["created"]] == ["night", "album", None]
    view = out["gallery"]
    assert [(b["id"], b.get("section")) for b in view["blocks"]] == [
        ("s-2", None), ("d-1", "s-2"), ("s-1", None), ("g-1", "s-1"), ("m-1", "s-1"),
        (night, None), (album, night), (meta, night)]
    grids = {b["id"]: b for b in view["blocks"] if b["type"] == "grid"}
    assert grids[album]["image_ids"] == ["img-d", "img-b", "img-c"]
    assert (grids["g-1"]["cols"], grids["g-1"]["collapsed"], grids["g-1"]["image_ids"]) == (2, True, ["img-a"])
    assert view["blocks"][-1]["grid"] == album
    assert (view["quick_access"], view["cover_image_id"]) == ([], "img-c")
    row = activity.recent()[1]  # under the get_gallery read
    assert (row["kind"], row["action"]) == ("write", "Arranged the gallery")
    assert row["detail"] == ("of “G” · 3 blocks added, 1 block updated, 1 block moved, 1 block removed, "
                             "3 images placed, cover set")

    # the open app catches up by replaying the same operations over its copy — to the same layout, however often
    stored = service.load_work(settings, "work-g")
    res = (await api.get("/api/agent/works/work-g/changes", params={"since": base.updated_at})).json()
    assert res["at"] == stored.updated_at
    replayed = base.model_copy(deep=True)
    for op in res["ops"] * 2:
        gallery.apply(replayed, op)
    assert (gallery.stack(replayed), replayed.preview_image_id) == (gallery.stack(stored), "img-c")
    assert stored.canvas["nodes"][0]["position"] == {"x": 1600, "y": 40}  # the layout around the gallery stays


async def test_a_gallery_batch_replays_to_the_same_layout_however_often(settings):
    base = seed_gallery(settings)
    async with mcp_client(settings) as c:
        out = await call_json(c, "edit_gallery", {"work_id": "work-g", "ops": [
            {"op": "place", "image_ids": ["img-c"], "grid": "g-1", "index": 0},
            {"op": "place", "image_ids": ["img-a"], "grid": "g-1"},  # the image the first one landed next to moves on
            {"op": "move", "block": "s-2", "before": "s-1"},
            {"op": "move", "block": "h-1", "after": "d-1"},  # …and so does the block the first move landed after
        ]})
    stored = service.load_work(settings, "work-g")
    assert next(b for b in out["gallery"]["blocks"] if b["id"] == "g-1")["image_ids"] == ["img-c", "img-b", "img-a"]
    assert [b["id"] for b in out["gallery"]["blocks"]] == ["s-2", "d-1", "h-1", "s-1", "g-1", "m-1"]
    ops = changes.edits_since("work-g", base.updated_at, stored.updated_at)
    for times in (1, 2, 3):
        replayed = base.model_copy(deep=True)
        for op in ops * times:
            gallery.apply(replayed, op)
        assert gallery.stack(replayed) == gallery.stack(stored), times


async def test_a_bad_gallery_op_changes_nothing_and_says_which(settings):
    before = seed_gallery(settings)
    cases = [
        ([{"op": "add", "type": "grid", "ref": "x"}, {"op": "place", "image_ids": ["img-s"], "grid": "x"}],
         "op 2 (place): No gallery image “img-s”"),
        ([{"op": "place", "image_ids": ["img-c"], "grid": "s-1"}], "“s-1” is a section, not a grid"),
        ([{"op": "update", "block": "s-1", "cols": 3}], "'cols' doesn't apply to a section"),
        ([{"op": "move", "block": "s-1", "after": "g-1"}], "'after' names the block being moved or one under it"),
        ([{"op": "move", "block": "s-2", "after": "g-1"}], "not in among its blocks"),  # would split Picks
        ([{"op": "update", "block": "g-1", "image_ids": ["img-c"]}], "Images go into a grid with place"),
        ([{"op": "update", "block": "s-1", "title": " "}], "'title' is required"),
        ([{"op": "add", "type": "grid", "ref": "g-1"}], "The ref “g-1” is taken"),
        ([{"op": "remove", "block": "nope"}], "No gallery block “nope”"),
        ([{"op": "add", "type": "heading", "text": "x", "level": 3}], "'level' must be 1 or 2"),
        ([{"op": "add", "type": "divider", "before": "s-1", "after": "s-2"}], "not both"),
        ([{"op": "update", "block": "d-1"}], "Nothing to update"),
        ([{"op": "shuffle"}], "'op' must be one of"),
        ([{"op": "cover", "image_id": "img-s"}], "No gallery image “img-s”"),
    ]
    async with mcp_client(settings) as c:
        for ops, message in cases:
            res = await call(c, "edit_gallery", {"work_id": "work-g", "ops": ops})
            text = res["content"][0]["text"]
            assert res["isError"] and text.startswith("Nothing changed — ") and message in text, (ops, text)
        # already so: no save, nothing for the app to replay — it reads as a read
        same = await call_json(c, "edit_gallery", {"work_id": "work-g", "ops": [
            {"op": "unplace", "image_ids": ["img-c"]}, {"op": "cover", "image_id": "img-a"}]})
    assert same["created"] == [] and same["gallery"]["quick_access"] == ["img-d", "img-c"]
    after = service.load_work(settings, "work-g")
    assert (after.updated_at, after.canvas) == (before.updated_at, before.canvas)
    assert "work-g" not in changes._log
    assert [(r["kind"], r["action"]) for r in activity.recent()] == [("read", "Arrange a work's gallery")]


async def test_an_agent_sets_the_station_and_the_app_replays_it(api, settings):
    seed(settings)
    doc = service.load_work(settings, "work-1")
    doc.params = {"model": "nai-diffusion-5-full", "steps": 20, "seed": 77, "dedupe": False}
    base = service.save_work(settings, doc)["updated_at"]
    presets.save_preset(settings, PresetDoc(id="preset-portrait", name="Portrait", params=PresetParams(
        steps=23, uc_preset=3, n_samples=2, dedupe=True)))
    async with mcp_client(settings) as c:
        out = await call_json(c, "update_work_params", {"work_id": "work-1", "preset_id": "preset-portrait", "steps": 24})
        # the preset as the app applies it (image count too), the call's steps on top; the seed is the user's
        assert {k: out["params"][k] for k in ("steps", "uc_preset", "n_samples", "dedupe", "seed")} == {
            "steps": 24, "uc_preset": 3, "n_samples": 2, "dedupe": True, "seed": 77}
        assert "steps" in out["changed"] and "model" not in out["changed"]  # only what differs is written
        assert (await call_json(c, "update_work_params", {"work_id": "work-1", "steps": 24}))["changed"] == []
        for args, message in [({"sampler": "k_nope"}, "No sampler “k_nope”"), ({"width": 830}, "multiples of 64"),
                              ({"steps": 60}, "'steps'"), ({"model": "nai-x"}, "Unknown model"),
                              ({"uc_preset": 9}, "'uc_preset'"), ({}, "Give the settings"),
                              ({"steps": True}, "'steps'"), ({"width": "832"}, "'width'"),  # no lax coercion
                              ({"preset_id": "preset-nope"}, "list_presets")]:
            res = await call(c, "update_work_params", {"work_id": "work-1", **args})
            assert res["isError"] and message in res["content"][0]["text"], (args, res)
        cfg = await call_json(c, "update_work_params", {"work_id": "work-1", "cfg_rescale": 0.2, "quality_toggle": False})
    stored = service.load_work(settings, "work-1")
    assert (stored.params["steps"], stored.params["seed"], cfg["changed"]) == (24, 77, ["cfg_rescale", "quality_toggle"])
    row = next(r for r in activity.recent() if r["kind"] == "write")
    assert (row["action"], row["detail"]) == ("Changed the generation settings",
                                              "of “Halloween” · cfg_rescale 0.2, quality_toggle off")  # values shown
    preset_op, own_op = (await api.get("/api/agent/works/work-1/changes", params={"since": base})).json()["ops"]
    # a preset lands whole in the open app, as its Apply does; a plain call only what changed — the seed never
    assert (preset_op["preset_id"], preset_op["set"]["steps"], preset_op["set"]["model"]) == (
        "preset-portrait", 24, "nai-diffusion-5-full")
    assert own_op == {"op": "params", "set": {"cfg_rescale": 0.2, "quality_toggle": False}}
    assert all("seed" not in op["set"] for op in (preset_op, own_op))


async def test_a_value_the_station_keeps_from_another_model_stays_untouched(settings):
    seed(settings)
    doc = service.load_work(settings, "work-1")  # Furry Focus kept after switching to a model without it, as the app does
    doc.params = {"model": "nai-diffusion-4-5-curated", "uc_preset": 7, "steps": 28}
    service.save_work(settings, doc)
    async with mcp_client(settings) as c:
        assert (await call_json(c, "update_work_params", {"work_id": "work-1", "steps": 24}))["changed"] == ["steps"]
        res = await call(c, "update_work_params", {"work_id": "work-1", "uc_preset": 7})  # but it can't be set anew
        assert res["isError"] and "No UC preset 7" in res["content"][0]["text"]
    assert service.load_work(settings, "work-1").params == {"model": "nai-diffusion-4-5-curated", "uc_preset": 7,
                                                            "steps": 24}


async def test_a_work_made_outside_the_app_gets_a_gallery_to_arrange(settings):
    seed(settings)  # work-1 has no canvas at all — as the works agents made before
    async with mcp_client(settings) as c:
        made = await call_json(c, "create_work", {"title": "Fresh"})
        assert (await call_json(c, "get_gallery", {"work_id": made["id"]}))["blocks"] == []
        assert (await call_json(c, "get_gallery", {"work_id": "work-1"}))["quick_access"] == ["img-1"]
        await call_json(c, "edit_gallery", {"work_id": "work-1", "ops": [{"op": "add", "type": "grid", "image_ids": ["img-1"]}]})
    # just the gallery zone, unplaced — the app lays the other widgets out around it when it opens the work
    assert service.load_work(settings, made["id"]).canvas["nodes"] == [
        {"id": "gallery", "type": "zone", "data": {"role": "gallery", "blocks": []}}]
    [zone] = service.load_work(settings, "work-1").canvas["nodes"]
    assert (zone["id"], "position" in zone) == ("gallery", False)
    assert zone["data"]["blocks"][0]["imageIds"] == ["img-1"]


async def test_the_open_app_replays_agent_edits_instead_of_reloading(api, settings):
    seed(settings)
    base = service.load_work(settings, "work-1").updated_at
    async with mcp_client(settings) as c:
        made = await call_json(c, "create_combination", {"work_id": "work-1", "name": "Set", "blocks": [{"text": "a"}]})
        await call_json(c, "update_images", {"work_id": "work-1", "images": [{"id": "img-1", "favorite": False}]})
    stored = service.load_work(settings, "work-1").updated_at
    res = (await api.get("/api/agent/works/work-1/changes", params={"since": base})).json()
    assert res["at"] == stored
    assert [op["op"] for op in res["ops"]] == ["combo", "image"]
    assert res["ops"][0]["combo"]["id"] == made["id"] and res["ops"][0]["combo"]["created_at"]
    assert res["ops"][1] == {"op": "image", "id": "img-1", "set": {"favorite": False}}
    assert (await api.get("/api/agent/works/work-1/changes", params={"since": stored})).json() == {"at": stored, "ops": []}

    # the app saves (its own edit), then the agent edits again: from the app's new base only the agent's edit
    # replays; from the old base the chain is broken by a save that wasn't the agent's → a real conflict
    doc = service.load_work(settings, "work-1")
    doc.title = "Halloween night"
    app_base = service.save_work(settings, doc)["updated_at"]
    async with mcp_client(settings) as c:
        await call_json(c, "rename_work", {"work_id": "work-1", "title": "Halloween ✦"})
    res = (await api.get("/api/agent/works/work-1/changes", params={"since": app_base})).json()
    assert res["ops"] == [{"op": "title", "title": "Halloween ✦"}]
    assert (await api.get("/api/agent/works/work-1/changes", params={"since": base})).status_code == 409


async def test_an_app_save_landing_mid_edit_is_kept_and_the_edit_redone(settings, monkeypatch):
    seed(settings)
    real_save = service.save_work
    raced: list[bool] = []

    def save_after_the_app(s, doc, **kw):
        if not raced:  # the app's autosave lands between the agent's load and its save
            raced.append(True)
            mine = service.load_work(s, doc.id)
            mine.title = "Renamed in the app"
            real_save(s, mine)
        return real_save(s, doc, **kw)

    monkeypatch.setattr(service, "save_work", save_after_the_app)
    async with mcp_client(settings) as c:
        await call_json(c, "update_images", {"work_id": "work-1", "images": [{"id": "img-1", "tags": ["redone"]}]})
    doc = service.load_work(settings, "work-1")
    assert doc.title == "Renamed in the app" and doc.images[0].tags == ["redone"]


def test_two_saves_on_one_base_never_both_land(settings, monkeypatch):
    """The app and an agent save in parallel threads from the same copy: one lands, the other is told it's stale
    — never a silent last-writer-wins that drops the first one's new image (its file gc'd as an orphan)."""
    seed(settings)
    base = service.load_work(settings, "work-1")
    real_write = vault_store.write_work

    def slow_write(work_dir, doc):  # a save writing big new PNGs takes a while
        time.sleep(0.2)
        real_write(work_dir, doc)

    monkeypatch.setattr(vault_store, "write_work", slow_write)
    png = base64.b64encode(solid_png(8, 8)).decode("ascii")
    outcomes: dict[str, object] = {}

    def save(who: str) -> None:
        doc = base.model_copy(deep=True)
        if who == "app":
            doc.images.append(Image(id="img-new", image_b64=png))
        else:
            doc.title = "Agent's title"
        try:
            outcomes[who] = service.save_work(settings, doc)["updated_at"]
        except HTTPException as exc:
            outcomes[who] = exc.status_code

    threads = [threading.Thread(target=save, args=(who,)) for who in ("app", "agent")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(isinstance(v, str) for v in outcomes.values()) == [False, True]
    assert 409 in outcomes.values()
    stored = service.load_work(settings, "work-1")
    if isinstance(outcomes["app"], str):  # whichever landed is whole on disk
        assert "img-new" in [im.id for im in stored.images]
    else:
        assert stored.title == "Agent's title"
    # an edit of a work deleted meanwhile is refused, never a resurrection with its image files gone
    with pytest.raises(HTTPException) as gone:
        service.save_work(settings, WorkDoc(id="work-gone", updated_at=base.updated_at))
    assert gone.value.status_code == 404


# ---- generation ----
@pytest.fixture
def mock_novelai(settings, monkeypatch):
    """The offline NovelAI client (an Opus account), no pause between generations, a fresh subscription read — and
    agent access switched on in the stored config, as it is whenever the listener runs."""
    store.update_config(settings, {"enabled": True})
    monkeypatch.setattr(settings.novelai, "mock", True)
    monkeypatch.setattr(generation, "_GAP_S", 0.0)
    monkeypatch.setattr(generation, "_sub", None)
    monkeypatch.setattr(generation, "_not_before", 0.0)
    monkeypatch.setattr(generation, "_backoff", 0.0)
    return settings


def paid_allowed(settings: Settings, **limits: int) -> None:
    store.update_config(settings, {"spending": {"paid": True, "max_per_request": 50, "daily_budget": 200, **limits}})


async def gen(c: httpx.AsyncClient, **args) -> dict:
    return await call(c, "generate_images", {"work_id": "work-1", "blocks": [{"text": "raiden shogun"}], **args})


async def test_a_free_generation_lands_in_quick_access_with_its_recipe(mock_novelai):
    settings = mock_novelai
    seed(settings)
    base = service.load_work(settings, "work-1").updated_at
    with changes.subscribe() as notices:
        async with mcp_client(settings) as c:
            combo = await call_json(c, "create_combination", {"work_id": "work-1", "name": "Raiden", "blocks": [
                {"block_id": "block-raiden"}, {"text": "night sky", "frozen": True}, {"text": "blurry", "polarity": "negative"}]})
            out = await call_json(c, "generate_images", {"work_id": "work-1", "combination_id": combo["id"],
                                                         "width": 832, "height": 1216, "steps": 28})
        await notices.get()  # the combination
        notice = await asyncio.wait_for(notices.get(), 1)
    assert out["anlas"] == 0 and len(out["images"]) == 1
    doc = service.load_work(settings, "work-1")
    im = next(i for i in doc.images if i.id == out["images"][0]["id"])
    snap = next(s for s in doc.snapshots if s.id == im.snapshot_id)
    assert (im.role, im.ar, im.file) == ("gallery", 832 / 1216, f"images/{im.id}.png")
    assert (snap.assembled_positive, snap.assembled_negative) == ("raiden shogun", "blurry")  # frozen left out
    assert snap.params["seed"] == out["images"][0]["seed"] and snap.params["width"] == 832
    assert [c.text for c in snap.components] == ["raiden shogun", "blurry"]
    assert notice["message"] == "Agent generated 1 image — in “Halloween” from “Raiden” · free" and notice["cost"] == 0
    assert (activity.recent()[0]["cost"], activity.recent()[0]["kind"]) == (0, "generate")
    ops = changes.edits_since("work-1", base, doc.updated_at)
    assert ops[-1]["op"] == "images_added" and ops[-1]["images"][0]["id"] == im.id


async def test_images_say_which_combination_made_them(mock_novelai):
    settings = mock_novelai
    seed(settings)
    size = {"width": 832, "height": 1216, "steps": 28}
    async with mcp_client(settings) as c:
        night = await call_json(c, "create_combination", {"work_id": "work-1", "name": "Night", "blocks": [
            {"block_id": "block-raiden"}, {"text": "night sky"}, {"text": "draft idea", "frozen": True}]})
        made = await call_json(c, "generate_images", {"work_id": "work-1", "combination_id": night["id"], **size})
        # the same prompt blocks given by hand make that combination's image too
        by_hand = await call_json(c, "generate_images", {"work_id": "work-1", **size, "blocks": [
            {"block_id": "block-raiden"}, {"text": "night sky"}]})
        page = await call_json(c, "list_images", {"work_id": "work-1"})
        made_from = {im["id"]: im["combinations"] for im in page["items"]}
        mine = {made["images"][0]["id"], by_hand["images"][0]["id"]}
        assert {made_from[i][0]["id"] for i in mine} == {night["id"]} and made_from["img-1"] == []
        assert made_from[made["images"][0]["id"]] == [{"id": night["id"], "name": "Night"}]
        only = await call_json(c, "list_images", {"work_id": "work-1", "combination_id": night["id"]})
        assert {im["id"] for im in only["items"]} == mine
        # edited since, the combination no longer is what made them
        await call_json(c, "update_combination", {"work_id": "work-1", "combination_id": night["id"],
                                                  "blocks": [{"text": "dusk"}]})
        assert (await call_json(c, "list_images", {"work_id": "work-1", "combination_id": night["id"]}))["total"] == 0
        unknown = await call(c, "list_images", {"work_id": "work-1", "combination_id": "combo-x"})
        assert unknown["isError"] and "No combination" in unknown["content"][0]["text"]


async def test_a_preset_drives_the_generation_and_unique_tags_apply_as_at_the_station(mock_novelai):
    settings = mock_novelai
    seed(settings)
    doc = service.load_work(settings, "work-1")
    doc.params = {"model": "nai-diffusion-5-full", "steps": 20, "dedupe": False}
    service.save_work(settings, doc)
    presets.save_preset(settings, PresetDoc(id="preset-portrait", name="Portrait", params=PresetParams(
        width=832, height=1216, steps=23, quality_toggle=False, uc_preset=3, n_samples=4, dedupe=True)))
    twice = [{"text": "long hair, smile"}, {"text": "long hair"}]
    async with mcp_client(settings) as c:
        mine = next(p for p in await call_json(c, "list_presets") if p["id"] == "preset-portrait")
        assert (mine["name"], mine["builtin"], mine["params"]["steps"]) == ("Portrait", False, 23)
        est = await call_json(c, "estimate_cost", {"work_id": "work-1", "preset_id": "preset-portrait"})
        assert (est["params"]["steps"], est["params"]["n_samples"]) == (23, 1)  # a preset's image count isn't used
        await call_json(c, "generate_images", {"work_id": "work-1", "preset_id": "preset-portrait", "steps": 28,
                                               "blocks": twice})
        await call_json(c, "generate_images", {"work_id": "work-1", "blocks": [{"text": "a, a"}]})
        await call_json(c, "generate_images", {"work_id": "work-1", "blocks": [{"text": "a, a"}], "dedupe": True})
        assert "list_presets" in (await gen(c, preset_id="preset-nope"))["content"][0]["text"]
    by_preset, plain, merged = service.load_work(settings, "work-1").snapshots
    assert by_preset.assembled_positive == "1.5::long hair::, smile"  # the preset's "unique tags"
    assert {k: by_preset.params[k] for k in ("steps", "width", "quality_toggle", "uc_preset", "dedupe")} == {
        "steps": 28, "width": 832, "quality_toggle": False, "uc_preset": 3, "dedupe": True}  # the call over the preset
    assert (plain.assembled_positive, plain.params["dedupe"], plain.params["steps"]) == ("a, a", False, 20)  # the work's
    assert (merged.assembled_positive, merged.params["dedupe"]) == ("1.5::a::", True)  # the call's own choice


async def test_the_account_and_token_counts_are_readable(mock_novelai, monkeypatch):
    settings = mock_novelai
    seed(settings)
    reads: list[int] = []

    async def subscription(self):
        reads.append(1)
        return {"tier": 3, "tier_name": "Opus", "active": True, "anlas": 6821 - 10 * len(reads)}

    monkeypatch.setattr(MockNovelAIClient, "get_subscription", subscription)
    async with mcp_client(settings) as c:
        acct = await call_json(c, "get_account")
        assert acct["tier"] == 3 and acct["free_generations"] and acct["anlas"] == 6811
        assert (acct["agent_paid_generations"], acct["agent_spent_today"]) == (False, 0)
        assert (await call_json(c, "get_account"))["anlas"] == 6811 and len(reads) == 1  # served again, NovelAI spared
        await gen(c)  # reads the tier itself — and moves the balance
        assert (await call_json(c, "get_account"))["anlas"] == 6791 and len(reads) == 3  # read afresh after it
        bare = await call_json(c, "count_tokens", {"model": "nai-diffusion-4-5-full", "positive": "1girl, smile",
                                                   "quality_toggle": False})
        tagged = await call_json(c, "count_tokens", {"model": "nai-diffusion-4-5-full", "positive": "1girl, smile"})
        assert 0 < bare["positive"] < tagged["positive"]  # generation's quality tags count too
        twice = {"model": "nai-diffusion-4-5-full", "positive": "1girl, smile, smile, smile", "quality_toggle": False}
        merged = await call_json(c, "count_tokens", {**twice, "dedupe": True})
        assert merged["positive"] < (await call_json(c, "count_tokens", twice))["positive"]
        assert (bare["positive_limit"], bare["tokenizer"]) == (512, "t5")
        assert (await call(c, "count_tokens", {"model": "no-such-model"}))["isError"]


async def test_a_paid_generation_needs_the_users_go_ahead_and_fits_their_limits(mock_novelai):
    settings = mock_novelai
    seed(settings)
    two = {"work_id": "work-1", "blocks": [{"text": "raiden shogun"}], "width": 1024, "height": 1024, "steps": 28,
           "n_samples": 2}  # Opus: the first image free, the second ◆ 20
    async with mcp_client(settings) as c:
        async def refused(args: dict) -> str:
            res = await call(c, "generate_images", args)
            assert res["isError"], res
            return res["content"][0]["text"]

        assert "paid: true" in await refused(two)  # the user didn't ask for a paid one
        assert "Paid generations are off" in await refused({**two, "paid": True})
        store.update_config(settings, {"spending": {"paid": True, "max_per_request": 15}})
        assert "per-generation limit" in await refused({**two, "paid": True})
        store.update_config(settings, {"spending": {"max_per_request": 50, "daily_budget": 30}})
        out = await call_json(c, "generate_images", {**two, "paid": True})
        assert (out["anlas"], out["spent_today"], len(out["images"])) == (20, 20, 2)
        assert "today's budget" in await refused({**two, "paid": True})  # 20 more would pass 30
        estimate = await call_json(c, "estimate_cost", {"width": 1024, "height": 1024, "steps": 28, "n_samples": 2})
    assert estimate["anlas"] == 20 and estimate["spent_today"] == 20 and "today's budget" in estimate["refused_even_with_paid"]
    assert [(r["action"], r["outcome"]) for r in activity.recent()[1:3]] == [("Generate images", "denied"),
                                                                             ("Generated 2 images", "ok")]
    seeds = [s.params["seed"] for s in service.load_work(settings, "work-1").snapshots]
    assert seeds[1] == seeds[0] + 1  # sample k is seed + k


async def test_whether_a_generation_is_free_is_read_fresh_every_time(mock_novelai, monkeypatch):
    settings = mock_novelai
    seed(settings)
    async with mcp_client(settings) as c:
        assert (await call_json(c, "generate_images", {"work_id": "work-1", "blocks": [{"text": "a"}]}))["anlas"] == 0
        # the Opus subscription lapsed (or another account's token went in) since that generation
        tablet = {"tier": 1, "tier_name": "Tablet", "active": True, "anlas": 100}
        monkeypatch.setattr(MockNovelAIClient, "get_subscription", lambda self: _resolved(tablet))
        res = await gen(c)
    assert res["isError"] and "no free generations" in res["content"][0]["text"]
    assert store.spent_today(store.load_config(settings)) == 0


async def _resolved(value):
    return value


async def test_generations_are_one_at_a_time_and_follow_the_switches_live(mock_novelai):
    settings = mock_novelai
    seed(settings)
    async with mcp_client(settings) as c:  # the listener's copy of the config still allows generating
        generation._lock.acquire()  # another agent generation is running
        try:
            busy = await gen(c)
        finally:
            generation._lock.release()
        assert busy["isError"] and "Another agent generation is running" in busy["content"][0]["text"]
        store.update_config(settings, {"permissions": {"generate": False}})  # switched off as the call came in
        off = await gen(c)
    assert off["isError"] and "turned off" in off["content"][0]["text"]
    assert len(service.load_work(settings, "work-1").images) == 2


async def test_a_failed_generation_is_logged_and_counted_unless_novelai_refused_it(mock_novelai, monkeypatch):
    settings = mock_novelai
    seed(settings)
    paid_allowed(settings)
    two = {"width": 1024, "height": 1024, "steps": 28, "n_samples": 2, "paid": True}  # ◆ 20

    async def timeout(self, params):
        raise httpx.ReadTimeout("no answer")

    async def too_many(self, params):
        raise NovelAIRateLimitError("NovelAI returned 429: slow down")

    async with mcp_client(settings) as c:
        monkeypatch.setattr(MockNovelAIClient, "generate", timeout)
        lost = await gen(c, **two)
        assert lost["isError"] and "may still have been charged" in lost["content"][0]["text"]
        assert store.spent_today(store.load_config(settings)) == 20  # it may have been: it counts
        monkeypatch.setattr(MockNovelAIClient, "generate", too_many)
        assert (await gen(c, **two))["isError"]
        assert store.spent_today(store.load_config(settings)) == 20  # a clear refusal is given back
        backing_off = await gen(c, **two)
    assert "slow down" in backing_off["content"][0]["text"]  # after a 429 the next one waits its turn
    rows = activity.recent()
    assert [r["outcome"] for r in rows[:3]] == ["denied", "error", "error"]
    assert rows[2]["cost"] == 20 and rows[1]["cost"] is None


async def test_images_a_save_could_not_keep_go_to_downloads(mock_novelai, monkeypatch, tmp_path):
    settings = mock_novelai
    seed(settings)

    def deleted_meanwhile(*_a, **_kw):
        raise HTTPException(status_code=404, detail="Work not found.")

    monkeypatch.setattr(service, "save_work", deleted_meanwhile)
    async with mcp_client(settings) as c:
        res = await gen(c)
    text = res["content"][0]["text"]
    assert res["isError"] and "saved to" in text and "Work not found" in text
    assert [p.suffix for p in (tmp_path / "downloads").iterdir()] == [".png"]


def test_the_daily_budget_starts_over_each_day(settings, monkeypatch):
    class Day:
        today_is = "2026-10-24"

        @classmethod
        def today(cls):
            return type("D", (), {"isoformat": lambda _self: cls.today_is})()

    monkeypatch.setattr(store, "date", Day)
    assert store.add_spent(settings, 20) == 20
    Day.today_is = "2026-10-25"
    assert store.spent_today(store.load_config(settings)) == 0
    assert store.add_spent(settings, 5) == 5
    assert store.add_spent(settings, -9) == 0  # a refund never goes below zero


async def test_no_token_means_no_generation(settings):
    seed(settings)  # not in mock mode, and the (test) keychain holds no NovelAI token
    async with mcp_client(settings) as c:
        res = await call(c, "generate_images", {"work_id": "work-1", "blocks": [{"text": "raiden"}]})
    assert res["isError"] and "No NovelAI token" in res["content"][0]["text"]
    assert len(service.load_work(settings, "work-1").images) == 2  # nothing was added


async def test_notices_stream_to_the_app_until_it_goes():
    res = await agent_router.events()
    stream = res.body_iterator
    assert await stream.__anext__() == ": connected\n\n"
    changes.publish({"kind": "library", "work_id": None, "message": "Agent added 1 block — Library → Character"})
    frame = await asyncio.wait_for(stream.__anext__(), 1)
    assert json.loads(frame.removeprefix("data: "))["message"].startswith("Agent added 1 block")
    await stream.aclose()
    assert not changes._subscribers  # a closed stream stops listening


async def test_write_tools_say_what_they_are(settings):
    async with mcp_client(settings) as c:
        tools = {t["name"]: t["annotations"] for t in (await rpc(c, "tools/list"))["result"]["tools"]}
    assert tools["search_blocks"] == {"title": "Search Library blocks", "readOnlyHint": True}
    assert tools["create_blocks"]["readOnlyHint"] is False and tools["create_blocks"]["destructiveHint"] is False
    assert all(tools[n]["destructiveHint"] for n in ("delete_blocks", "delete_images", "delete_combination"))


# ---- Settings API + the real listener ----
@pytest.fixture
async def api(settings):
    app.dependency_overrides[get_settings] = lambda: settings
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as ac:
        yield ac
    app.dependency_overrides.clear()
    await listener.stop()


def _free_ports(n: int) -> list[int]:
    """Distinct free loopback ports for the listener to try (stands in for 8790… so tests never touch them)."""
    found: list[int] = []
    for port in range(23_450, 23_650):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        found.append(port)
        if len(found) == n:
            return found
    raise RuntimeError("no free ports")


@pytest.fixture
def ports(monkeypatch):
    """The listener's port range, redirected to free test ports."""
    chosen = _free_ports(3)
    monkeypatch.setattr(agent_server, "PORTS", chosen)
    monkeypatch.setattr(agent_router, "PORTS", chosen)
    return chosen


async def test_settings_api_starts_serves_and_stops_the_listener(api, ports):
    off = (await api.get("/api/agent")).json()
    assert off["running"] is False and off["config"] == AgentConfig().model_dump()
    assert off["token_hint"] is None and off["address"] == f"http://127.0.0.1:{ports[0]}/mcp"
    assert (await api.get("/api/agent/token")).status_code == 404  # off → nothing to hand out, nothing issued

    on = (await api.patch("/api/agent", json={"enabled": True})).json()
    assert on["running"] is True and on["error"] is None and on["note"] is None
    assert on["address"] == f"http://127.0.0.1:{ports[0]}/mcp"
    res = await api.get("/api/agent/token")
    assert res.headers["cache-control"] == "no-store"
    token = res.json()["token"]
    assert on["token_hint"] == token[-4:]
    assert (await api.get("/api/agent/token", headers={"Host": "evil.example"})).status_code == 403
    assert (await api.get("/api/agent/token", headers={"Sec-Fetch-Site": "cross-site"})).status_code == 403

    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{ports[0]}", headers={"Authorization": f"Bearer {token}"}) as agent:
        assert (await rpc(agent, "ping"))["result"] == {}
        # a regenerated token locks the old one out at once
        await api.post("/api/agent/token")
        assert (await agent.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})).status_code == 401

    off_again = (await api.patch("/api/agent", json={"enabled": False})).json()
    assert off_again["running"] is False


async def test_the_activity_log_can_be_cleared(api, settings):
    seed(settings)
    async with mcp_client(settings) as c:
        await call(c, "list_works", {})
    shown = (await api.get("/api/agent")).json()["activity"]
    assert [(r["action"], r["kind"], r["seq"]) for r in shown] == [("List works", "read", 1)]
    assert (await api.delete("/api/agent/activity")).json()["activity"] == []
    async with mcp_client(settings) as c:
        await call(c, "list_works", {})
    assert activity.recent()[0]["seq"] == 2  # counting on — nothing after a Clear looks older than it is


async def test_a_taken_port_moves_the_listener_to_the_next_one(api, ports):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", ports[0]))
        taken.listen()
        res = (await api.patch("/api/agent", json={"enabled": True})).json()
        assert res["running"] is True and res["address"] == f"http://127.0.0.1:{ports[1]}/mcp"
        assert str(ports[0]) in res["note"] and res["error"] is None
    # the port is no setting the user (or a client) can change
    assert (await api.patch("/api/agent", json={"port": 9001})).json()["address"].endswith(f":{ports[1]}/mcp")


async def test_no_free_port_is_reported_not_raised(api, ports, settings):
    held = []
    for p in ports:
        s = socket.socket()
        s.bind(("127.0.0.1", p))
        s.listen()
        held.append(s)
    try:
        res = (await api.patch("/api/agent", json={"enabled": True})).json()
        assert res["running"] is False and "No free port" in res["error"]
    finally:
        for s in held:
            s.close()


async def test_the_apps_own_port_is_never_taken(settings, ports, monkeypatch):
    monkeypatch.setattr(settings.api, "port", ports[0])
    await listener.apply(settings, AgentConfig(enabled=True))
    assert listener.running and listener.port == ports[1]
    await listener.stop()


async def test_overlapping_changes_never_leave_a_stray_listener(settings, ports):
    on, off = AgentConfig(enabled=True), AgentConfig(enabled=False)
    await asyncio.gather(*(listener.apply(settings, c) for c in (on, off, on, on, off, on)))
    assert listener.running
    await listener.stop()
    for p in ports:  # every port free again — nothing kept serving behind our back
        with socket.socket() as s:
            s.bind(("127.0.0.1", p))


async def test_agent_access_never_stops_the_app_from_starting(settings, ports, monkeypatch):
    store.update_config(settings, {"enabled": True})

    def broken(*_a):
        raise RuntimeError("logon session does not exist")  # what Windows' backend raises — not a KeyringError

    monkeypatch.setattr(keyring, "get_password", broken)
    monkeypatch.setattr(app_main, "get_settings", lambda: settings)
    async with app.router.lifespan_context(app):
        for _ in range(100):
            if listener.error:
                break
            await asyncio.sleep(0.01)
        assert not listener.running and "keychain" in listener.error
    assert not listener.running


def test_the_listener_leaves_the_main_servers_logging_alone(settings):
    error_log, access_log = logging.getLogger("uvicorn.error"), logging.getLogger("uvicorn.access")
    before = (error_log.level, list(access_log.handlers), access_log.propagate)
    _Config(build_app(settings, lambda: TOKEN, AgentConfig), log_config=None, access_log=False, log_level="warning")
    assert (error_log.level, list(access_log.handlers), access_log.propagate) == before


def test_agent_token_lives_in_the_keychain():
    first = keychain.ensure_agent_token()
    assert keychain.ensure_agent_token() == first
    assert keychain.agent_token_hint() == first[-4:]
    assert keychain.regenerate_agent_token() != first


def test_a_keychain_glitch_never_replaces_the_token(monkeypatch):
    first = keychain.ensure_agent_token()
    working = keyring.get_password  # the in-memory keychain (conftest)

    def broken(*_a):
        raise RuntimeError("keychain locked")

    monkeypatch.setattr(keyring, "get_password", broken)
    with pytest.raises(RuntimeError):
        keychain.ensure_agent_token()
    assert keychain.agent_token_hint() is None  # the status hint degrades instead
    monkeypatch.setattr(keyring, "get_password", working)
    assert keychain.read_agent_token() == first

