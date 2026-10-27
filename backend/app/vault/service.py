"""Vault orchestration: save/load/list works, serve images, gallery query, save blocks.

Files on disk are authoritative; the SQLite index (in this machine's cache) is opened per call and rebuilt on demand.
"""
import json
import logging
import sqlite3
import threading
import uuid
from contextlib import AbstractContextManager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException
from PIL import Image as PILImage

from app import appconfig
from app.settings import Settings
from app.vault import categories, gallery_layout, index, layout, manager, migrate, store
from app.vault import config as vaultcfg
from app.vault.models import (
    BlockDoc, BlocksPage, CategoryCount, CategoryDoc, GalleryAlbum, GalleryFacets, GalleryImage, GalleryItem,
    GalleryPage, ImageDetail, SaveCategory, TagCount, WorkCount, WorkDoc, WorkListItem, WorkMatch, WorksPage,
)

log = logging.getLogger(__name__)

# One work write at a time: the optimistic-lock check, the files, the thumbnail and the index row land together.
# Two writers exist — the app's saves and backend edits (workedit) — and both run in worker threads, so without
# it both could pass the check and the later write would drop the other's images (gc'd as orphans) or edits.
_works_lock = threading.Lock()
# Thumbnails made at once: a gallery scrolled fast asks for dozens — four at a time leave the CPU to the rest.
_thumbs = threading.BoundedSemaphore(4)


def _now() -> str:
    # Millisecond precision so two saves in the same second get distinct timestamps — the optimistic-lock
    # base comparison (H3) needs a strictly newer stored value to detect a concurrent edit.
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _stamp_after(stored: str | None) -> str:
    """A save's new ``updated_at``: now — or, for two saves in one millisecond, just past the stored one, so the lock
    base still moves on. A stored value that doesn't parse (a hand-edited work.json) is simply replaced."""
    now = _now()
    if not stored or now > stored:
        return now
    try:
        return (datetime.fromisoformat(stored) + timedelta(milliseconds=1)).isoformat(timespec="milliseconds")
    except (ValueError, OverflowError):
        return now


def _vault(settings: Settings) -> Path:
    # The active vault (the default, created on first run); a 503 while it can't be reached — waited at most 2 s for.
    return appconfig.active_vault(settings)


def _index(settings: Settings, vault: Path) -> AbstractContextManager[sqlite3.Connection]:
    # The vault's index for one request — kept in this machine's cache, never in the vault itself.
    return index.connection(vault, index.location(appconfig.cache_dir(settings), vault))


def _guard_no_move() -> None:
    # Any vault write landing between a move's copy and rmtree would silently vanish — refuse while moving.
    if manager.move_status().active:
        raise HTTPException(status_code=409, detail="A vault move is in progress — try again in a moment.")


def save_work(settings: Settings, doc: WorkDoc, adopt: dict[str, Path] | None = None) -> dict:
    """Write a work. One that comes with a base (``updated_at``: the stored copy it was loaded from) is an edit: if
    that work is gone meanwhile — deleted by the user or an agent, or the vault changed under it — it's a 404, never a
    resurrection whose image files are gone; the app keeps its unsaved changes as a new work then. (A copy that opted
    out of the base — the browser fallback's close beacon — saves as a new one would.) ``adopt``: image files copied in
    from other works (``store.write_work``) — a backend edit's, never a request's."""
    if not layout.valid_id(doc.id):
        raise HTTPException(status_code=400, detail="Invalid work id.")
    _guard_no_move()
    vault = _vault(settings)
    with _works_lock:
        return _save_work_locked(settings, vault, doc, adopt)


def _saved_at(work_dir: Path) -> str | None:
    """The stored work's ``updated_at``, read from its manifest: a save checks against the files, which are the truth —
    not the index, which changes made outside this app reach only at its next sync. None: no manifest there (any more);
    "" when it has no readable one (a hand-broken file, which the save writes over)."""
    try:
        raw = json.loads((work_dir / "work.json").read_text("utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:
        return ""
    stamp = raw.get("updated_at") if isinstance(raw, dict) else None
    return stamp if isinstance(stamp, str) else ""


def _save_work_locked(settings: Settings, vault: Path, doc: WorkDoc, adopt: dict[str, Path] | None) -> dict:
    with _index(settings, vault) as conn:
        # Full id (not a prefix) in the directory name — a short prefix can collide for two same-title works
        # created close together, and the second write_work would overwrite the first's work.json.
        existing_dir = index.find_work_dir(conn, doc.id)
        stored = _saved_at(layout.safe_join(vault / "works", existing_dir)) if existing_dir else None
        if doc.updated_at and stored is None:
            if existing_dir:  # its manifest went without this app — the index learns it now
                if not vaultcfg.is_initialized(vault):  # or the share dropped, which reads as missing: wait for it
                    raise appconfig.VaultUnavailable(str(vault))
                index.remove_work(conn, doc.id)
            raise HTTPException(status_code=404, detail="This work was deleted.")
        dir_name = existing_dir or f"{_now()[:10]}__{layout.slugify(doc.title)}__{doc.id}"
        work_dir = layout.safe_join(vault / "works", dir_name)
        doc.slug = layout.slugify(doc.title)
        # Optimistic concurrency (H3): if the stored copy is newer than the base the client loaded
        # (its incoming updated_at), another editor saved in between — refuse so it doesn't clobber.
        # A client that sends no base (empty) opts out (fresh works, best-effort close beacon).
        if stored and doc.updated_at and stored > doc.updated_at:
            raise HTTPException(status_code=409, detail="This work was changed elsewhere. Reload to get the latest.")
        doc.updated_at = _stamp_after(stored)
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
        for combo in doc.combos:
            if not combo.created_at:
                combo.created_at = doc.updated_at
            if not combo.updated_at:
                combo.updated_at = combo.created_at
        works_root = (vault / "works").resolve()
        if adopt and not all(p.resolve().is_relative_to(works_root) for p in adopt.values()):
            raise ValueError("A save copies in the vault's own image files only.")  # a bug, never a request's doing
        try:
            store.write_work(work_dir, doc, adopt)
        except ValueError as exc:  # bad base64 image data → client error, not a 500 (binascii.Error ⊂ ValueError)
            raise HTTPException(status_code=400, detail="Invalid image data.") from exc
        store.write_thumbnail(work_dir, doc)
        index.upsert_work(conn, doc, dir_name)
        return {"id": doc.id, "updated_at": doc.updated_at}


def load_work(settings: Settings, work_id: str) -> WorkDoc:
    if not layout.valid_id(work_id):
        raise HTTPException(status_code=400, detail="Invalid work id.")
    vault = _vault(settings)
    with _index(settings, vault) as conn:
        dir_name = index.find_work_dir(conn, work_id)
    if not dir_name:
        raise HTTPException(status_code=404, detail="Work not found.")
    work_dir = layout.safe_join(vault / "works", dir_name)
    if not (work_dir / "work.json").is_file():
        raise HTTPException(status_code=404, detail="Work not found.")
    return store.read_work(work_dir)


def delete_work(settings: Settings, work_id: str) -> dict:
    """Remove a work: its index rows and its on-disk folder (images, sidecars, preview, thumbnails).

    The manifest goes first: without it the folder is no work, not even to an index rebuild. So a file another program
    holds open (Explorer's Thumbs.db on a network share) can only leave litter behind — never a work that lost its
    images but comes back."""
    if not layout.valid_id(work_id):
        raise HTTPException(status_code=400, detail="Invalid work id.")
    _guard_no_move()
    vault = _vault(settings)
    with _works_lock:  # never under a save that is writing into the same folder
        with _index(settings, vault) as conn:
            dir_name = index.find_work_dir(conn, work_id)
            if not dir_name:
                raise HTTPException(status_code=404, detail="Work not found.")
            work_dir = layout.safe_join(vault / "works", dir_name)
            try:
                (work_dir / "work.json").unlink(missing_ok=True)
            except OSError as exc:  # nothing is gone yet
                raise HTTPException(status_code=409, detail="The work's files are in use by another program — "
                                                            "close it and try again.") from exc
            index.remove_work(conn, work_id)
        if work_dir.is_dir() and (held := layout.remove_tree(work_dir)):
            log.warning("Deleted %s, but %d file(s) in use by another program stay behind: %s",
                        work_dir.name, len(held), ", ".join(held[:5]))
    return {"deleted": work_id}


def list_works(settings: Settings, page: int, per_page: int, search: str | None = None, sort: str = "updated",
               direction: str = "desc", tags: tuple[str, ...] = ()) -> WorksPage:
    """Works whose title or contents match ``search`` (their gallery's words, their gallery images' prompts,
    descriptions and tags) and whose gallery images carry all ``tags``. A work the title didn't match says where it
    matched."""
    vault = _vault(settings)
    with _index(settings, vault) as conn:
        total, rows = index.list_works(conn, page, per_page, search, sort, direction, tags)
        in_vault = index.work_count(conn) if search or tags else total
        needle = search.casefold() if search else ""
        found = {r["id"]: _matched_words(r["texts"], needle) for r in rows
                 if needle and needle not in (r["title"] or "").casefold()}
        counts = index.matching_images(conn, [i for i, m in found.items() if m is None], search) if search else {}
    items = [
        WorkListItem(
            id=r["id"], title=r["title"] or "Untitled", updated_at=r["updated_at"] or "",
            image_count=r["image_count"] or 0,
            # Versioned by the cover: cached while it stays, fetched anew once it changes.
            preview_url=f"/api/vault/works/{r['id']}/preview?v={r['preview_image_id']}" if r["preview_image_id"] else None,
            match=found.get(r["id"]) or (WorkMatch(kind="images", count=counts[r["id"]]) if r["id"] in counts else None),
        )
        for r in rows
    ]
    return WorksPage(items=items, total=total, page=page, per_page=per_page, in_vault=in_vault)


def _matched_words(texts: str, needle: str) -> WorkMatch | None:
    for kind, text in json.loads(texts or "[]"):
        if needle in text.casefold():
            return WorkMatch(kind=kind, text=text)
    return None


def work_tags(settings: Settings, search: str | None, tags: tuple[str, ...]) -> list[TagCount]:
    with _index(settings, _vault(settings)) as conn:
        return [TagCount(name=n, count=c) for n, c in index.work_tag_counts(conn, search, tags)]


def image_path(settings: Settings, work_id: str, image_id: str) -> Path:
    if not (layout.valid_id(work_id) and layout.valid_id(image_id)):
        raise HTTPException(status_code=400, detail="Invalid id.")
    vault = _vault(settings)
    with _index(settings, vault) as conn:
        dir_name = index.find_work_dir(conn, work_id)
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
    def cached() -> bool:
        return dst.is_file() and dst.stat().st_mtime >= src.stat().st_mtime

    if cached():
        return dst
    with _thumbs:
        if cached():  # made by another request while this one waited
            return dst
        return _make_thumb(src, dst, width)


def _make_thumb(src: Path, dst: Path, width: int) -> Path:
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
    with _index(settings, vault) as conn:
        dir_name = index.find_work_dir(conn, work_id)
    if not dir_name:
        raise HTTPException(status_code=404, detail="Not found.")
    path = layout.safe_join(vault / "works", dir_name, "preview.png")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No preview.")
    return path


def _image_url(work_id: str, image_id: str) -> str:
    return f"/api/vault/works/{work_id}/images/{image_id}"


def gallery(settings: Settings, q: index.ImageQuery, per_page: int, *, page: int = 1,
            after: tuple[int, str] | None = None, oldest_first: bool = False) -> GalleryPage:
    """Gallery images across the vault — newest first unless ``oldest_first`` — as ``q`` filters them: the
    ``page``-th page, or the one after the ``after`` cursor (``index.gallery``)."""
    vault = _vault(settings)
    with _index(settings, vault) as conn:
        total, rows, more = index.gallery(conn, q, per_page, page=page, after=after, oldest_first=oldest_first)
    items = [
        GalleryImage(
            image_id=r["id"], work_id=r["work_id"], work_title=r["work_title"] or "Untitled",
            url=_image_url(r["work_id"], r["id"]), favorite=bool(r["favorite"]), created_at=r["created_at"] or "",
            ar=r["ar"], model=r["model"],
            album=GalleryAlbum(grid_id=r["grid_id"], number=r["grid_no"], heading=r["heading"], section=r["section"])
            if r["grid_id"] else None,
        )
        for r in rows
    ]
    return GalleryPage(items=items, total=total, page=page, per_page=per_page,
                       next=f"{more[0]}:{more[1]}" if more else None)


def gallery_facets(settings: Settings, q: index.ImageQuery) -> GalleryFacets:
    with _index(settings, _vault(settings)) as conn:
        f = index.gallery_facets(conn, q)
    return GalleryFacets(
        total=f["total"], unsorted=f["unsorted"],
        tags=[TagCount(name=n, count=c) for n, c in f["tags"]],
        models=[TagCount(name=n, count=c) for n, c in f["models"]],
        works=[WorkCount(id=i, title=t or "Untitled", count=c) for i, t, c in f["works"]],
    )


def gallery_image(settings: Settings, work_id: str, image_id: str) -> ImageDetail:
    """One gallery image with its recipe, from its work as stored."""
    doc = load_work(settings, work_id)
    im = next((im for im in doc.images if im.id == image_id and im.role == "gallery"), None)
    if im is None:
        raise HTTPException(status_code=404, detail="Image not found.")
    snap = next((s for s in doc.snapshots if s.id == im.snapshot_id), None) if im.snapshot_id else None
    where = gallery_layout.placements(doc).get(im.id)
    params = snap.params if snap else {}
    return ImageDetail(
        image_id=im.id, work_id=doc.id, work_title=doc.title or "Untitled", url=_image_url(doc.id, im.id),
        favorite=im.favorite, created_at=im.created_at, ar=im.ar or index.ratio(params), model=params.get("model"),
        album=GalleryAlbum(grid_id=where.grid_id, number=where.number, heading=where.heading, section=where.section)
        if where else None,
        description=im.description, tags=im.tags,
        block_tags=list(dict.fromkeys(t for c in snap.components if c.polarity != "negative" for t in c.tags
                                      if t not in im.tags)) if snap else [],
        positive=snap.assembled_positive if snap else "", negative=snap.assembled_negative if snap else "",
        components=snap.components if snap else [], params=params,
    )


def _blocks_root(vault: Path) -> Path:
    return vault / "library" / "blocks"


def blocks_root(settings: Settings) -> Path:
    """Public: the on-disk library blocks directory for the active vault (used by the importer)."""
    return _blocks_root(_vault(settings))


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
    with _index(settings, vault) as conn:
        index.upsert_block(conn, json.loads(block.model_dump_json()))
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
    with _index(settings, vault) as conn:
        index.delete_block(conn, block_id)
    return {"id": block_id, "deleted": True}


def list_blocks(settings: Settings, category_slugs: list[str], tags: list[str], search: str, sort: str,
                page: int, per_page: int) -> BlocksPage:
    vault = _vault(settings)
    with _index(settings, vault) as conn:
        # Section order follows the category order (one order everywhere), so pages arrive in display order.
        cat_order = [c.slug for c in categories.read_all(vault)] if sort == "category" else None
        total, rows = index.list_blocks(conn, category_slugs, tags, search or None, sort, page, per_page, cat_order)
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
    with _index(settings, vault) as conn:
        rows = index.blocks_by_ids(conn, ids)
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
    with _index(settings, vault) as conn:
        counts = index.category_counts(conn, tags or None)
    cats = categories.read_all(vault)
    known = {c.slug for c in cats}
    result = [
        CategoryCount(**c.model_dump(), count=counts.get(c.slug, 0), builtin=c.slug in categories.DEFAULT_SLUGS)
        for c in cats
    ]
    # Surface any category a block references but the categories doesn't know (fallback color).
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
    removed = categories.remove(vault, slug)
    if not removed and not moved:
        raise HTTPException(status_code=404, detail="Category not found.")
    with _index(settings, vault) as conn:
        for data in moved:  # targeted re-index of just the moved blocks (no full, crash-prone rebuild)
            index.upsert_block(conn, data)
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
        existing = {c.slug for c in categories.read_all(vault)} | categories.DEFAULT_SLUGS
        slug = _new_category_id(existing)  # opaque id, not derived from the (possibly non-Latin) name
    cat = CategoryDoc(slug=slug, name=body.name.strip(), color=body.color)
    categories.upsert(vault, cat)
    return cat


def default_categories() -> list[CategoryDoc]:
    """The built-in category set — drives the 'restore defaults' picker (shows which are missing)."""
    return [c.model_copy() for c in categories.DEFAULTS]


def restore_categories(settings: Settings, slugs: list[str]) -> dict:
    """Un-tombstone deleted built-in categories the user wants back."""
    _guard_no_move()
    return {"restored": categories.restore(_vault(settings), slugs)}


def reorder_categories(settings: Settings, slugs: list[str]) -> dict:
    """Persist a user-defined category order — one order shared by every category list."""
    _guard_no_move()
    categories.reorder(_vault(settings), slugs)
    return {"order": slugs}


def image_examples(settings: Settings, tags: list[str], limit: int) -> list[GalleryItem]:
    vault = _vault(settings)
    with _index(settings, vault) as conn:
        rows = index.examples_by_tags(conn, [t for t in tags if t.strip()], min(max(limit, 1), 24))
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
    with _index(settings, vault) as conn:
        rows = index.tag_counts(conn, category or None)
    return [TagCount(name=r["name"], count=r["count"]) for r in rows]
