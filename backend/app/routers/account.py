"""NovelAI account endpoints — subscription tier + remaining Anlas (drives the cost/balance UI)."""
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.novelai import get_client
from app.novelai.errors import NovelAIError
from app.settings import Settings, get_settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/account", tags=["account"])


class Subscription(BaseModel):
    tier: int
    tier_name: str
    active: bool
    anlas: int


@router.get("/subscription", response_model=Subscription)
async def subscription(settings: Settings = Depends(get_settings)) -> dict:
    client = await run_in_threadpool(get_client, settings)  # get_client reads the keychain (blocking)
    try:
        return await client.get_subscription()
    except NovelAIError as exc:
        raise HTTPException(status_code=exc.http_status or 502, detail=str(exc)) from exc
    except httpx.HTTPError as exc:  # timeout / connect against an unofficial upstream → clean 502
        raise HTTPException(status_code=502, detail=f"NovelAI request failed: {exc}") from exc
