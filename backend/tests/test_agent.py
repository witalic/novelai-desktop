"""Agent access (MCP): config persistence, the listener's auth gate, the JSON-RPC surface, read tools and
per-call permissions, and that agent access can never stop the app from starting. Offline: the MCP app is
driven through ASGITransport; a few tests bind real loopback ports to prove the listener starts, serves,
moves and stops."""
import asyncio
import base64
import json
import logging
import socket
from collections import deque

import httpx
import keyring
import pytest

from app import appconfig, keychain
from app import main as app_main
from app.agent import activity, store
from app.agent import server as agent_server
from app.agent.config import AgentConfig, coerce
from app.agent.server import _Config, build_app, listener
from app.routers import agent as agent_router
from app.main import app
from app.novelai._png import solid_png
from app.settings import Settings, VaultSettings, get_settings
from app.vault import service
from app.vault.models import BlockDoc, Image, WorkDoc

TOKEN = "test-agent-token"
BASE = "http://127.0.0.1:8790"


@pytest.fixture
def settings(tmp_path):
    return Settings(vault=VaultSettings(dir=str(tmp_path / "vault"), state_dir=str(tmp_path / "state")), _env_file=None)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    monkeypatch.setattr(activity, "_entries", deque(maxlen=activity.MAX_ENTRIES))
    monkeypatch.setattr(activity, "_sessions", {})
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
def test_off_by_default_reads_allowed():
    cfg = AgentConfig()
    assert not cfg.enabled and cfg.permissions.read


def test_coerce_keeps_good_fields_and_drops_bad_ones():
    out = coerce({"enabled": True, "port": 9001, "permissions": "junk"})
    assert out == {"enabled": True, "permissions": {"read": True}}  # the port is no setting — an old one is dropped
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


async def test_get_work_lists_gallery_images_and_shows_one(settings):
    seed(settings)
    async with mcp_client(settings) as c:
        works = await call_json(c, "list_works")
        assert works["items"][0]["title"] == "Halloween"
        work = await call_json(c, "get_work", {"work_id": "work-1"})
        assert [im["id"] for im in work["images"]] == ["img-1"]  # the scratch image isn't listed
        assert work["images"][0] == {"id": "img-1", "favorite": True, "tags": ["vampire"], "description": "first",
                                     "group": None, "created_at": work["images"][0]["created_at"],
                                     "prompt": None, "params": None}
        assert "canvas" not in work
        image = await call(c, "get_image", {"work_id": "work-1", "image_id": "img-1", "width": 64})
        block = image["content"][0]
        assert block["type"] == "image" and block["mimeType"] == "image/png"
        assert base64.b64decode(block["data"])[:8] == b"\x89PNG\r\n\x1a\n"


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
    assert off["running"] is False and off["config"] == {"enabled": False, "permissions": {"read": True}}
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

