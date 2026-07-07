"""FastAPI application: the sidecar Electron spawns.

Serves the API and the built frontend single-origin at ``/app/`` (README: один origin, no CORS).
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.routers import generate, system, vault

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
    app.include_router(system.router)
    app.include_router(generate.router)
    app.include_router(vault.router)

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
