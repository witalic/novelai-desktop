"""Settings → Agent access: the MCP listener's config, live status, token and activity (``app.agent``).

The agent token is a local credential the user pastes into their agent's config. It is handed out in full only
by ``GET /token`` (the Copy button) — the token the running listener accepts, never a freshly issued one — and
only to a loopback-Host, same-origin request, whether or not the /api guard is active (it isn't in dev).
Otherwise it shows as a 4-character hint. Keychain and settings-file I/O run off the event loop.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from app import keychain
from app.agent import activity, store
from app.agent.config import AgentConfig
from app.agent.server import PORTS, listener
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/agent", tags=["agent"])
_LOOPBACK = {"127.0.0.1", "localhost"}


class AgentActivity(BaseModel):
    at: str
    action: str
    detail: str
    outcome: Literal["ok", "denied", "error"]
    count: int


class AgentStatus(BaseModel):
    config: AgentConfig
    running: bool
    address: str  # where agents connect: the listener's port, or the usual one while it's off
    note: str | None  # e.g. the usual port was taken, so another one is in use
    error: str | None
    connected: int
    token_hint: str | None  # the token's last 4 characters, to recognise it without revealing it
    activity: list[AgentActivity]


class AgentPatch(BaseModel):
    enabled: bool | None = None
    permissions: dict[str, bool] | None = None


def _status(settings: Settings) -> AgentStatus:
    running = listener.running
    return AgentStatus(
        config=store.load_config(settings), running=running,
        address=f"http://127.0.0.1:{listener.port if running else PORTS[0]}/mcp",
        note=listener.note if running else None, error=listener.error,
        connected=activity.connected() if running else 0,
        token_hint=keychain.agent_token_hint(), activity=[AgentActivity(**a) for a in activity.recent()],
    )


@router.get("", response_model=AgentStatus)
def get_agent(settings: Settings = Depends(get_settings)) -> AgentStatus:
    return _status(settings)


@router.patch("", response_model=AgentStatus)
async def patch_agent(body: AgentPatch, settings: Settings = Depends(get_settings)) -> AgentStatus:
    try:
        cfg = await run_in_threadpool(store.update_config, settings, body.model_dump(exclude_none=True))
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=exc.errors()[0]["msg"]) from exc
    await listener.apply(settings, cfg)
    return await run_in_threadpool(_status, settings)


@router.get("/token")
def reveal_token(request: Request) -> JSONResponse:
    host = (request.headers.get("host") or "").split(":")[0]
    if host not in _LOOPBACK or request.headers.get("sec-fetch-site", "same-origin") != "same-origin":
        raise HTTPException(status_code=403, detail="Forbidden.")
    token = listener.token
    if not token:
        raise HTTPException(status_code=404, detail="Agent access is off — switch it on first.")
    return JSONResponse({"token": token}, headers={"Cache-Control": "no-store"})


@router.post("/token", response_model=AgentStatus)
def regenerate_token(settings: Settings = Depends(get_settings)) -> AgentStatus:
    try:
        listener.set_token(keychain.regenerate_agent_token())
    except Exception as exc:  # noqa: BLE001 — any keychain backend failure
        raise HTTPException(status_code=503, detail="The OS keychain is unavailable.") from exc
    return _status(settings)
