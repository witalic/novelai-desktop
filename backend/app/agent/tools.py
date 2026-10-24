"""The MCP tools agents can call. Each one wraps the same vault/catalog services the UI uses, declares the
permission (``config.AgentPermissions`` field) it needs, and validates its own arguments. Handlers are
sync (vault I/O) — the protocol layer runs them in a worker thread.

Results are plain JSON-able values; ``ImageResult`` becomes an MCP image block. A ``ToolError`` is the
agent's mistake (bad argument, missing work, …) and is reported back as a tool error, not a crash.
"""
import base64
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.novelai.catalog import get_catalog
from app.settings import Settings
from app.vault import service
from app.vault.models import BlockDoc, Image

MAX_PAGE = 200


class ToolError(Exception):
    """The call can't be done as asked — the message goes back to the agent."""


@dataclass(frozen=True)
class ImageResult:
    png: bytes


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
    if v is None or v == "":
        if required:
            raise ToolError(f"'{key}' is required.")
        return ""
    if not isinstance(v, str):
        raise ToolError(f"'{key}' must be a string.")
    if len(v) > max_len:
        raise ToolError(f"'{key}' is too long (max {max_len} characters).")
    return v.strip()


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


def _block(b: BlockDoc) -> dict:
    return {"id": b.id, "category": b.category, "name": b.name, "text": b.text, "polarity": b.polarity,
            "tags": b.tags, "version": b.version}


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
]}


def run(tool: Tool, settings: Settings, args: dict) -> Any:
    """Run a tool, turning service-level HTTP errors into agent-facing ToolErrors."""
    try:
        return tool.handler(settings, args)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc


def image_block(result: ImageResult) -> dict:
    return {"type": "image", "mimeType": "image/png", "data": base64.b64encode(result.png).decode("ascii")}
