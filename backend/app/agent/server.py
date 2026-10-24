"""The agent listener: a second, tiny HTTP server inside the sidecar, on a fixed loopback port, serving MCP's
Streamable HTTP transport at ``/mcp`` (plain JSON replies — no server-initiated streams). It shares the
event loop and the vault services with the main app, so the vault keeps a single writer.

Every request must carry a loopback ``Host`` (anti DNS-rebinding), no ``Origin`` (web pages can't drive it;
native agents send none), and the agent token as ``Authorization: Bearer …`` (constant-time compare). The
main app's /api guard plays no part — this endpoint authenticates in dev too.

The port is the app's business, not the user's: 8790 when it's free (a fixed address agents can be set up
with once), else the next free one in ``PORTS`` — the Settings card then shows the address in use and says so.

Agent access must never stop the app itself: whatever goes wrong starting the listener (keychain, no free
port) becomes ``error`` on the Settings card, not a failed boot.
"""
import asyncio
import contextlib
import json
import logging
import secrets
import socket
import uuid
from collections.abc import Callable, Iterator
from urllib.parse import urlsplit

import uvicorn
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app import keychain
from app.agent import activity, protocol
from app.agent.config import AgentConfig
from app.settings import Settings

log = logging.getLogger(__name__)

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
PORTS = range(8790, 8810)  # tried in order; the first is the address agents are normally set up with
_MAX_BODY = 1_000_000  # bytes — agent calls are small; refuse anything bigger
_MAX_BATCH = 16  # JSON-RPC batches (older MCP revisions only) are capped, never unbounded
_STOP_TIMEOUT_S = 5


def _loopback_host(host: str) -> bool:
    if "@" in host:
        return False
    try:
        return (urlsplit(f"//{host}").hostname or "") in _LOOPBACK
    except ValueError:
        return False


def build_app(settings: Settings, token: Callable[[], str | None], config: Callable[[], AgentConfig]) -> Starlette:
    async def mcp(request: Request) -> Response:
        if not _loopback_host(request.headers.get("host", "")):
            return JSONResponse({"error": "Bad host."}, status_code=400)
        if "origin" in request.headers:
            return JSONResponse({"error": "Browser requests are not accepted."}, status_code=403)
        expected = token()
        auth = request.headers.get("authorization", "")
        given = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        if not expected or not secrets.compare_digest(given.encode(), expected.encode()):
            return JSONResponse({"error": "Unauthorized."}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
        version = request.headers.get("mcp-protocol-version")
        if version is not None and version not in protocol.PROTOCOL_VERSIONS:
            return JSONResponse({"error": f"Unsupported MCP protocol version: {version}"}, status_code=400)

        session_id = request.headers.get("mcp-session-id", "")
        if request.method == "DELETE":  # the client ends its session
            activity.end_session(session_id)
            return Response(status_code=204)
        if request.method == "GET":  # no server-initiated stream on offer
            return Response(status_code=405, headers={"Allow": "POST, DELETE"})

        if int(request.headers.get("content-length") or 0) > _MAX_BODY:
            return JSONResponse({"error": "Request too large."}, status_code=413)
        body = await request.body()
        if len(body) > _MAX_BODY:  # chunked bodies carry no Content-Length
            return JSONResponse({"error": "Request too large."}, status_code=413)
        try:
            payload = json.loads(body)
        except (ValueError, RecursionError):
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error."}},
                                status_code=400)
        batch = isinstance(payload, list)
        messages = payload if batch else [payload]
        if not messages or len(messages) > _MAX_BATCH:
            return JSONResponse(protocol.invalid_request(f"A batch holds 1–{_MAX_BATCH} messages."), status_code=400)

        headers = {}
        if any(isinstance(m, dict) and m.get("method") == "initialize" for m in messages):
            session_id = uuid.uuid4().hex
            headers["Mcp-Session-Id"] = session_id
        if session_id:
            activity.touch_session(session_id)

        ctx = protocol.Context(settings=settings, config=config())
        replies = [r for r in [await protocol.handle(m, ctx) for m in messages] if r is not None]
        if not replies:
            return Response(status_code=202, headers=headers)
        return JSONResponse(replies if batch else replies[0], headers=headers)

    return Starlette(routes=[Route("/mcp", mcp, methods=["GET", "POST", "DELETE"])])


class _Config(uvicorn.Config):
    """uvicorn configures process-wide logging when a Config is built — the main server's. Leave it alone."""

    def configure_logging(self) -> None:
        pass


class _Server(uvicorn.Server):
    """The main server owns the process signals (Ctrl+C, SIGTERM); this one is stopped by its owner."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class AgentListener:
    def __init__(self) -> None:
        self.config = AgentConfig()
        self.port: int | None = None
        self.error: str | None = None
        self.note: str | None = None  # set when the listener had to take a port other than the usual one
        self._token: str | None = None
        self._server: _Server | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()  # one apply/stop at a time — overlapping ones would orphan a server

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def token(self) -> str | None:
        """The token the running listener accepts (what the Copy button hands out)."""
        return self._token if self.running else None

    def set_token(self, token: str) -> None:
        """A regenerated token takes effect at once and disconnects every agent."""
        self._token = token
        activity.forget_sessions()

    async def apply(self, settings: Settings, config: AgentConfig) -> None:
        """Bring the listener in line with ``config``: start it or stop it. Never raises — a failure to start is
        reported through ``error``."""
        async with self._lock:
            self.config = config
            if not config.enabled:
                await self._stop()
                self.error = self.note = None
                return
            if self.running:
                return  # only permissions changed — they're read per call from self.config
            self.error = await self._start(settings)

    async def _start(self, settings: Settings) -> str | None:
        try:
            self._token = await run_in_threadpool(keychain.ensure_agent_token)  # the keychain may block
        except Exception as exc:  # noqa: BLE001 — any keychain backend failure, not just KeyringError
            log.warning("Agent access: no agent token from the keychain: %s", exc)
            return "The OS keychain is unavailable, so no agent token can be stored."
        port, sock = None, None
        for p in PORTS:
            # The app's own port is skipped — binding it first would stop the app's server from starting.
            if p != settings.api.port and (sock := _bind(p)) is not None:
                port = p
                break
        if port is None or sock is None:
            log.warning("Agent access: no free port in %d–%d", PORTS[0], PORTS[-1])
            return f"No free port between {PORTS[0]} and {PORTS[-1]} — something else is using them all."
        self.note = None if port == PORTS[0] else (
            f"Port {PORTS[0]} is taken by another program, so agents connect on {port} for now — "
            "if your agent is set up with the old address, copy the command again.")
        app = build_app(settings, lambda: self._token, lambda: self.config)
        self._server = _Server(_Config(app, lifespan="off", log_config=None, access_log=False, proxy_headers=False,
                                       server_header=False, timeout_graceful_shutdown=2))
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]))
        self._task.add_done_callback(lambda t: self._on_exit(t, sock))
        self.port = port
        log.info("Agent access (MCP) listening on http://127.0.0.1:%d/mcp", port)
        return None

    def _on_exit(self, task: asyncio.Task, sock: socket.socket) -> None:
        sock.close()
        if task.cancelled() or self._server is None:
            return
        if (exc := task.exception()) is not None or not self._server.should_exit:
            log.error("Agent access (MCP) listener stopped unexpectedly", exc_info=exc)
            self.error = "The agent listener stopped unexpectedly — see the app log."

    async def stop(self) -> None:
        async with self._lock:
            await self._stop()

    async def _stop(self) -> None:
        server, task = self._server, self._task
        self._server = self._task = None
        self.port = None
        activity.forget_sessions()
        if server is not None and task is not None:
            server.should_exit = True
            try:
                await asyncio.wait_for(asyncio.shield(task), _STOP_TIMEOUT_S)
            except asyncio.TimeoutError:
                task.cancel()  # a stalled client must not hold up a port change or the app's shutdown
            except Exception:  # noqa: BLE001 — already logged by _on_exit
                pass
            log.info("Agent access (MCP) stopped")


def _bind(port: int) -> socket.socket | None:
    """A listening-ready loopback socket on ``port``, or None if it can't be had. Bound here (not by uvicorn) so
    a busy port means "try the next", not uvicorn's sys.exit. On Windows the bind is exclusive, so no other
    process can bind the same port alongside us and intercept agents' requests."""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)  # POSIX: rebind across TIME_WAIT on restart
        sock.bind(("127.0.0.1", port))
        return sock
    except OSError as exc:
        if sock is not None:
            sock.close()
        log.info("Agent access: port %d unavailable (%s) — trying the next", port, exc)
        return None


listener = AgentListener()
