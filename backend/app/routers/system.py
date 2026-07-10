"""System endpoints: the liveness probe the Electron shell polls before loading the UI."""
import hashlib

from fastapi import APIRouter, Request

from app import __version__

router = APIRouter(tags=["system"])


@router.get("/health")
async def health(request: Request) -> dict:
    """Liveness probe (never raises). ``sig`` is sha256 of the per-launch auth token when one is set, so the
    shell can confirm it reached the sidecar it spawned — not a foreign process that grabbed the port."""
    token = request.app.state.auth_token
    sig = hashlib.sha256(token.encode()).hexdigest() if token else None
    return {"status": "ok", "version": __version__, "sig": sig}
