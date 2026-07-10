"""Image generation endpoints. Returns base64 PNG(s); the vault will later persist them to disk."""
import base64
import json
import logging
import secrets
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import appconfig
from app.novelai import GenerateParams, get_client
from app.novelai.errors import NovelAIError
from app.settings import Settings, get_settings

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["generate"])


def _with_seed(params: GenerateParams) -> GenerateParams:
    """Resolve a random seed up front so the actually-used seed can be surfaced — otherwise a saved image
    can't be reproduced. A caller-supplied seed is kept as-is."""
    if params.seed is not None:
        return params
    return params.model_copy(update={"seed": secrets.randbelow(2**32)})


@router.post("/generate")
async def generate(params: GenerateParams, settings: Settings = Depends(get_settings)) -> dict:
    client = get_client(settings)
    params = _with_seed(params)
    try:
        images = await client.generate(params)
    except NovelAIError as exc:
        raise HTTPException(status_code=exc.http_status or 502, detail=str(exc)) from exc
    except httpx.HTTPError as exc:  # timeout / connect / read against an unofficial upstream → clean 502
        raise HTTPException(status_code=502, detail=f"NovelAI request failed: {exc}") from exc
    return {
        "mock": client.is_mock,
        "count": len(images),
        "seed": params.seed,
        "images": [base64.b64encode(img).decode("ascii") for img in images],
    }


@router.post("/generate/stream")
async def generate_stream(params: GenerateParams, settings: Settings = Depends(get_settings)) -> StreamingResponse:
    """Server-sent stream of the generation: intermediate previews then the final image.

    Each SSE data line is a JSON event: ``{type:'intermediate',step,mime,image}`` /
    ``{type:'final',mime,image,seed}`` / ``{type:'error',message,status}`` (``image`` is raw base64).
    The final event carries the resolved ``seed`` so the client can persist a reproducible snapshot.
    """
    client = get_client(settings)
    params = _with_seed(params)

    async def sse():
        try:
            async for event in client.generate_stream(params):
                if event.get("type") == "final":
                    event = {**event, "seed": params.seed}
                yield f"data: {json.dumps(event)}\n\n"
        except NovelAIError as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc), 'status': exc.http_status or 502})}\n\n"
        except httpx.HTTPError as exc:  # never drop the SSE silently — the UI spinner would hang forever
            yield f"data: {json.dumps({'type': 'error', 'message': f'NovelAI request failed: {exc}', 'status': 502})}\n\n"
        except Exception:  # noqa: BLE001 — any other failure must still surface as an error event, not a dead stream
            log.exception("Generation stream failed")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Generation failed.', 'status': 500})}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


class DownloadRequest(BaseModel):
    images: list[str]  # raw base64-encoded PNGs


@router.post("/download")
def download(req: DownloadRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Write image(s) straight to the Downloads folder (no OS save dialog); the UI just toasts the result.

    Plain ``def`` — the base64 decode + file writes are blocking, so Starlette runs it in its threadpool."""
    dest = Path(appconfig.load(settings)["download_dir"])
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
