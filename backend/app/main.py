"""FastAPI application: the sidecar Electron spawns.

Serves the API and the built frontend single-origin at ``/app/`` (README: один origin, no CORS).
"""
import secrets
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.routers import account, catalog, generate, settings, system, tokenize, vault
from app.settings import get_settings

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


def create_app() -> FastAPI:
    app = FastAPI(title="novelai-desktop backend", version=__version__)
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
