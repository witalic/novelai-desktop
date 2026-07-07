"""Vault orchestration: save/load/list works, serve images, gallery query, save blocks.

Files on disk are authoritative; the SQLite index is opened per call and rebuilt on demand.
"""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.settings import Settings
from app.vault import config, index, layout, store
from app.vault.models import (
    BlockDoc, GalleryItem, GalleryPage, WorkDoc, WorkListItem, WorksPage,
)

log = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _vault(settings: Settings) -> Path:
    vault = config.active_vault_dir(settings)
    if not vault or not config.is_initialized(vault):
        raise HTTPException(status_code=409, detail="No vault selected. Choose a folder in Settings.")
    return vault


def save_work(settings: Settings, doc: WorkDoc) -> dict:
    if not layout.valid_id(doc.id):
        raise HTTPException(status_code=400, detail="Invalid work id.")
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        dir_name = index.find_work_dir(conn, doc.id) or f"{_now()[:10]}__{layout.slugify(doc.title)}__{doc.id[:8]}"
        work_dir = layout.safe_join(vault / "works", dir_name)
        doc.slug = layout.slugify(doc.title)
        doc.updated_at = _now()
        if not doc.created_at:
            doc.created_at = doc.updated_at
        store.write_work(work_dir, doc)
        store.write_thumbnail(work_dir, doc)
        index.upsert_work(conn, doc, dir_name)
        return {"id": doc.id, "updated_at": doc.updated_at}
    finally:
        conn.close()


def load_work(settings: Settings, work_id: str) -> WorkDoc:
    if not layout.valid_id(work_id):
        raise HTTPException(status_code=400, detail="Invalid work id.")
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        dir_name = index.find_work_dir(conn, work_id)
    finally:
        conn.close()
    if not dir_name:
        raise HTTPException(status_code=404, detail="Work not found.")
    work_dir = layout.safe_join(vault / "works", dir_name)
    if not (work_dir / "work.json").is_file():
        raise HTTPException(status_code=404, detail="Work not found.")
    return store.read_work(work_dir)


def list_works(settings: Settings, page: int, per_page: int) -> WorksPage:
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        total, rows = index.list_works(conn, page, per_page)
    finally:
        conn.close()
    items = [
        WorkListItem(
            id=r["id"], title=r["title"] or "Untitled", updated_at=r["updated_at"] or "",
            image_count=r["image_count"] or 0,
            preview_url=f"/api/vault/works/{r['id']}/preview" if r["preview_image_id"] else None,
        )
        for r in rows
    ]
    return WorksPage(items=items, total=total, page=page, per_page=per_page)


def image_path(settings: Settings, work_id: str, image_id: str) -> Path:
    if not (layout.valid_id(work_id) and layout.valid_id(image_id)):
        raise HTTPException(status_code=400, detail="Invalid id.")
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        dir_name = index.find_work_dir(conn, work_id)
    finally:
        conn.close()
    if not dir_name:
        raise HTTPException(status_code=404, detail="Not found.")
    path = layout.safe_join(vault / "works", dir_name, "images", f"{image_id}.png")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found.")
    return path


def preview_path(settings: Settings, work_id: str) -> Path:
    if not layout.valid_id(work_id):
        raise HTTPException(status_code=400, detail="Invalid work id.")
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        dir_name = index.find_work_dir(conn, work_id)
    finally:
        conn.close()
    if not dir_name:
        raise HTTPException(status_code=404, detail="Not found.")
    path = layout.safe_join(vault / "works", dir_name, "preview.png")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No preview.")
    return path


def gallery(settings: Settings, tags: list[str], favorite: bool, page: int, per_page: int) -> GalleryPage:
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        total, rows = index.gallery(conn, tags, favorite, page, per_page)
    finally:
        conn.close()
    items = [
        GalleryItem(
            image_id=r["id"], work_id=r["work_id"],
            url=f"/api/vault/works/{r['work_id']}/images/{r['id']}",
            favorite=bool(r["favorite"]), group=r["group_name"], created_at=r["created_at"] or "",
        )
        for r in rows
    ]
    return GalleryPage(items=items, total=total, page=page, per_page=per_page)


def save_block(settings: Settings, block: BlockDoc) -> dict:
    if not layout.valid_id(block.id):
        raise HTTPException(status_code=400, detail="Invalid block id.")
    vault = _vault(settings)
    block.updated_at = _now()
    if not block.created_at:
        block.created_at = block.updated_at
    category = layout.slugify(block.category) or "custom"
    path = layout.safe_join(vault / "library" / "blocks", category, f"{block.id}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(block.model_dump_json(), "utf-8")
    conn = index.open_index(vault)
    try:
        index.upsert_block(conn, json.loads(block.model_dump_json()))
    finally:
        conn.close()
    return {"id": block.id}


def reindex(settings: Settings) -> dict:
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        return index.rebuild(conn, vault)
    finally:
        conn.close()
