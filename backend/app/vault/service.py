"""Vault orchestration: save/load/list works, serve images, gallery query, save blocks.

Files on disk are authoritative; the SQLite index is opened per call and rebuilt on demand.
"""
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException
from PIL import Image as PILImage

from app import appconfig
from app.settings import Settings
from app.vault import catalog, index, layout, manager, migrate, store
from app.vault.models import (
    BlockDoc, BlocksPage, CategoryCount, CategoryDoc, GalleryItem, GalleryPage,
    SaveCategory, TagCount, WorkDoc, WorkListItem, WorksPage,
)

log = logging.getLogger(__name__)


def _now() -> str:
    # Millisecond precision so two saves in the same second get distinct timestamps — the optimistic-lock
    # base comparison (H3) needs a strictly newer stored value to detect a concurrent edit.
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _vault(settings: Settings) -> Path:
    # Never blocks: resolves the active vault, creating + registering the default on first run.
    return appconfig.active_vault(settings)


def _guard_no_move() -> None:
    # Any vault write landing between a move's copy and rmtree would silently vanish — refuse while moving.
    if manager.move_status().active:
        raise HTTPException(status_code=409, detail="A vault move is in progress — try again in a moment.")


def save_work(settings: Settings, doc: WorkDoc) -> dict:
    if not layout.valid_id(doc.id):
        raise HTTPException(status_code=400, detail="Invalid work id.")
    _guard_no_move()
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        # Full id (not a prefix) in the directory name — a short prefix can collide for two same-title works
        # created close together, and the second write_work would overwrite the first's work.json.
        existing_dir = index.find_work_dir(conn, doc.id)
        dir_name = existing_dir or f"{_now()[:10]}__{layout.slugify(doc.title)}__{doc.id}"
        work_dir = layout.safe_join(vault / "works", dir_name)
        doc.slug = layout.slugify(doc.title)
        # Optimistic concurrency (H3): if the stored copy is newer than the base the client loaded
        # (its incoming updated_at), another editor saved in between — refuse so it doesn't clobber.
        # A client that sends no base (empty) opts out (fresh works, best-effort close beacon).
        if existing_dir and doc.updated_at:
            stored = index.work_updated_at(conn, doc.id)
            if stored and stored > doc.updated_at:
                raise HTTPException(status_code=409, detail="This work was changed elsewhere. Reload to get the latest.")
        doc.updated_at = _now()
        # Server owns the version; the client always sends current-shape data (M1). Refuse a doc claiming a
        # newer schema than this build understands rather than silently downgrading it (data-loss).
        if doc.schema_version and doc.schema_version > migrate.CURRENT:
            raise HTTPException(status_code=400, detail=f"schema_version {doc.schema_version} is newer than this build supports ({migrate.CURRENT}).")
        doc.schema_version = migrate.CURRENT
        if not doc.created_at:
            doc.created_at = doc.updated_at
        # Stamp creation time on images/snapshots that don't have one yet (kept stable across re-saves).
        for snap in doc.snapshots:
            if not snap.created_at:
                snap.created_at = doc.updated_at
        for im in doc.images:
            if not layout.valid_id(im.id):  # ids become filenames — reject traversal / junk before writing
                raise HTTPException(status_code=400, detail="Invalid image id.")
            if not im.created_at:
                im.created_at = doc.updated_at
        for st in doc.stack:
            if not layout.valid_id(st.id):
                raise HTTPException(status_code=400, detail="Invalid image id.")
            if not st.created_at:
                st.created_at = doc.updated_at
        try:
            store.write_work(work_dir, doc)
        except ValueError as exc:  # bad base64 image data → client error, not a 500 (binascii.Error ⊂ ValueError)
            raise HTTPException(status_code=400, detail="Invalid image data.") from exc
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


def delete_work(settings: Settings, work_id: str) -> dict:
    """Remove a work: its index rows and its on-disk folder (images, sidecars, preview, thumbnails)."""
    if not layout.valid_id(work_id):
        raise HTTPException(status_code=400, detail="Invalid work id.")
    _guard_no_move()
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        dir_name = index.find_work_dir(conn, work_id)
        if not dir_name:
            raise HTTPException(status_code=404, detail="Work not found.")
        index.remove_work(conn, work_id)
    finally:
        conn.close()
    work_dir = layout.safe_join(vault / "works", dir_name)
    if work_dir.is_dir():
        shutil.rmtree(work_dir)
    return {"deleted": work_id}


def list_works(settings: Settings, page: int, per_page: int,
               search: str | None = None, sort: str = "updated", direction: str = "desc") -> WorksPage:
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        total, rows = index.list_works(conn, page, per_page, search, sort, direction)
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


def image_thumb_path(settings: Settings, work_id: str, image_id: str, width: int) -> Path:
    """Return a width-capped PNG derivative of an image, cached on disk under the work's ``.thumbs/``.
    A right-sized thumbnail keeps small displays (e.g. the library grid) crisp without the client
    decoding the full-resolution source — decoding many full images at once pressures Chromium's
    image-decode budget, which makes it downsample cached bitmaps (the pixelation we hit)."""
    src = image_path(settings, work_id, image_id)  # validates ids + existence
    width = max(16, min(width, 4096))
    dst = src.parent.parent / ".thumbs" / f"{image_id}@{width}.png"
    if dst.is_file() and dst.stat().st_mtime >= src.stat().st_mtime:
        return dst
    with PILImage.open(src) as im:
        if im.width <= width:
            return src  # never upscale — the source is already at or below the requested width
        dst.parent.mkdir(exist_ok=True)
        rgb = im if im.mode in ("RGB", "RGBA") else im.convert("RGBA")
        # Atomic write into the cache: concurrent requests (and a FileResponse reader) must never see a
        # half-written PNG. Write a unique temp then os.replace into place.
        tmp = dst.with_suffix(f".{uuid.uuid4().hex}.tmp")
        rgb.resize((width, round(im.height * width / im.width)), PILImage.LANCZOS).save(tmp, "PNG")
    tmp.replace(dst)
    return dst


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


def _blocks_root(vault: Path) -> Path:
    return vault / "library" / "blocks"


def save_block(settings: Settings, block: BlockDoc) -> dict:
    if not layout.valid_id(block.id):
        raise HTTPException(status_code=400, detail="Invalid block id.")
    _guard_no_move()
    if not block.text.strip():
        raise HTTPException(status_code=400, detail="Block text is required.")
    vault = _vault(settings)
    block.category = layout.slugify(block.category) or "custom"
    block.updated_at = _now()
    # Version is server-owned: a content-changing update bumps it, an identical re-save doesn't.
    # Snapshot block refs (block_id + version) rely on this to tell which revision a frozen copy
    # came from. The previous copy is read before the stale-copy sweep (the category — and thus
    # the folder — may have changed since last save).
    root = _blocks_root(vault)
    prev: dict | None = None
    if root.is_dir():
        for old in root.glob(f"**/{block.id}.json"):
            if prev is None:
                try:
                    prev = json.loads(old.read_text("utf-8"))
                except (OSError, ValueError):
                    prev = None  # unreadable old copy → treat as a fresh save
            old.unlink(missing_ok=True)
    if prev:
        changed = any(getattr(block, f) != prev.get(f) for f in ("category", "name", "text", "polarity", "tags"))
        block.version = int(prev.get("version") or 1) + (1 if changed else 0)
        if not block.created_at:
            block.created_at = prev.get("created_at") or block.updated_at
    else:
        block.version = 1
        if not block.created_at:
            block.created_at = block.updated_at
    path = layout.safe_join(root, block.category, f"{block.id}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    layout.atomic_write_text(path, block.model_dump_json())
    conn = index.open_index(vault)
    try:
        index.upsert_block(conn, json.loads(block.model_dump_json()))
    finally:
        conn.close()
    return {"id": block.id}


def delete_block(settings: Settings, block_id: str) -> dict:
    if not layout.valid_id(block_id):
        raise HTTPException(status_code=400, detail="Invalid block id.")
    _guard_no_move()
    vault = _vault(settings)
    root = _blocks_root(vault)
    removed = False
    if root.is_dir():
        for bj in root.glob(f"**/{block_id}.json"):
            bj.unlink(missing_ok=True)
            removed = True
    if not removed:
        raise HTTPException(status_code=404, detail="Block not found.")
    conn = index.open_index(vault)
    try:
        index.delete_block(conn, block_id)
    finally:
        conn.close()
    return {"id": block_id, "deleted": True}


def list_blocks(settings: Settings, categories: list[str], tags: list[str], search: str, sort: str,
                page: int, per_page: int) -> BlocksPage:
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        # Section order follows the category order (one order everywhere), so pages arrive in display order.
        cat_order = [c.slug for c in catalog.read_all(vault)] if sort == "category" else None
        total, rows = index.list_blocks(conn, categories, tags, search or None, sort, page, per_page, cat_order)
    finally:
        conn.close()
    items = [
        BlockDoc(
            id=r["id"], category=r["category"] or "custom", name=r["name"] or "", text=r["text"] or "",
            polarity=r["polarity"] or "positive", version=r["version"] or 1,
            created_at=r["created_at"] or "", updated_at=r["updated_at"] or "",
            tags=r["tags"].split(chr(31)) if r["tags"] else [],
        )
        for r in rows
    ]
    return BlocksPage(items=items, total=total, page=page, per_page=per_page)


def resolve_blocks(settings: Settings, ids: list[str]) -> list[BlockDoc]:
    """Current BlockDocs for the given ids (missing ids are simply omitted) — the widget diffs a
    pinned copy's frozen version against the live one to flag drift."""
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        rows = index.blocks_by_ids(conn, ids)
    finally:
        conn.close()
    return [
        BlockDoc(
            id=r["id"], category=r["category"] or "custom", name=r["name"] or "", text=r["text"] or "",
            polarity=r["polarity"] or "positive", version=r["version"] or 1,
            created_at=r["created_at"] or "", updated_at=r["updated_at"] or "",
            tags=r["tags"].split(chr(31)) if r["tags"] else [],
        )
        for r in rows
    ]


def list_categories(settings: Settings, tags: list[str] | None = None) -> list[CategoryCount]:
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        counts = index.category_counts(conn, tags or None)
    finally:
        conn.close()
    cats = catalog.read_all(vault)
    known = {c.slug for c in cats}
    result = [
        CategoryCount(**c.model_dump(), count=counts.get(c.slug, 0), builtin=c.slug in catalog.DEFAULT_SLUGS)
        for c in cats
    ]
    # Surface any category a block references but the catalog doesn't know (fallback color).
    for slug, cnt in counts.items():
        if slug not in known:
            result.append(CategoryCount(slug=slug, name=slug.replace("-", " ").title(), color="#738496", count=cnt))
    return result


def _reassign_blocks_to_custom(vault: Path, slug: str) -> list[dict]:
    """Move every block file in a category's folder into ``custom/`` (retag); return the moved docs."""
    root = _blocks_root(vault)
    src = layout.safe_join(root, slug)
    if not src.is_dir():
        return []
    dst_dir = layout.safe_join(root, "custom")
    dst_dir.mkdir(parents=True, exist_ok=True)
    moved: list[dict] = []
    for bj in src.glob("*.json"):
        try:
            data = json.loads(bj.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        data["category"] = "custom"
        data["updated_at"] = _now()
        layout.atomic_write_text(dst_dir / bj.name, json.dumps(data, ensure_ascii=False))
        bj.unlink(missing_ok=True)
        moved.append(data)
    try:
        src.rmdir()
    except OSError:  # not empty / already gone — harmless
        pass
    return moved


def delete_category(settings: Settings, slug: str) -> dict:
    # "custom" is the reassignment sink, so it can't itself be deleted; every other category can —
    # its blocks move to Custom rather than blocking the delete.
    _guard_no_move()
    if slug == "custom":
        raise HTTPException(status_code=400, detail="The Custom category is the fallback and can't be deleted.")
    vault = _vault(settings)
    moved = _reassign_blocks_to_custom(vault, slug)
    removed = catalog.remove(vault, slug)
    if not removed and not moved:
        raise HTTPException(status_code=404, detail="Category not found.")
    conn = index.open_index(vault)
    try:
        for data in moved:  # targeted re-index of just the moved blocks (no full, crash-prone rebuild)
            index.upsert_block(conn, data)
    finally:
        conn.close()
    return {"slug": slug, "deleted": True, "moved": len(moved)}


def _new_category_id(existing: set[str]) -> str:
    for _ in range(50):
        cid = f"cat-{uuid.uuid4()}"
        if cid not in existing:
            return cid
    return f"cat-{uuid.uuid4()}"


def save_category(settings: Settings, body: SaveCategory) -> CategoryDoc:
    _guard_no_move()
    vault = _vault(settings)
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Category name is required.")
    if body.slug:
        slug = body.slug  # update (rename/recolor) — the id is stable, independent of the name
    else:
        existing = {c.slug for c in catalog.read_all(vault)} | catalog.DEFAULT_SLUGS
        slug = _new_category_id(existing)  # opaque id, not derived from the (possibly non-Latin) name
    cat = CategoryDoc(slug=slug, name=body.name.strip(), color=body.color)
    catalog.upsert(vault, cat)
    return cat


def default_categories() -> list[CategoryDoc]:
    """The built-in category set — drives the 'restore defaults' picker (shows which are missing)."""
    return [c.model_copy() for c in catalog.DEFAULTS]


def restore_categories(settings: Settings, slugs: list[str]) -> dict:
    """Un-tombstone deleted built-in categories the user wants back."""
    _guard_no_move()
    return {"restored": catalog.restore(_vault(settings), slugs)}


def reorder_categories(settings: Settings, slugs: list[str]) -> dict:
    """Persist a user-defined category order — one order shared by every category list."""
    _guard_no_move()
    catalog.reorder(_vault(settings), slugs)
    return {"order": slugs}


def image_examples(settings: Settings, tags: list[str], limit: int) -> list[GalleryItem]:
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        rows = index.examples_by_tags(conn, [t for t in tags if t.strip()], min(max(limit, 1), 24))
    finally:
        conn.close()
    return [
        GalleryItem(
            image_id=r["id"], work_id=r["work_id"],
            url=f"/api/vault/works/{r['work_id']}/images/{r['id']}",
            favorite=bool(r["favorite"]), group=r["group_name"], created_at=r["created_at"] or "",
        )
        for r in rows
    ]


def list_tags(settings: Settings, category: str) -> list[TagCount]:
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        rows = index.tag_counts(conn, category or None)
    finally:
        conn.close()
    return [TagCount(name=r["name"], count=r["count"]) for r in rows]


def reindex(settings: Settings) -> dict:
    vault = _vault(settings)
    conn = index.open_index(vault)
    try:
        return index.rebuild(conn, vault)
    finally:
        conn.close()
