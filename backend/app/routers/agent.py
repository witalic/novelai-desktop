"""Settings → Agents: the MCP listener's config, live status, token and activity (``app.agent``) — and
what agents change, for the rest of the app: a stream of notices (an open work catches up on the edits themselves
through ``GET /api/vault/works/{id}/changes``).

The agent token is a local credential the user pastes into their agent's config. It is handed out in full only
by ``GET /token`` (the Copy button) — the token the running listener accepts, never a freshly issued one — and
only to a loopback-Host, same-origin request, whether or not the /api guard is active (it isn't in dev).
Otherwise it shows as a 4-character hint. Keychain and settings-file I/O run off the event loop.
"""
import asyncio
import json
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool

from app import keychain
from app.agent import activity, changes, store
from app.agent.config import AgentConfig
from app.agent.server import PORTS, listener
from app.settings import Settings, get_settings

router = APIRouter(prefix="/api/agent", tags=["agent"])
_LOOPBACK = {"127.0.0.1", "localhost"}
_KEEPALIVE_S = 15


class AgentActivity(BaseModel):
    seq: int  # rises with every new row or fold — what arrived since the user last looked
    at: str
    kind: activity.Kind
    action: str
    detail: str
    outcome: Literal["ok", "denied", "error"]
    count: int
    cost: int | None = None  # Anlas a generation spent (0: free); None for everything else


class AgentStatus(BaseModel):
    config: AgentConfig
    running: bool
    address: str  # where agents connect: the listener's port, or the usual one while it's off
    note: str | None  # e.g. the usual port was taken, so another one is in use
    error: str | None
    connected: int
    token_hint: str | None  # the token's last 4 characters, to recognise it without revealing it
    spent_today: int  # Anlas paid agent generations spent today (the daily budget's meter)
    activity: list[AgentActivity]


class AgentPatch(BaseModel):
    enabled: bool | None = None
    permissions: dict[str, bool] | None = None
    spending: dict[str, bool | int] | None = None


def _status(settings: Settings) -> AgentStatus:
    running = listener.running
    cfg = store.load_config(settings)
    # The app polls this while agents can act: the running listener holds the token, so the keychain (a real OS
    # call, maybe a prompt on macOS) is only asked while access is off.
    hint = listener.token[-4:] if listener.token else keychain.agent_token_hint()
    return AgentStatus(
        config=cfg, running=running, spent_today=store.spent_today(cfg),
        address=f"http://127.0.0.1:{listener.port if running else PORTS[0]}/mcp",
        note=listener.note if running else None, error=listener.error,
        connected=activity.connected() if running else 0,
        token_hint=hint, activity=[AgentActivity(**a) for a in activity.recent()],
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


@router.delete("/activity", response_model=AgentStatus)
def clear_activity(settings: Settings = Depends(get_settings)) -> AgentStatus:
    activity.clear()
    return _status(settings)


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


@router.get("/events")
async def events() -> StreamingResponse:
    """Agents' change notices as server-sent events, for as long as the app keeps the stream open. A comment line
    every few seconds keeps it alive (and lets a closed client be noticed)."""
    async def stream() -> AsyncIterator[str]:
        with changes.subscribe() as queue:
            yield ": connected\n\n"
            while True:
                try:
                    notice = await asyncio.wait_for(queue.get(), _KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(notice, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})
