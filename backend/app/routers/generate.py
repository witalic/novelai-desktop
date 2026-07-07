"""Image generation endpoint. Returns base64 PNG(s); the vault will later persist them to disk."""
import base64
import logging

from fastapi import APIRouter, Depends, HTTPException

from app.novelai import GenerateParams, get_client
from app.novelai.errors import NovelAIError
from app.settings import Settings, get_settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["generate"])


@router.post("/generate")
async def generate(params: GenerateParams, settings: Settings = Depends(get_settings)) -> dict:
    client = get_client(settings)
    try:
        images = await client.generate(params)
    except NovelAIError as exc:
        raise HTTPException(status_code=exc.http_status or 502, detail=str(exc)) from exc
    return {
        "mock": client.is_mock,
        "count": len(images),
        "images": [base64.b64encode(img).decode("ascii") for img in images],
    }
