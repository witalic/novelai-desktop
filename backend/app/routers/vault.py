"""Vault endpoints: choose the folder, and store/read works, images, blocks + the global gallery."""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from app.settings import Settings, get_settings
from app.vault import config as vaultcfg
from app.vault import service
from app.vault.models import (
    BlockDoc, GalleryPage, SetVaultConfig, VaultConfig, WorkDoc, WorksPage,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vault", tags=["vault"])


@router.get("/config", response_model=VaultConfig)
async def get_config(settings: Settings = Depends(get_settings)) -> VaultConfig:
    active = vaultcfg.active_vault_dir(settings)
    return VaultConfig(
        vault_dir=str(active) if active else None,
        initialized=bool(active and vaultcfg.is_initialized(active)),
        writable=bool(active and vaultcfg.writable(active)),
        proposed_default=str(vaultcfg.proposed_default()),
    )


@router.put("/config", response_model=VaultConfig)
async def set_config(body: SetVaultConfig, settings: Settings = Depends(get_settings)) -> VaultConfig:
    target = Path(body.vault_dir).expanduser()
    error = vaultcfg.validate_dir(target)
    if error:
        raise HTTPException(status_code=400, detail=error)
    vaultcfg.init_vault(target)
    vaultcfg.write_pointer(settings, target)
    return VaultConfig(
        vault_dir=str(target),
        initialized=True,
        writable=True,
        proposed_default=str(vaultcfg.proposed_default()),
    )


@router.put("/works")
async def save_work(doc: WorkDoc, settings: Settings = Depends(get_settings)) -> dict:
    return service.save_work(settings, doc)


@router.get("/works", response_model=WorksPage)
async def list_works(
    page: int = Query(1, ge=1), per_page: int = Query(24, ge=1, le=100),
    settings: Settings = Depends(get_settings),
) -> WorksPage:
    return service.list_works(settings, page, per_page)


@router.get("/works/{work_id}", response_model=WorkDoc)
async def load_work(work_id: str, settings: Settings = Depends(get_settings)) -> WorkDoc:
    return service.load_work(settings, work_id)


@router.get("/works/{work_id}/images/{image_id}")
async def get_image(work_id: str, image_id: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    return FileResponse(service.image_path(settings, work_id, image_id), media_type="image/png")


@router.get("/works/{work_id}/preview")
async def get_preview(work_id: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    return FileResponse(service.preview_path(settings, work_id), media_type="image/png")


@router.get("/gallery", response_model=GalleryPage)
async def gallery(
    tags: list[str] = Query(default=[]), favorite: bool = False,
    page: int = Query(1, ge=1), per_page: int = Query(48, ge=1, le=200),
    settings: Settings = Depends(get_settings),
) -> GalleryPage:
    return service.gallery(settings, tags, favorite, page, per_page)


@router.post("/library/blocks")
async def save_block(block: BlockDoc, settings: Settings = Depends(get_settings)) -> dict:
    return service.save_block(settings, block)


@router.post("/index/rebuild")
async def rebuild_index(settings: Settings = Depends(get_settings)) -> dict:
    return service.reindex(settings)
