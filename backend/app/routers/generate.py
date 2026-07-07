"""Image generation endpoints. Returns base64 PNG(s); the vault will later persist them to disk."""
import base64
import json
import logging
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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


@router.post("/generate/stream")
async def generate_stream(params: GenerateParams, settings: Settings = Depends(get_settings)) -> StreamingResponse:
    """Server-sent stream of the generation: intermediate previews then the final image.

    Each SSE data line is a JSON event: ``{type:'intermediate',step,mime,image}`` /
    ``{type:'final',mime,image}`` / ``{type:'error',message,status}`` (``image`` is raw base64).
    """
    client = get_client(settings)

    async def sse():
        try:
            async for event in client.generate_stream(params):
                yield f"data: {json.dumps(event)}\n\n"
        except NovelAIError as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc), 'status': exc.http_status or 502})}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


class DownloadRequest(BaseModel):
    images: list[str]  # raw base64-encoded PNGs


@router.post("/download")
async def download(req: DownloadRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Write image(s) straight to the Downloads folder (no OS save dialog); the UI just toasts the result."""
    dest = Path(settings.download_dir)
    dest.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    saved: list[str] = []
    for i, b64 in enumerate(req.images, start=1):
        try:
            data = base64.b64decode(b64, validate=True)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="invalid image data") from exc
        name = f"novelai-{stamp}-{i}.png"
        (dest / name).write_bytes(data)
        saved.append(name)
    log.info("Saved %d image(s) to %s", len(saved), dest)
    return {"count": len(saved), "dir": str(dest)}
