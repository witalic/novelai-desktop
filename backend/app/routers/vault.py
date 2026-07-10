"""Vault endpoints: manage vault folders (list/add/switch/delete/move) + store/read works,
images, blocks and the global gallery.

Handlers are plain ``def`` (not ``async``): the service layer does blocking SQLite/Pillow/file I/O, so
Starlette runs them in its threadpool instead of stalling the event loop (which serves ``/health`` and the
generation SSE). Only genuinely-async endpoints (``routers/generate.py``, httpx) stay ``async def``."""
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse

from app.settings import Settings, get_settings
from app.vault import manager, service
from app.vault.models import (
    BlockDoc, BlocksPage, CategoryCount, CategoryDoc, GalleryItem, GalleryPage, MoveStatus, MoveVault,
    SaveCategory, TagCount, VaultConfig, VaultPath, WorkDoc, WorksPage,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/vault", tags=["vault"])


@router.get("/config", response_model=VaultConfig)
def get_config(settings: Settings = Depends(get_settings)) -> VaultConfig:
    return manager.get_config(settings)


@router.post("/vaults", response_model=VaultConfig)
def add_vault(body: VaultPath, settings: Settings = Depends(get_settings)) -> VaultConfig:
    return manager.add_vault(settings, body.dir)


@router.put("/active", response_model=VaultConfig)
def set_active(body: VaultPath, settings: Settings = Depends(get_settings)) -> VaultConfig:
    return manager.set_active(settings, body.dir)


@router.delete("/vaults", response_model=VaultConfig)
def delete_vault(dir: str = Query(...), settings: Settings = Depends(get_settings)) -> VaultConfig:
    return manager.delete_vault(settings, dir)


@router.post("/move", response_model=MoveStatus)
def move_vault(body: MoveVault, settings: Settings = Depends(get_settings)) -> MoveStatus:
    return manager.start_move(settings, body.src, body.dst)


@router.get("/move/status", response_model=MoveStatus)
def move_status() -> MoveStatus:
    return manager.move_status()


@router.put("/works")
@router.post("/works")  # POST alias so navigator.sendBeacon can flush on window close
def save_work(doc: WorkDoc, settings: Settings = Depends(get_settings)) -> dict:
    return service.save_work(settings, doc)


@router.get("/works", response_model=WorksPage)
def list_works(
    page: int = Query(1, ge=1), per_page: int = Query(24, ge=1, le=100),
    settings: Settings = Depends(get_settings),
) -> WorksPage:
    return service.list_works(settings, page, per_page)


@router.get("/works/{work_id}", response_model=WorkDoc)
def load_work(work_id: str, settings: Settings = Depends(get_settings)) -> WorkDoc:
    return service.load_work(settings, work_id)


@router.delete("/works/{work_id}")
def delete_work(work_id: str, settings: Settings = Depends(get_settings)) -> dict:
    return service.delete_work(settings, work_id)


@router.get("/works/{work_id}/images/{image_id}")
def get_image(
    work_id: str, image_id: str,
    w: int | None = Query(None, ge=16, le=4096, description="cap the width → cached thumbnail"),
    settings: Settings = Depends(get_settings),
) -> FileResponse:
    path = service.image_thumb_path(settings, work_id, image_id, w) if w else service.image_path(settings, work_id, image_id)
    return FileResponse(path, media_type="image/png")


@router.get("/works/{work_id}/preview")
def get_preview(work_id: str, settings: Settings = Depends(get_settings)) -> FileResponse:
    return FileResponse(service.preview_path(settings, work_id), media_type="image/png")


@router.get("/gallery", response_model=GalleryPage)
def gallery(
    tags: list[str] = Query(default=[]), favorite: bool = False,
    page: int = Query(1, ge=1), per_page: int = Query(48, ge=1, le=200),
    settings: Settings = Depends(get_settings),
) -> GalleryPage:
    return service.gallery(settings, tags, favorite, page, per_page)


@router.get("/library/blocks", response_model=BlocksPage)
def list_blocks(
    category: str = "", tags: list[str] = Query(default=[]), search: str = "",
    page: int = Query(1, ge=1), per_page: int = Query(48, ge=1, le=200),
    settings: Settings = Depends(get_settings),
) -> BlocksPage:
    return service.list_blocks(settings, category, tags, search, page, per_page)


@router.post("/library/blocks")
def save_block(block: BlockDoc, settings: Settings = Depends(get_settings)) -> dict:
    return service.save_block(settings, block)


@router.delete("/library/blocks/{block_id}")
def delete_block(block_id: str, settings: Settings = Depends(get_settings)) -> dict:
    return service.delete_block(settings, block_id)


@router.get("/library/categories", response_model=list[CategoryCount])
def list_categories(
    tags: list[str] = Query(default=[]), settings: Settings = Depends(get_settings),
) -> list[CategoryCount]:
    return service.list_categories(settings, tags)


@router.post("/library/categories", response_model=CategoryDoc)
def save_category(body: SaveCategory, settings: Settings = Depends(get_settings)) -> CategoryDoc:
    return service.save_category(settings, body)


@router.delete("/library/categories/{slug}")
def delete_category(slug: str, settings: Settings = Depends(get_settings)) -> dict:
    return service.delete_category(settings, slug)


@router.get("/library/tags", response_model=list[TagCount])
def list_tags(category: str = "", settings: Settings = Depends(get_settings)) -> list[TagCount]:
    return service.list_tags(settings, category)


@router.get("/library/examples", response_model=list[GalleryItem])
def block_examples(
    tags: list[str] = Query(default=[]), limit: int = Query(8, ge=1, le=24),
    settings: Settings = Depends(get_settings),
) -> list[GalleryItem]:
    return service.image_examples(settings, tags, limit)


@router.post("/index/rebuild")
def rebuild_index(settings: Settings = Depends(get_settings)) -> dict:
    return service.reindex(settings)
