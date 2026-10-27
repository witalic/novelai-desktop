"""FastAPI application: the sidecar Electron spawns.

Serves the API and the built frontend single-origin at ``/app/`` (README: один origin, no CORS).
"""
import asyncio
import logging
import secrets
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app import __version__
from app.agent import store as agent_store
from app.agent.server import listener as agent_listener
from app.routers import account, agent, catalog, generate, settings, system, tokenize, vault
from app.settings import Settings, get_settings
from app.vault import manager as vault_manager

log = logging.getLogger(__name__)
_LOOPBACK = {"127.0.0.1", "localhost"}

# Frozen (PyInstaller) builds bundle the built frontend under sys._MEIPASS (the .spec adds it); dev serves
# it straight from frontend/dist relative to the source tree.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _DIST = Path(sys._MEIPASS) / "frontend" / "dist"
else:
    _DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

_PLACEHOLDER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>novelai-desktop</title></head>
<body style="font-family: sans-serif; padding: 2rem">
<h1>Frontend not built yet</h1>
<p>The Vue bundle will be served here from <code>frontend/dist</code>.
Backend is running — try <a href="/health">/health</a>.</p>
</body></html>"""


async def _start_agent_access(app_settings: Settings) -> None:
    """Agent access (MCP) runs its own loopback listener while the app is up — if the user switched it on.
    Whatever goes wrong here shows on the Settings card; it must never stop the app from starting."""
    try:
        await agent_listener.apply(app_settings, await run_in_threadpool(agent_store.load_config, app_settings))
    except Exception:  # noqa: BLE001
        log.exception("Agent access could not start")
        agent_listener.error = "Agent access could not start — see the app log."


async def _sweep_index_cache(app_settings: Settings) -> None:
    """The cached indexes of vaults gone from the list — once per start; never a reason not to start."""
    try:
        await run_in_threadpool(vault_manager.sweep_index_cache, app_settings)
    except Exception:  # noqa: BLE001
        log.exception("Could not sweep the vault index cache")


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # In the background: a slow keychain must not hold up the boot (the shell waits on /health).
    starting = asyncio.create_task(_start_agent_access(get_settings()))
    sweeping = asyncio.create_task(_sweep_index_cache(get_settings()))
    yield
    await starting
    await sweeping
    await agent_listener.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="novelai-desktop backend", version=__version__, lifespan=_lifespan)
    # Set once from the (env-provisioned) settings; tests may override app.state.auth_token to exercise it.
    app.state.auth_token = get_settings().api.auth_token

    # Single guard, active ONLY when the shell provisioned a token (prod): reject non-loopback Host headers
    # (anti DNS-rebinding) and require the shared secret on /api. In dev/tests (no token) it is a pass-through.
    @app.middleware("http")
    async def _guard(request: Request, call_next):  # noqa: ANN001, ANN202
        token = request.app.state.auth_token
        if token:
            host = (request.headers.get("host") or "").split(":")[0]
            if host not in _LOOPBACK:
                return JSONResponse({"detail": "Bad host."}, status_code=400)
            if request.url.path.startswith("/api") and not secrets.compare_digest(request.cookies.get("nai_auth") or "", token):
                return JSONResponse({"detail": "Unauthorized."}, status_code=403)
        return await call_next(request)

    app.include_router(system.router)
    app.include_router(generate.router)
    app.include_router(account.router)
    app.include_router(catalog.router)
    app.include_router(tokenize.router)
    app.include_router(vault.router)
    app.include_router(settings.router)
    app.include_router(agent.router)

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)

    # Single-origin static frontend, registered LAST so it can't shadow API routes.
    if _DIST.is_dir():
        app.mount("/app", StaticFiles(directory=str(_DIST), html=True), name="app")
    else:

        @app.get("/app", include_in_schema=False)
        @app.get("/app/{_path:path}", include_in_schema=False)
        async def frontend_placeholder(_path: str = "") -> HTMLResponse:
            return HTMLResponse(_PLACEHOLDER)

    return app


app = create_app()
