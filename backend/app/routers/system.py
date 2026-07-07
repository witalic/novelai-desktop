"""System endpoints: the liveness probe the Electron shell polls before loading the UI."""
from fastapi import APIRouter

from app import __version__

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Never raises."""
    return {"status": "ok", "version": __version__}
