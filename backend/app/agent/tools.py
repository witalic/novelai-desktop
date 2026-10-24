"""The MCP tools agents can call. Each one wraps the same vault/catalog services the UI uses, declares the
permission (``config.AgentPermissions`` field) it needs, and validates its own arguments. Handlers are
sync (vault I/O) — the protocol layer runs them in a worker thread.

Results are plain JSON-able values; ``ImageResult`` becomes an MCP image block, and a write returns ``Written``
— its result plus what it did, for the activity feed and the app's notice (``changes``). A ``ToolError`` is the
agent's mistake (bad argument, missing work, …) and is reported back as a tool error, not a crash.
"""
import base64
import json
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from app.agent import changes
from app.novelai.catalog import get_catalog
from app.settings import Settings
from app.vault import import_blocks, service
from app.vault.models import BlockDoc, Combo, ComboBlock, Image, WorkDoc

MAX_PAGE = 200
MAX_BATCH = 100  # blocks per create/delete call, blocks per combination
_MAX_TEXT = 4000
_MAX_NAME = 200
_MAX_TAGS = 50


class ToolError(Exception):
    """The call can't be done as asked — the message goes back to the agent."""


@dataclass(frozen=True)
class ImageResult:
    png: bytes


@dataclass(frozen=True)
class Written:
    """A write's result for the agent, plus what it did: ``action`` (e.g. "Added 3 blocks") and ``detail``
    (what it touched) make the activity row and the app's notice; ``work_id`` is the work it changed (None: the
    Library)."""

    result: Any
    action: str
    detail: str
    work_id: str | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    schema: dict
    permission: str  # an AgentPermissions field
    handler: Callable[[Settings, dict], Any]


# ---- argument helpers (the schema documents the shape; these enforce it) ----
def _str(args: dict, key: str, *, required: bool = False, max_len: int = 500) -> str:
    v = args.get(key)
    if v is not None and not isinstance(v, str):
        raise ToolError(f"'{key}' must be a string.")
    v = (v or "").strip()
    if not v:
        if required:
            raise ToolError(f"'{key}' is required.")
        return ""
    if len(v) > max_len:
        raise ToolError(f"'{key}' is too long (max {max_len} characters).")
    return v


def _int(args: dict, key: str, default: int, lo: int, hi: int) -> int:
    v = args.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int):
        raise ToolError(f"'{key}' must be an integer.")
    if not lo <= v <= hi:
        raise ToolError(f"'{key}' must be between {lo} and {hi}.")
    return v


def _strs(args: dict, key: str, *, max_items: int = MAX_PAGE) -> list[str]:
    v = args.get(key) or []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ToolError(f"'{key}' must be a list of strings.")
    if len(v) > max_items:
        raise ToolError(f"'{key}' takes at most {max_items} items.")
    return [x.strip() for x in v if x.strip()]


def _items(args: dict, key: str, max_items: int) -> list[dict]:
    v = args.get(key)
    if not isinstance(v, list) or not v or not all(isinstance(x, dict) for x in v):
        raise ToolError(f"'{key}' must be a non-empty list of objects.")
    if len(v) > max_items:
        raise ToolError(f"'{key}' takes at most {max_items} items.")
    return v


def _tags(args: dict, key: str = "tags") -> list[str]:
    v = args.get(key) or []
    if not isinstance(v, list) or not all(isinstance(t, str) for t in v):
        raise ToolError(f"'{key}' must be a list of strings.")
    tags = list(dict.fromkeys(t.strip() for t in v if t.strip()))
    if len(tags) > _MAX_TAGS or any(len(t) > 80 for t in tags):
        raise ToolError(f"'{key}' takes at most {_MAX_TAGS} tags of up to 80 characters.")
    return tags


def _polarity(args: dict) -> str:
    v = args.get("polarity") or "positive"
    if v not in ("positive", "negative"):
        raise ToolError("'polarity' must be \"positive\" or \"negative\".")
    return v


def _flag(args: dict, key: str) -> bool:
    v = args.get(key, False)
    if not isinstance(v, bool):
        raise ToolError(f"'{key}' must be true or false.")
    return v


def _block(b: BlockDoc) -> dict:
    return {"id": b.id, "category": b.category, "name": b.name, "text": b.text, "polarity": b.polarity,
            "tags": b.tags, "version": b.version}


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"  # the app's own id shape (frontend vault/ids.ts)


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _names(names: list[str]) -> str:
    shown = ", ".join(f"“{n}”" for n in names[:3])
    return shown + (f" and {len(names) - 3} more" if len(names) > 3 else "")


_PAGING = {
    "page": {"type": "integer", "minimum": 1, "default": 1},
    "per_page": {"type": "integer", "minimum": 1, "maximum": MAX_PAGE, "default": 50},
}


# ---- read tools ----
def _list_categories(settings: Settings, _args: dict) -> list[dict]:
    return [{"slug": c.slug, "name": c.name, "color": c.color, "blocks": c.count}
            for c in service.list_categories(settings)]


def _list_tags(settings: Settings, args: dict) -> list[dict]:
    return [{"name": t.name, "count": t.count} for t in service.list_tags(settings, _str(args, "category"))]


def _search_blocks(settings: Settings, args: dict) -> dict:
    page = service.list_blocks(
        settings, _strs(args, "categories"), _strs(args, "tags"), _str(args, "query"), "category",
        _int(args, "page", 1, 1, 100_000), _int(args, "per_page", 50, 1, MAX_PAGE),
    )
    return {"total": page.total, "page": page.page, "per_page": page.per_page, "items": [_block(b) for b in page.items]}


def _get_blocks(settings: Settings, args: dict) -> list[dict]:
    ids = _strs(args, "ids")
    if not ids:
        raise ToolError("'ids' must name at least one block.")
    return [_block(b) for b in service.resolve_blocks(settings, ids)]


def _list_works(settings: Settings, args: dict) -> dict:
    page = service.list_works(settings, _int(args, "page", 1, 1, 100_000), _int(args, "per_page", 50, 1, MAX_PAGE),
                              search=_str(args, "query") or None)
    return {"total": page.total, "page": page.page, "per_page": page.per_page,
            "items": [{"id": w.id, "title": w.title, "updated_at": w.updated_at, "image_count": w.image_count}
                      for w in page.items]}


_RECIPE_PARAMS = ("model", "width", "height", "steps", "scale", "sampler", "seed", "n_samples")


def _get_work(settings: Settings, args: dict) -> dict:
    doc = service.load_work(settings, _str(args, "work_id", required=True))
    snaps = {s.id: s for s in doc.snapshots}

    def image(im: Image) -> dict:
        snap = snaps.get(im.snapshot_id or "")
        return {
            "id": im.id, "favorite": im.favorite, "tags": im.tags, "description": im.description,
            "group": im.group, "created_at": im.created_at,
            "prompt": {"positive": snap.assembled_positive, "negative": snap.assembled_negative} if snap else None,
            "params": {k: snap.params[k] for k in _RECIPE_PARAMS if k in snap.params} if snap else None,
        }

    return {
        "id": doc.id, "title": doc.title, "created_at": doc.created_at, "updated_at": doc.updated_at,
        "params": {k: doc.params[k] for k in _RECIPE_PARAMS if k in doc.params},
        "combos": [c.model_dump() for c in doc.combos],
        "favorite_block_ids": doc.favorites,
        # The gallery only: scratch images are working material loose on the canvas, persisted but never listed.
        "images": [image(im) for im in doc.images if im.role == "gallery"],
        "stack_count": len(doc.stack),
    }


def _get_image(settings: Settings, args: dict) -> ImageResult:
    path = service.image_thumb_path(settings, _str(args, "work_id", required=True), _str(args, "image_id", required=True),
                                    _int(args, "width", 512, 64, 1024))
    return ImageResult(path.read_bytes())


def _get_catalog(_settings: Settings, _args: dict) -> dict:
    return get_catalog().model_dump()


def _list_combinations(settings: Settings, args: dict) -> list[dict]:
    return [c.model_dump() for c in service.load_work(settings, _str(args, "work_id", required=True)).combos]


# ---- Library writes (same rules as Library import: import_blocks) ----
def _category_of(settings: Settings) -> Callable[[str], str]:
    """Resolve a category given as its slug or its display name; none given → Custom."""
    cats = service.list_categories(settings)
    slugs = {c.slug for c in cats}
    by_name = {c.name.strip().lower(): c.slug for c in cats}

    def resolve(value: str) -> str:
        if not value:
            return "custom"
        if value in slugs:
            return value
        if slug := by_name.get(value.lower()):
            return slug
        raise ToolError(f"Unknown category “{value}” — list_categories has the slugs.")

    return resolve


def _category_names(settings: Settings, slugs: list[str]) -> str:
    names = {c.slug: c.name for c in service.list_categories(settings)}
    unique = list(dict.fromkeys(slugs))
    return f"Library → {names.get(unique[0], unique[0])}" if len(unique) == 1 else f"Library → {len(unique)} categories"


_library_lock = threading.Lock()  # parallel create_blocks calls would each miss the other's new blocks as duplicates


def _create_blocks(settings: Settings, args: dict) -> Written | dict:
    items = _items(args, "blocks", MAX_BATCH)
    category = _category_of(settings)
    with _library_lock:
        created, duplicates, errors = _add_blocks(settings, items, category)
    result = {"created": [{"id": b.id, "name": b.name, "category": b.category} for b in created],
              "duplicates": duplicates, "errors": errors}
    if not created:
        return result
    detail = _category_names(settings, [b.category for b in created])
    if duplicates:
        detail += f" ({_plural(len(duplicates), 'duplicate')} skipped)"
    return Written(result, f"Added {_plural(len(created), 'block')}", detail)


def _add_blocks(settings: Settings, items: list[dict],
                category: Callable[[str], str]) -> tuple[list[BlockDoc], list[dict], list[dict]]:
    existing = import_blocks.existing_index(settings)
    created: list[BlockDoc] = []
    duplicates: list[dict] = []
    errors: list[dict] = []
    for i, raw in enumerate(items):
        try:
            block = BlockDoc(id=_new_id("block"), category=category(_str(raw, "category", max_len=_MAX_NAME)),
                             name=_str(raw, "name", max_len=_MAX_NAME),
                             text=_str(raw, "text", required=True, max_len=_MAX_TEXT),
                             polarity=_polarity(raw), tags=_tags(raw))
        except ToolError as exc:
            errors.append({"index": i, "error": str(exc)})
            continue
        key = import_blocks.dedupe_key(block.polarity, block.text)
        if key in existing:  # the import's duplicate rule: same polarity + prompt text is the same block
            dup_id, dup_name = existing[key]
            duplicates.append({"index": i, "existing_id": dup_id, "existing_name": dup_name})
            continue
        service.save_block(settings, block)
        existing[key] = (block.id, block.name)
        created.append(block)
    return created, duplicates, errors


def _update_block(settings: Settings, args: dict) -> Written:
    block_id = _str(args, "id", required=True)
    found = service.resolve_blocks(settings, [block_id])
    if not found:
        raise ToolError(f"No Library block “{block_id}”.")
    block = found[0]
    if "category" in args:
        block.category = _category_of(settings)(_str(args, "category", max_len=_MAX_NAME))
    if "name" in args:
        block.name = _str(args, "name", max_len=_MAX_NAME)
    if "text" in args:
        block.text = _str(args, "text", required=True, max_len=_MAX_TEXT)
    if "polarity" in args:
        block.polarity = _polarity(args)
    if "tags" in args:
        block.tags = _tags(args)
    service.save_block(settings, block)  # bumps the version when the content changed
    return Written(_block(block), "Edited a block", f"“{block.name or block.id}”")


def _delete_blocks(settings: Settings, args: dict) -> Written | dict:
    ids = _strs(args, "ids", max_items=MAX_BATCH)
    if not ids:
        raise ToolError("'ids' must name at least one block.")
    names = {b.id: b.name or b.id for b in service.resolve_blocks(settings, ids)}
    deleted: list[str] = []
    for block_id in [i for i in dict.fromkeys(ids) if i in names]:
        try:
            service.delete_block(settings, block_id)
        except HTTPException as exc:
            if exc.status_code != 404:  # 404: deleted in the app meanwhile — just missing
                raise
            continue
        deleted.append(block_id)
    result = {"deleted": deleted, "missing": [i for i in ids if i not in deleted]}
    if not deleted:
        return result
    return Written(result, f"Deleted {_plural(len(deleted), 'block')}", _names([names[i] for i in deleted]))


# ---- work writes: load → change → save on the loaded copy as the lock base, logged for the open app ----
def _dump(value: object) -> object:
    return value.model_dump() if isinstance(value, BaseModel) else value


def _edit_work(settings: Settings, work_id: str, change: Callable[[WorkDoc], list[dict]]) -> tuple[WorkDoc, list[dict]]:
    """Apply ``change`` (it returns the operations it made — the open app replays them) and save. When the app
    saves the work in between (409), load it again and redo the change on the fresh copy."""
    with changes.edit_lock:
        for _ in range(3):
            doc = service.load_work(settings, work_id)
            prev = doc.updated_at
            ops = change(doc)
            if not ops:
                return doc, []
            try:
                saved = service.save_work(settings, doc, must_exist=True)
            except HTTPException as exc:
                if exc.status_code != 409 or service.load_work(settings, work_id).updated_at == prev:
                    raise  # not a concurrent edit (e.g. a vault move in progress)
                continue
            # Dumped after the save, which stamps what the change left empty (a new combination's times).
            ops = json.loads(json.dumps(ops, default=_dump))
            changes.record_work_edit(work_id, prev, saved["updated_at"], ops)
            return doc, ops
    raise ToolError("The work kept changing while this edit was being saved — try again.")


def _title(doc: WorkDoc) -> str:
    return f"“{doc.title or 'Untitled'}”"


def _create_work(settings: Settings, args: dict) -> Written:
    title = _str(args, "title", required=True, max_len=_MAX_NAME)
    doc = WorkDoc(id=_new_id("work"), title=title)  # no canvas yet — the app lays out a new work's widgets on open
    service.save_work(settings, doc)
    return Written({"id": doc.id, "title": doc.title}, "Created a work", _title(doc), work_id=doc.id)


def _rename_work(settings: Settings, args: dict) -> Written:
    title = _str(args, "title", required=True, max_len=_MAX_NAME)
    old: list[str] = []

    def change(doc: WorkDoc) -> list[dict]:
        old[:] = [doc.title or "Untitled"]
        doc.title = title
        return [{"op": "title", "title": title}]

    doc, _ = _edit_work(settings, _str(args, "work_id", required=True), change)
    return Written({"id": doc.id, "title": doc.title}, "Renamed a work", f"“{old[0]}” → {_title(doc)}", work_id=doc.id)


def _update_images(settings: Settings, args: dict) -> Written | dict:
    items = _items(args, "images", MAX_PAGE)

    def change(doc: WorkDoc) -> list[dict]:
        gallery = {im.id: im for im in doc.images if im.role == "gallery"}
        ops = []
        for raw in items:
            image_id = _str(raw, "id", required=True)
            im = gallery.get(image_id)
            if im is None:
                raise ToolError(f"No gallery image “{image_id}” in this work.")
            fields: dict = {}
            if "tags" in raw:
                im.tags = fields["tags"] = _tags(raw)
            if "description" in raw:
                im.description = fields["description"] = _str(raw, "description", max_len=_MAX_TEXT)
            if "favorite" in raw:
                im.favorite = fields["favorite"] = _flag(raw, "favorite")
            if fields:
                ops.append({"op": "image", "id": image_id, "set": fields})
        return ops

    doc, ops = _edit_work(settings, _str(args, "work_id", required=True), change)
    result = {"updated": [op["id"] for op in ops]}
    if not ops:
        return result
    return Written(result, f"Updated {_plural(len(ops), 'image')}", f"in {_title(doc)}", work_id=doc.id)


def _purge_images(doc: WorkDoc, ids: set[str]) -> None:
    """Delete images from every structure that names them — the images list, the canvas image nodes, every
    gallery grid album — and move the preview on. Mirrors ``frontend/src/vault/workOps.ts`` ``purgeImages``."""
    doc.images = [im for im in doc.images if im.id not in ids]
    nodes = doc.canvas.get("nodes")
    if isinstance(nodes, list):
        doc.canvas["nodes"] = nodes = [n for n in nodes if not (isinstance(n, dict) and n.get("type") == "image"
                                                                 and n.get("id") in ids)]
        for n in nodes:
            data = n.get("data") if isinstance(n, dict) else None
            if isinstance(data, dict) and n.get("type") == "zone" and data.get("role") == "gallery":
                for block in data.get("blocks") or []:
                    if isinstance(block, dict) and isinstance(block.get("imageIds"), list):
                        block["imageIds"] = [i for i in block["imageIds"] if i not in ids]
    if doc.preview_image_id in ids:
        doc.preview_image_id = next((im.id for im in doc.images if im.role == "gallery"), None)


def _delete_images(settings: Settings, args: dict) -> Written | dict:
    ids = _strs(args, "image_ids", max_items=MAX_PAGE)
    if not ids:
        raise ToolError("'image_ids' must name at least one image.")
    gone: list[str] = []

    def change(doc: WorkDoc) -> list[dict]:
        gallery = {im.id for im in doc.images if im.role == "gallery"}
        gone[:] = [i for i in dict.fromkeys(ids) if i in gallery]
        if not gone:
            return []
        _purge_images(doc, set(gone))
        return [{"op": "images_deleted", "ids": gone}]

    doc, ops = _edit_work(settings, _str(args, "work_id", required=True), change)
    result = {"deleted": gone, "missing": [i for i in ids if i not in gone]}
    if not ops:
        return result
    return Written(result, f"Deleted {_plural(len(gone), 'image')}", f"from {_title(doc)}", work_id=doc.id)


# ---- combinations ----
def _combo_blocks(settings: Settings, args: dict) -> list[ComboBlock]:
    """A combination's blocks: a Library block by ``block_id`` (its current text is copied in, like the station
    does) or a custom one by ``text``. Each block once — the station refuses repeats."""
    items = _items(args, "blocks", MAX_BATCH)
    lib_ids = [b["block_id"] for b in items if isinstance(b.get("block_id"), str)]
    library = {b.id: b for b in service.resolve_blocks(settings, lib_ids)} if lib_ids else {}
    out: list[ComboBlock] = []
    seen: dict[str, int] = {}
    for i, raw in enumerate(items):
        frozen = _flag(raw, "frozen")
        if block_id := _str(raw, "block_id"):
            lib = library.get(block_id)
            if lib is None:
                raise ToolError(f"blocks[{i}]: no Library block “{block_id}”.")
            block = ComboBlock(source="library", block_id=lib.id, version=lib.version, name=lib.name, text=lib.text,
                               polarity=lib.polarity, category=lib.category, tags=list(lib.tags), frozen=frozen)
        else:
            block = ComboBlock(source="custom", name=_str(raw, "name", max_len=_MAX_NAME),
                               text=_str(raw, "text", required=True, max_len=_MAX_TEXT), polarity=_polarity(raw),
                               category="custom", frozen=frozen)
        key = import_blocks.dedupe_key(block.polarity, block.text)
        if key in seen:
            raise ToolError(f"blocks[{i}] repeats blocks[{seen[key]}] (same polarity and text).")
        seen[key] = i
        out.append(block)
    return out


def _unique_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    n = 2
    while f"{name} ({n})" in taken:
        n += 1
    return f"{name} ({n})"


def _find_combo(doc: WorkDoc, combo_id: str) -> Combo:
    combo = next((c for c in doc.combos if c.id == combo_id), None)
    if combo is None:
        raise ToolError(f"No combination “{combo_id}” in this work.")
    return combo


def _create_combination(settings: Settings, args: dict) -> Written:
    name = _str(args, "name", required=True, max_len=_MAX_NAME)
    blocks = _combo_blocks(settings, args)
    made: list[Combo] = []

    def change(doc: WorkDoc) -> list[dict]:
        combo = Combo(id=_new_id("combo"), name=_unique_name(name, {c.name for c in doc.combos}), blocks=blocks,
                      origin="agent")
        doc.combos.insert(0, combo)  # newest first, as the widget lists them
        made[:] = [combo]
        return [{"op": "combo", "combo": combo}]

    doc, _ = _edit_work(settings, _str(args, "work_id", required=True), change)
    combo = made[0]
    return Written({"id": combo.id, "name": combo.name, "blocks": len(combo.blocks)}, "Saved a combination",
                   f"“{combo.name}” in {_title(doc)}", work_id=doc.id)


def _update_combination(settings: Settings, args: dict) -> Written:
    combo_id = _str(args, "combination_id", required=True)
    name = _str(args, "name", required=True, max_len=_MAX_NAME) if "name" in args else None
    blocks = _combo_blocks(settings, args) if "blocks" in args else None
    if name is None and blocks is None:
        raise ToolError("Give a new 'name', new 'blocks', or both.")
    edited: list[Combo] = []

    def change(doc: WorkDoc) -> list[dict]:
        combo = _find_combo(doc, combo_id)
        if name:
            combo.name = _unique_name(name, {c.name for c in doc.combos if c.id != combo_id})
        if blocks is not None:
            combo.blocks = blocks
        combo.origin = "agent"  # the card says an agent made it what it is now
        combo.updated_at = _stamp()
        edited[:] = [combo]
        return [{"op": "combo", "combo": combo}]

    doc, _ = _edit_work(settings, _str(args, "work_id", required=True), change)
    combo = edited[0]
    return Written({"id": combo.id, "name": combo.name, "blocks": len(combo.blocks)}, "Edited a combination",
                   f"“{combo.name}” in {_title(doc)}", work_id=doc.id)


def _delete_combination(settings: Settings, args: dict) -> Written:
    combo_id = _str(args, "combination_id", required=True)
    gone: list[str] = []

    def change(doc: WorkDoc) -> list[dict]:
        gone[:] = [_find_combo(doc, combo_id).name]
        doc.combos = [c for c in doc.combos if c.id != combo_id]
        return [{"op": "combo_deleted", "id": combo_id}]

    doc, _ = _edit_work(settings, _str(args, "work_id", required=True), change)
    return Written({"deleted": combo_id}, "Deleted a combination", f"“{gone[0]}” from {_title(doc)}", work_id=doc.id)


_BLOCK_FIELDS = {
    "category": {"type": "string", "description": "category slug or display name (list_categories); default Custom"},
    "name": {"type": "string", "maxLength": _MAX_NAME},
    "text": {"type": "string", "maxLength": _MAX_TEXT, "description": "the prompt text (tags / natural language)"},
    "polarity": {"type": "string", "enum": ["positive", "negative"], "default": "positive"},
    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": _MAX_TAGS},
}
_COMBO_BLOCKS = {
    "type": "array", "minItems": 1, "maxItems": MAX_BATCH,
    "description": "in prompt order; each a Library block by block_id, or a custom one by text",
    "items": {"type": "object", "properties": {
        "block_id": {"type": "string"},
        "text": {"type": "string", "maxLength": _MAX_TEXT}, "name": {"type": "string"},
        "polarity": {"type": "string", "enum": ["positive", "negative"]},
        "frozen": {"type": "boolean", "description": "kept in the set but left out of the prompt"}}},
}


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("list_categories", "List block categories",
         "Every Library block category: slug, display name, colour and how many blocks it holds. "
         "Names like \"Body - Skin\" are subgroups of \"Body\".",
         {"type": "object", "properties": {}}, "read", _list_categories),
    Tool("list_tags", "List block tags",
         "Tags used on Library blocks with their counts, optionally within one category.",
         {"type": "object", "properties": {"category": {"type": "string", "description": "category slug"}}},
         "read", _list_tags),
    Tool("search_blocks", "Search Library blocks",
         "Find Library prompt blocks by text (name or prompt), categories and tags (all given tags must match). "
         "Paged, ordered by category.",
         {"type": "object", "properties": {
             "query": {"type": "string"},
             "categories": {"type": "array", "items": {"type": "string"}, "description": "category slugs (any of)"},
             "tags": {"type": "array", "items": {"type": "string"}, "description": "tags (all of)"},
             **_PAGING}},
         "read", _search_blocks),
    Tool("get_blocks", "Get blocks by id", "The current version of the given Library blocks; unknown ids are skipped.",
         {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_PAGE}},
          "required": ["ids"]},
         "read", _get_blocks),
    Tool("list_works", "List works", "Works in the vault, most recently updated first, optionally filtered by title.",
         {"type": "object", "properties": {"query": {"type": "string"}, **_PAGING}}, "read", _list_works),
    Tool("get_work", "Get a work",
         "One work: title, generation params, saved block combinations, and every gallery image's metadata and "
         "recipe (the prompt it was generated from). Canvas layout is not included.",
         {"type": "object", "properties": {"work_id": {"type": "string"}}, "required": ["work_id"]},
         "read", _get_work),
    Tool("get_image", "View an image",
         "A downscaled PNG of one image of a work, to look at it (e.g. before describing or tagging it).",
         {"type": "object", "properties": {
             "work_id": {"type": "string"}, "image_id": {"type": "string"},
             "width": {"type": "integer", "minimum": 64, "maximum": 1024, "default": 512}},
          "required": ["work_id", "image_id"]},
         "read", _get_image),
    Tool("get_catalog", "Model catalog",
         "NovelAI models, samplers and resolution presets the app knows.",
         {"type": "object", "properties": {}}, "read", _get_catalog),
    Tool("list_combinations", "List combinations",
         "A work's saved block combinations (ready block sets for the generation station), newest first.",
         {"type": "object", "properties": {"work_id": {"type": "string"}}, "required": ["work_id"]},
         "read", _list_combinations),
    # Library
    Tool("create_blocks", "Create Library blocks",
         "Add prompt blocks to the Library, with the same rules as the app's Library import: a block whose polarity "
         "and text match an existing one is reported as a duplicate (with its id) instead of added. Returns the new "
         "ids; one bad item is reported in 'errors' and the rest still land.",
         {"type": "object", "properties": {"blocks": {
             "type": "array", "minItems": 1, "maxItems": MAX_BATCH,
             "items": {"type": "object", "properties": _BLOCK_FIELDS, "required": ["text"]}}},
          "required": ["blocks"]},
         "blocks_write", _create_blocks),
    Tool("update_block", "Edit a Library block",
         "Change a Library block's category, name, text, polarity or tags (only the fields given). Its version goes "
         "up when the content changes; combinations and past images keep the copy they were made with.",
         {"type": "object", "properties": {"id": {"type": "string"}, **_BLOCK_FIELDS}, "required": ["id"]},
         "blocks_write", _update_block),
    Tool("delete_blocks", "Delete Library blocks",
         "Remove blocks from the Library for good. Combinations and past images keep their own copies.",
         {"type": "object", "properties": {"ids": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                                   "maxItems": MAX_BATCH}},
          "required": ["ids"]},
         "blocks_delete", _delete_blocks),
    # works
    Tool("create_work", "Create a work", "Start a new, empty work with this title. Returns its id.",
         {"type": "object", "properties": {"title": {"type": "string", "maxLength": _MAX_NAME}}, "required": ["title"]},
         "works_write", _create_work),
    Tool("rename_work", "Rename a work", "Give a work a new title.",
         {"type": "object", "properties": {"work_id": {"type": "string"},
                                           "title": {"type": "string", "maxLength": _MAX_NAME}},
          "required": ["work_id", "title"]},
         "works_write", _rename_work),
    Tool("update_images", "Edit image metadata",
         "Set gallery images' tags (replaces their own tags), description or favourite flag — only the fields "
         "given, per image.",
         {"type": "object", "properties": {"work_id": {"type": "string"}, "images": {
             "type": "array", "minItems": 1, "maxItems": MAX_PAGE, "items": {"type": "object", "properties": {
                 "id": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}, "maxItems": _MAX_TAGS},
                 "description": {"type": "string", "maxLength": _MAX_TEXT}, "favorite": {"type": "boolean"}},
                 "required": ["id"]}}},
          "required": ["work_id", "images"]},
         "works_write", _update_images),
    Tool("delete_images", "Delete images", "Remove gallery images from a work for good (files included).",
         {"type": "object", "properties": {"work_id": {"type": "string"}, "image_ids": {
             "type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_PAGE}},
          "required": ["work_id", "image_ids"]},
         "works_delete", _delete_images),
    # combinations
    Tool("create_combination", "Create a combination",
         "Save a named block set in a work — what the user loads into the generation station in one click (blocks "
         "only, no generation params). Library blocks are copied in at their current version.",
         {"type": "object", "properties": {"work_id": {"type": "string"},
                                           "name": {"type": "string", "maxLength": _MAX_NAME}, "blocks": _COMBO_BLOCKS},
          "required": ["work_id", "name", "blocks"]},
         "combos_write", _create_combination),
    Tool("update_combination", "Edit a combination",
         "Rename a combination and/or replace its blocks (the whole list, in order).",
         {"type": "object", "properties": {"work_id": {"type": "string"}, "combination_id": {"type": "string"},
                                           "name": {"type": "string", "maxLength": _MAX_NAME}, "blocks": _COMBO_BLOCKS},
          "required": ["work_id", "combination_id"]},
         "combos_write", _update_combination),
    Tool("delete_combination", "Delete a combination", "Remove a saved combination from a work.",
         {"type": "object", "properties": {"work_id": {"type": "string"}, "combination_id": {"type": "string"}},
          "required": ["work_id", "combination_id"]},
         "works_delete", _delete_combination),
]}


def run(tool: Tool, settings: Settings, args: dict) -> Any:
    """Run a tool, turning service-level HTTP errors into agent-facing ToolErrors."""
    try:
        return tool.handler(settings, args)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc


def image_block(result: ImageResult) -> dict:
    return {"type": "image", "mimeType": "image/png", "data": base64.b64encode(result.png).decode("ascii")}
