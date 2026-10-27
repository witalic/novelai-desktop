"""The MCP tools agents can call. Each one wraps the same vault/catalog services the UI uses, declares the
permission (``config.AgentPermissions`` field) it needs, and validates its own arguments. Handlers are
sync (vault I/O) — the protocol layer runs them in a worker thread.

Results are plain JSON-able values; ``ImageResult`` becomes an MCP image block, and a write returns ``Written``
— its result plus what it did, for the activity feed and the app's notice (``changes``). Failures are the
``errors`` classes, reported back as tool errors, not crashes.
"""
import base64
import copy
import io
import json
import re
import secrets
import threading
import time
import zipfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import anyio
import httpx
from fastapi import HTTPException
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont, ImageOps
from pydantic import ValidationError

from app import appconfig
from app.agent import generation, store
from app.agent.errors import GenerationFailed, Refused, ToolError
from app.novelai import GenerateParams, get_client
from app.novelai.augment import augment
from app.novelai.catalog import get_catalog
from app.novelai.cost import anlas_cost
from app.novelai.dedup import dedupe_prompt
from app.novelai.errors import NovelAIError, NovelAIRateLimitError
from app.novelai.tokenizer import count_tokens
from app.settings import Settings
from app.vault import gallery_layout, import_blocks, index, presets, service, workedit, worklog
from app.vault.models import (
    BlockDoc, Combo, ComboBlock, Component, Image, PresetParams, SaveCategory, Snapshot, WorkDoc,
)

MAX_PAGE = 200
MAX_BATCH = 100  # blocks per create/delete call, blocks per combination
_MAX_TEXT = 4000
_MAX_PROMPT = 50_000  # a whole assembled prompt, to count
_MAX_NAME = 200
_MAX_TAGS = 50


@dataclass(frozen=True)
class ImageResult:
    data: bytes
    mime: str = "image/png"
    caption: str = ""  # sent along as a text block (e.g. which image is which on a contact sheet)


@dataclass(frozen=True)
class Written:
    """A write's result for the agent, plus what it did: ``action`` (e.g. "Added 3 blocks") and ``detail``
    (what it touched) make the activity row and the app's notice; ``work_id`` is the work it changed (None: the
    Library) — ``also`` other works it changed, ``deleted`` when it removed that work; ``cost`` the Anlas a generation
    spent (0: free; None: not a generation)."""

    result: Any
    action: str
    detail: str
    work_id: str | None = None
    cost: int | None = None
    also: tuple[str, ...] = ()
    deleted: bool = False


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
            "items": [{"id": w.id, "title": w.title, "updated_at": w.updated_at, "image_count": w.image_count,
                       **({"match": w.match.model_dump(exclude_defaults=True)} if w.match else {})}
                      for w in page.items]}


# Everything a recipe holds — what reproduces an image (and the station settings a work keeps).
_RECIPE_KEYS = ("model", "width", "height", "steps", "scale", "sampler", "seed", "n_samples", "noise_schedule",
                "cfg_rescale", "quality_toggle", "uc_preset", "dedupe")
MAX_IMAGES_PAGE = 100  # a page of full recipes still fits an agent's tool-result limit…
MAX_PROMPTS_PAGE = 25  # …and of recipes with their prompts and blocks


def _recipe_of(params: dict) -> dict:
    return {k: params[k] for k in _RECIPE_KEYS if k in params}


def _get_work(settings: Settings, args: dict) -> dict:
    doc = service.load_work(settings, _str(args, "work_id", required=True))
    return {
        "id": doc.id, "title": doc.title, "created_at": doc.created_at, "updated_at": doc.updated_at,
        "params": _recipe_of(doc.params), "preview_image_id": doc.preview_image_id,
        "combinations": [{"id": c.id, "name": c.name, "blocks": len(c.blocks), "origin": c.origin} for c in doc.combos],
        "favorite_block_ids": doc.favorites,
        # The gallery only: scratch images are working material loose on the canvas, persisted but never listed.
        "image_count": sum(im.role == "gallery" for im in doc.images),
        "stack_count": len(doc.stack),
    }


def _made_from(doc: WorkDoc) -> Callable[[Snapshot | None], list[dict]]:
    """The work's combinations an image was made from: those whose prompt blocks — polarity and text, in order — are
    the image's. A combination edited since no longer matches the images it made before."""
    def key(blocks: list) -> tuple:
        return tuple((b.polarity, b.text.strip()) for b in blocks if b.text.strip())

    combos = [(c, key([b for b in c.blocks if not b.frozen])) for c in doc.combos]

    def of(snap: Snapshot | None) -> list[dict]:
        if snap is None:
            return []
        made = key(snap.components)
        return [{"id": c.id, "name": c.name} for c, k in combos if k and k == made]

    return of


def _list_images(settings: Settings, args: dict) -> dict:
    doc = service.load_work(settings, _str(args, "work_id", required=True))
    tags, favorite, with_prompt = _tags(args), _flag(args, "favorite"), _flag(args, "include_prompt")
    combo_id = _str(args, "combination_id")
    if combo_id:
        _find_combo(doc, combo_id)
    page = _int(args, "page", 1, 1, 100_000)
    per_page = (_int(args, "per_page", 10, 1, MAX_PROMPTS_PAGE) if with_prompt
                else _int(args, "per_page", 50, 1, MAX_IMAGES_PAGE))
    snaps = {s.id: s for s in doc.snapshots}
    made_from = _made_from(doc)

    def block_tags(im: Image) -> list[str]:
        """Tags the image has from the blocks it was made of — the gallery and search_images match these too."""
        snap = snaps.get(im.snapshot_id or "")
        own = set(im.tags)
        return list(dict.fromkeys(t for c in snap.components for t in c.tags if t not in own)) if snap else []

    def combos_of(im: Image) -> list[dict]:
        return made_from(snaps.get(im.snapshot_id or ""))

    shown = sorted((im for im in doc.images if im.role == "gallery" and (im.favorite or not favorite)
                    and set(tags) <= {*im.tags, *block_tags(im)}
                    and (not combo_id or any(c["id"] == combo_id for c in combos_of(im)))),
                   key=lambda im: (im.created_at, im.id), reverse=True)

    def item(im: Image) -> dict:
        snap = snaps.get(im.snapshot_id or "")
        out = {"id": im.id, "created_at": im.created_at, "favorite": im.favorite, "tags": im.tags,
               "block_tags": block_tags(im), "combinations": combos_of(im), "description": im.description,
               "params": _recipe_of(snap.params) if snap else None}
        if with_prompt and snap:
            out["prompt"] = {"positive": snap.assembled_positive, "negative": snap.assembled_negative}
            out["blocks"] = [{"name": c.name, "block_id": c.block_id, "version": c.version, "polarity": c.polarity}
                             for c in snap.components]
        return out

    return {"total": len(shown), "page": page, "per_page": per_page,
            "items": [item(im) for im in shown[(page - 1) * per_page:page * per_page]]}


def _search_images(settings: Settings, args: dict) -> dict:
    query = index.ImageQuery(tags=tuple(_tags(args)), favorite=_flag(args, "favorite"))
    page = service.gallery(settings, query, _int(args, "per_page", 50, 1, MAX_PAGE),
                           page=_int(args, "page", 1, 1, 100_000))
    return {"total": page.total, "page": page.page, "per_page": page.per_page,
            "items": [{"image_id": i.image_id, "work_id": i.work_id, "work_title": i.work_title,
                       "favorite": i.favorite, "created_at": i.created_at} for i in page.items]}


def _crop_box(raw: object, size: tuple[int, int]) -> tuple[int, int, int, int]:
    if not isinstance(raw, dict):
        raise ToolError("'crop' must be an object with x, y, width and height.")
    x, y = _int(raw, "x", 0, 0, 100_000), _int(raw, "y", 0, 0, 100_000)
    w, h = _int(raw, "width", 16, 16, 100_000), _int(raw, "height", 16, 16, 100_000)
    if x + w > size[0] or y + h > size[1]:
        raise ToolError(f"The crop goes past the image's edge — it is {size[0]}×{size[1]} px.")
    return x, y, x + w, y + h


def _encode(im: PILImage.Image, fmt: str = "PNG") -> bytes:
    buf = io.BytesIO()
    im.save(buf, fmt, **({"quality": 85} if fmt == "JPEG" else {}))
    return buf.getvalue()


def _get_image(settings: Settings, args: dict) -> ImageResult:
    work_id, image_id = _str(args, "work_id", required=True), _str(args, "image_id", required=True)
    width = _int(args, "width", 512, 64, 1024)
    if args.get("crop") is None:
        return ImageResult(service.image_thumb_path(settings, work_id, image_id, width).read_bytes())
    # A region at the source's own resolution — a close look (a signature, the hands) without the whole image.
    with PILImage.open(service.image_path(settings, work_id, image_id)) as im:
        part = im.crop(_crop_box(args["crop"], im.size))
    if part.width > width:
        part = part.resize((width, round(part.height * width / part.width)), PILImage.LANCZOS)
    return ImageResult(_encode(part))


_SHEET_MAX = 36
_SHEET_DEFAULT = 24
_SHEET_LONG_SIDE = 1568  # what a model looks at without shrinking it (and well inside any image-size limit)
_SHEET_SOURCE_W = 320  # one cached thumbnail size per image, shrunk to the tile in memory


def _contact_sheet(settings: Settings, args: dict) -> ImageResult:
    """Many gallery images in one picture, numbered — the caption says which number is which image."""
    work_id = _str(args, "work_id", required=True)
    doc = service.load_work(settings, work_id)
    gallery = {im.id: im for im in doc.images if im.role == "gallery"}
    if ids := _strs(args, "image_ids", max_items=_SHEET_MAX):
        if missing := [i for i in ids if i not in gallery]:
            raise ToolError(f"No gallery image {_names(missing)} in this work.")
        ids = list(dict.fromkeys(ids))
    else:  # the newest ones
        ids = [im.id for im in sorted(gallery.values(), key=lambda im: im.created_at, reverse=True)[:_SHEET_DEFAULT]]
    if not ids:
        raise ToolError("This work has no gallery images yet.")
    cols = min(_int(args, "cols", 6, 1, 8), len(ids))
    rows = -(-len(ids) // cols)
    # Tiles shrink so the sheet's long side fits; cells are 2:3, like NovelAI's default sizes.
    tile = min(_int(args, "tile", 192, 64, 320), _SHEET_LONG_SIDE // cols, _SHEET_LONG_SIDE * 2 // (rows * 3))
    if tile < 64:
        raise ToolError(f"{len(ids)} images in {cols} columns make too tall a sheet — use more columns or fewer images.")
    cell_h = tile * 3 // 2
    sheet = PILImage.new("RGB", (cols * tile, rows * cell_h), (24, 26, 29))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=max(12, tile // 9))
    for n, image_id in enumerate(ids):
        x, y = (n % cols) * tile, (n // cols) * cell_h
        with PILImage.open(service.image_thumb_path(settings, work_id, image_id, _SHEET_SOURCE_W)) as im:
            thumb = ImageOps.contain(im.convert("RGB"), (tile - 4, cell_h - 4))
        sheet.paste(thumb, (x + (tile - thumb.width) // 2, y + (cell_h - thumb.height) // 2))
        label = str(n + 1)
        left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
        draw.rectangle((x + 2, y + 2, x + 10 + right - left, y + 8 + bottom - top), fill=(0, 0, 0))
        draw.text((x + 6 - left, y + 5 - top), label, fill=(255, 255, 255), font=font)
    caption = "\n".join(f"{n + 1}: {image_id}" for n, image_id in enumerate(ids))
    return ImageResult(_encode(sheet, "JPEG"), "image/jpeg", caption)


def _get_catalog(_settings: Settings, _args: dict) -> dict:
    return get_catalog().model_dump()


def _list_combinations(settings: Settings, args: dict) -> list[dict]:
    return [c.model_dump() for c in service.load_work(settings, _str(args, "work_id", required=True)).combos]


def _list_presets(settings: Settings, _args: dict) -> list[dict]:
    return [{"id": p.id, "name": p.name, "builtin": p.builtin, "favorite": p.favorite, "default": p.is_default,
             "params": p.params.model_dump()} for p in presets.list_presets(settings)]


_ACCOUNT_TTL_S = 30.0  # an agent checking its balance between calls shouldn't hit NovelAI every time


def _get_account(settings: Settings, _args: dict) -> dict:
    try:  # a read this young with no generation since is served again (generation.subscription)
        sub = generation.subscription(_client(settings), max_age=_ACCOUNT_TTL_S)
    except (NovelAIError, httpx.HTTPError) as exc:
        raise ToolError(f"Could not read the NovelAI subscription: {exc}") from exc
    cfg = store.load_config(settings)
    return {"tier": sub.get("tier"), "tier_name": sub.get("tier_name"), "active": sub.get("active"),
            "anlas": sub.get("anlas"), "free_generations": generation.FREE_TEXT if generation.opus_of(sub) else None,
            "agent_paid_generations": cfg.spending.paid, "agent_spent_today": store.spent_today(cfg),
            "agent_daily_budget": cfg.spending.daily_budget}


def _count_tokens(_settings: Settings, args: dict) -> dict:
    """Tokens as generation would send them — quality tags and the UC preset added, like the station's meter."""
    cat = get_catalog()
    model = _str(args, "model") or cat.default_model
    spec = next((m for m in cat.models if m.id == model), None)
    if spec is None:
        raise ToolError(f"Unknown model “{model}” — get_catalog lists them.")
    quality = _flag(args, "quality_toggle") if "quality_toggle" in args else True
    uc_preset = _int(args, "uc_preset", 4, 0, 7)
    positive, negative = _str(args, "positive", max_len=_MAX_PROMPT), _str(args, "negative", max_len=_MAX_PROMPT)
    if _flag(args, "dedupe"):
        positive, negative = dedupe_prompt(positive), dedupe_prompt(negative)
    text = augment(model, positive, negative, quality=quality, uc_preset=uc_preset)
    return {"positive": count_tokens(text.positive, spec.tokenizer), "positive_limit": spec.token_limit,
            "negative": count_tokens(text.negative, spec.tokenizer), "negative_limit": spec.negative_token_limit,
            "tokenizer": spec.tokenizer}


# ---- Library writes (same rules as Library import: import_blocks) ----
_PALETTE = ("#6e5dc6", "#0c66e4", "#ae4787", "#1f845a", "#b65c02", "#12b5a6", "#d4537e", "#e2483d", "#2fb8c6",
            "#738496")  # the Library's own (LibraryView.vue): a new category takes the first hue not in use
_MAX_NEW_CATEGORIES = 10
_CATEGORY_ID = re.compile(r"cat-[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}")  # service._new_category_id


def _category_of(settings: Settings, created: list[str]) -> Callable[[str], str]:
    """Resolve a category given as its slug or its display name; none given → Custom. A name the Library doesn't
    know yet becomes a new category, as the Library's "New category" makes one (its name lands in ``created``)."""
    cats = service.list_categories(settings)
    slugs = {c.slug for c in cats}
    by_name = {c.name.strip().lower(): c.slug for c in cats}
    colors = {c.color.lower() for c in cats}

    def resolve(value: str) -> str:
        if not value:
            return "custom"
        if value in slugs:
            return value
        if slug := by_name.get(value.lower()):
            return slug
        if _CATEGORY_ID.fullmatch(value):  # the id of a category deleted since — not a name for a new one
            raise ToolError(f"Unknown category “{value}” — list_categories has the slugs.")
        if len(created) >= _MAX_NEW_CATEGORIES:
            raise ToolError(f"At most {_MAX_NEW_CATEGORIES} new categories in one call.")
        color = next((p for p in _PALETTE if p not in colors), _PALETTE[0])
        cat = service.save_category(settings, SaveCategory(name=value, color=color))
        slugs.add(cat.slug)
        by_name[value.lower()] = cat.slug
        colors.add(color)
        created.append(cat.name)
        return cat.slug

    return resolve


def _category_names(settings: Settings, slugs: list[str]) -> str:
    names = {c.slug: c.name for c in service.list_categories(settings)}
    unique = list(dict.fromkeys(slugs))
    return f"Library → {names.get(unique[0], unique[0])}" if len(unique) == 1 else f"Library → {len(unique)} categories"


_library_lock = threading.Lock()  # parallel create_blocks calls would each miss the other's new blocks as duplicates


def _new_categories(names: list[str]) -> str:
    return f" · new categor{'y' if len(names) == 1 else 'ies'} {_names(names)}" if names else ""


def _create_blocks(settings: Settings, args: dict) -> Written | dict:
    items = _items(args, "blocks", MAX_BATCH)
    new_cats: list[str] = []
    with _library_lock:
        created, duplicates, errors = _add_blocks(settings, items, _category_of(settings, new_cats))
    result = {"created": [{"id": b.id, "name": b.name, "category": b.category} for b in created],
              "duplicates": duplicates, "errors": errors, "new_categories": new_cats}
    if not created:
        return result
    detail = _category_names(settings, [b.category for b in created])
    if duplicates:
        detail += f" ({_plural(len(duplicates), 'duplicate')} skipped)"
    return Written(result, f"Added {_plural(len(created), 'block')}", detail + _new_categories(new_cats))


def _add_blocks(settings: Settings, items: list[dict],
                category: Callable[[str], str]) -> tuple[list[BlockDoc], list[dict], list[dict]]:
    existing = import_blocks.existing_index(settings)
    created: list[BlockDoc] = []
    duplicates: list[dict] = []
    errors: list[dict] = []
    for i, raw in enumerate(items):
        try:
            name, text = _str(raw, "name", max_len=_MAX_NAME), _str(raw, "text", required=True, max_len=_MAX_TEXT)
            polarity, tags, cat = _polarity(raw), _tags(raw), _str(raw, "category", max_len=_MAX_NAME)
        except ToolError as exc:
            errors.append({"index": i, "error": str(exc)})
            continue
        key = import_blocks.dedupe_key(polarity, text)
        if key in existing:  # the import's duplicate rule: same polarity + prompt text is the same block
            dup_id, dup_name = existing[key]
            duplicates.append({"index": i, "existing_id": dup_id, "existing_name": dup_name})
            continue
        try:  # the category last: a new one is made only for a block that really lands
            slug = category(cat)
        except ToolError as exc:
            errors.append({"index": i, "error": str(exc)})
            continue
        block = BlockDoc(id=workedit.new_id("block"), category=slug, name=name, text=text, polarity=polarity, tags=tags)
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
    new_cats: list[str] = []
    if "name" in args:
        block.name = _str(args, "name", max_len=_MAX_NAME)
    if "text" in args:
        block.text = _str(args, "text", required=True, max_len=_MAX_TEXT)
    if "polarity" in args:
        block.polarity = _polarity(args)
    if "tags" in args:
        block.tags = _tags(args)
    if "category" in args:  # last: a new category is only made once the rest of the edit is known to be valid
        with _library_lock:
            block.category = _category_of(settings, new_cats)(_str(args, "category", max_len=_MAX_NAME))
    service.save_block(settings, block)  # bumps the version when the content changed
    return Written({**_block(block), "new_categories": new_cats}, "Edited a block",
                   f"“{block.name or block.id}”" + _new_categories(new_cats))


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


# ---- work writes: through app/vault/workedit — saved on the loaded copy as the lock base, logged for the open app ----
def _create_work(settings: Settings, args: dict) -> Written:
    title = _str(args, "title", required=True, max_len=_MAX_NAME)
    doc = WorkDoc(id=workedit.new_id("work"), title=title)
    gallery_layout.blocks(doc)  # just the gallery zone, to arrange — the app lays the other widgets out on open
    service.save_work(settings, doc)
    return Written({"id": doc.id, "title": doc.title}, "Created a work", workedit.title_of(doc), work_id=doc.id)


def _rename_work(settings: Settings, args: dict) -> Written:
    title = _str(args, "title", required=True, max_len=_MAX_NAME)
    old: list[str] = []

    def change(doc: WorkDoc) -> list[dict]:
        old[:] = [doc.title or "Untitled"]
        doc.title = title
        return [{"op": "title", "title": title}]

    doc, _ = workedit.edit_work(settings, _str(args, "work_id", required=True), change)
    return Written({"id": doc.id, "title": doc.title}, "Renamed a work", f"“{old[0]}” → {workedit.title_of(doc)}",
                   work_id=doc.id)


def _update_images(settings: Settings, args: dict) -> Written | dict:
    items = _items(args, "images", MAX_PAGE)

    def change(doc: WorkDoc) -> list[dict]:
        in_gallery = {im.id: im for im in doc.images if im.role == "gallery"}
        ops = []
        for raw in items:
            image_id = _str(raw, "id", required=True)
            im = in_gallery.get(image_id)
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

    doc, ops = workedit.edit_work(settings, _str(args, "work_id", required=True), change)
    result = {"updated": [op["id"] for op in ops]}
    if not ops:
        return result
    return Written(result, f"Updated {_plural(len(ops), 'image')}", f"in {workedit.title_of(doc)}", work_id=doc.id)


def _star_blocks(settings: Settings, args: dict) -> Written | dict:
    ids = list(dict.fromkeys(_strs(args, "block_ids", max_items=MAX_BATCH)))
    if not ids:
        raise ToolError("'block_ids' must name at least one block.")
    starred = _flag(args, "starred") if "starred" in args else True
    names = {b.id: b.name for b in service.resolve_blocks(settings, ids)}
    if starred and (missing := next((i for i in ids if i not in names), None)):
        raise ToolError(f"No Library block “{missing}”.")  # unstarring one that's gone from the Library is fine
    changed: list[str] = []

    def change(doc: WorkDoc) -> list[dict]:
        if starred:
            changed[:] = [i for i in ids if i not in doc.favorites]
            doc.favorites += changed
        else:
            changed[:] = [i for i in ids if i in doc.favorites]
            doc.favorites = [f for f in doc.favorites if f not in changed]
        return [{"op": "starred", "ids": list(changed), "starred": starred}] if changed else []

    doc, ops = workedit.edit_work(settings, _str(args, "work_id", required=True), change)
    result = {"favorite_block_ids": doc.favorites}
    if not ops:
        return result
    action = f"{'Starred' if starred else 'Unstarred'} {_plural(len(changed), 'block')}"
    return Written(result, action, f"{_names([names.get(i, i) for i in changed])} in {workedit.title_of(doc)}",
                   work_id=doc.id)


def _delete_images(settings: Settings, args: dict) -> Written | dict:
    ids = _strs(args, "image_ids", max_items=MAX_PAGE)
    if not ids:
        raise ToolError("'image_ids' must name at least one image.")
    gone: list[str] = []

    def change(doc: WorkDoc) -> list[dict]:
        in_gallery = {im.id for im in doc.images if im.role == "gallery"}
        gone[:] = [i for i in dict.fromkeys(ids) if i in in_gallery]
        if not gone:
            return []
        workedit.purge_images(doc, set(gone))
        return [{"op": "images_deleted", "ids": gone}]

    doc, ops = workedit.edit_work(settings, _str(args, "work_id", required=True), change)
    result = {"deleted": gone, "missing": [i for i in ids if i not in gone]}
    if not ops:
        return result
    return Written(result, f"Deleted {_plural(len(gone), 'image')}", f"from {workedit.title_of(doc)}", work_id=doc.id)


def _delete_work(settings: Settings, args: dict) -> Written:
    work_id = _str(args, "work_id", required=True)
    with worklog.edit_lock:  # never in the middle of a backend edit of it
        doc = service.load_work(settings, work_id)
        service.delete_work(settings, work_id)
    count = sum(im.role == "gallery" for im in doc.images)
    return Written({"deleted": work_id}, "Deleted a work", f"{workedit.title_of(doc)} with {_plural(count, 'image')}",
                   work_id=work_id, deleted=True)


_MAX_MOVE = 20  # images per move


def _move_images(settings: Settings, args: dict) -> Written:
    src_id, dst_id = _str(args, "from_work_id", required=True), _str(args, "to_work_id", required=True)
    if src_id == dst_id:
        raise ToolError("'to_work_id' is the work they are in already.")
    ids = list(dict.fromkeys(_strs(args, "image_ids", max_items=_MAX_MOVE)))
    if not ids:
        raise ToolError("'image_ids' must name at least one image.")
    moved = workedit.transfer_images(settings, src_id, dst_id, ids, copy=_flag(args, "copy"))
    result = {"to_work_id": dst_id, "images": [{"from": i, "id": moved.ids[i]} for i in ids], "copied": moved.copied}
    what = f"{_plural(len(ids), 'image')} from {workedit.title_of(moved.src)} to {workedit.title_of(moved.dst)}"
    if moved.not_removed:
        result["not_removed"] = moved.not_removed
        return Written(result, "Copied images", f"{what} — removing them there failed", work_id=dst_id)
    if moved.copied:
        return Written(result, "Copied images", what, work_id=dst_id)
    return Written(result, "Moved images", what, work_id=dst_id, also=(src_id,))


# ---- the gallery layout (app/vault/gallery_layout): sections, headings, albums, Quick access, the cover ----
_GALLERY_TYPES = ("section", "heading", "text", "divider", "grid", "meta")
_GALLERY_FIELDS: dict[str, set[str]] = {  # what each block type takes
    "section": {"title", "collapsed"}, "heading": {"text", "level"}, "text": {"text"}, "divider": set(),
    "grid": {"cols", "collapsed", "image_ids"}, "meta": {"grid"},
}
_META_FIELDS = [  # a new Metadata block, as the app makes one (GalleryStack newBlock): summaries of its images
    {"key": "Tags", "kind": "chips", "auto": "tags"}, {"key": "Date", "kind": "text", "auto": "date"},
    {"key": "Model", "kind": "mono", "auto": "model"}, {"key": "Seed", "kind": "mono", "auto": "seed"},
]
_MAX_GALLERY_OPS = 100
_MAX_GALLERY_BLOCKS = 300


def _gallery_view(doc: WorkDoc) -> dict:
    shown = sorted((im for im in doc.images if im.role == "gallery"), key=lambda im: (im.created_at, im.id),
                   reverse=True)
    live = {im.id for im in shown}
    stack = gallery_layout.stack(doc)
    grid_ids = {b.get("id") for b in gallery_layout.grids(stack)}
    placed: set[str] = set()
    out: list[dict] = []
    section = None
    for b in stack:
        kind = b.get("type")
        item: dict = {"id": b.get("id"), "type": kind}
        if kind == "section":
            section = b.get("id")
            item.update(title=b.get("title", ""), collapsed=bool(b.get("collapsed")))
        else:
            if section:
                item["section"] = section
            if kind == "heading":
                item.update(text=b.get("text", ""), level=b.get("level", 2))
            elif kind == "text":
                item["text"] = b.get("text", "")
            elif kind == "grid":
                ids = [i for i in b["imageIds"] if i in live] if isinstance(b.get("imageIds"), list) else []
                placed.update(ids)
                item.update(cols=b.get("cols", 3), collapsed=bool(b.get("collapsed")), image_ids=ids)
            elif kind == "meta":  # a deleted grid's summary falls back to the whole gallery, as in the app
                item["grid"] = b.get("gridId") if b.get("gridId") in grid_ids else None
        out.append(item)
    return {"work_id": doc.id, "cover_image_id": doc.preview_image_id, "image_count": len(live), "blocks": out,
            "quick_access": [im.id for im in shown if im.id not in placed]}


def _get_gallery(settings: Settings, args: dict) -> dict:
    return _gallery_view(service.load_work(settings, _str(args, "work_id", required=True)))


def _level(args: dict) -> int:
    v = args.get("level", 2)
    if type(v) is not int or v not in (1, 2):
        raise ToolError("'level' must be 1 or 2.")
    return v


class _Arrange:
    """One edit_gallery call on a loaded work. Each of the agent's ops is checked against the layout the ops before it
    left, then applied as the operations the open app replays (``gallery_layout.apply``) — collected in ``ops``."""

    def __init__(self, doc: WorkDoc) -> None:
        self.doc = doc
        self.in_gallery = {im.id for im in doc.images if im.role == "gallery"}
        self.refs: dict[str, str] = {}  # an add's 'ref' → the new block's id
        self.ops: list[dict] = []
        self.created: list[dict] = []
        self.counts: Counter[str] = Counter()

    def _run(self, op: dict) -> None:
        gallery_layout.apply(self.doc, op)
        self.ops.append(op)

    @staticmethod
    def _only(raw: dict, allowed: set[str], what: str) -> None:
        if extra := sorted(set(raw) - allowed - {"op"}):
            raise ToolError(f"'{extra[0]}' doesn't apply to {what}.")

    def _block(self, raw: dict, key: str = "block") -> dict:
        name = _str(raw, key, required=True)
        bid = self.refs.get(name, name)
        found = next((b for b in gallery_layout.blocks(self.doc) if b.get("id") == bid), None)
        if found is None:
            raise ToolError(f"No gallery block “{name}” — get_gallery lists them.")
        return found

    def _grid(self, raw: dict) -> dict:
        found = self._block(raw, "grid")
        if found.get("type") != "grid":
            raise ToolError(f"“{raw['grid']}” is a {found.get('type')}, not a grid.")
        return found

    def _images(self, raw: dict) -> list[str]:
        ids = list(dict.fromkeys(_strs(raw, "image_ids", max_items=MAX_PAGE)))
        if not ids:
            raise ToolError("'image_ids' must name at least one image.")
        if missing := next((i for i in ids if i not in self.in_gallery), None):
            raise ToolError(f"No gallery image “{missing}” in this work.")
        return ids

    def _landing(self, raw: dict, moving: list[dict], section: bool) -> tuple[list[dict], int]:
        """Where a block goes: before or after a block, else at the end — as the blocks staying put and the index
        among them. Placed after a section, a section goes after the blocks under it: sections don't nest."""
        if "before" in raw and "after" in raw:
            raise ToolError("Give 'before' or 'after', not both.")
        skip = {id(b) for b in moving}
        rest = [b for b in gallery_layout.blocks(self.doc) if id(b) not in skip]
        key = "before" if "before" in raw else "after" if "after" in raw else None
        if key is None:
            return rest, len(rest)
        target = self._block(raw, key)
        if id(target) in skip:
            raise ToolError(f"'{key}' names the block being moved{' or one under it' if section else ''}.")
        at = next(i for i, b in enumerate(rest) if b is target)
        if key == "before":
            return rest, at
        return rest, gallery_layout.span(rest, at) if section else at + 1

    def add(self, raw: dict) -> None:
        kind = raw.get("type")
        if kind not in _GALLERY_TYPES:
            raise ToolError(f"'type' must be one of: {', '.join(_GALLERY_TYPES)}.")
        self._only(raw, _GALLERY_FIELDS[kind] | {"type", "ref", "before", "after"}, f"a {kind}")
        block: dict = {"id": workedit.new_id("gb"), "type": kind}
        images: list[str] = []
        if kind == "section":
            block.update(title=_str(raw, "title", required=True, max_len=_MAX_NAME), collapsed=_flag(raw, "collapsed"))
        elif kind == "heading":
            block.update(text=_str(raw, "text", required=True, max_len=_MAX_NAME), level=_level(raw))
        elif kind == "text":
            block["text"] = _str(raw, "text", required=True, max_len=_MAX_TEXT)
        elif kind == "grid":
            block.update(imageIds=[], cols=_int(raw, "cols", 3, 2, 8))
            if _flag(raw, "collapsed"):
                block["collapsed"] = True
            if "image_ids" in raw:
                images = self._images(raw)
        elif kind == "meta":
            block["fields"] = copy.deepcopy(_META_FIELDS)
            if raw.get("grid") is not None:
                block["gridId"] = self._grid(raw)["id"]
        ref = _str(raw, "ref", max_len=100)
        if ref and (ref in self.refs or any(b.get("id") == ref for b in gallery_layout.blocks(self.doc))):
            raise ToolError(f"The ref “{ref}” is taken already.")
        rest, at = self._landing(raw, [], kind == "section")
        before = next((b["id"] for b in rest[at:] if b.get("id")), None)
        self._run({"op": "gallery_add", "block": block, "before": before})
        if ref:
            self.refs[ref] = block["id"]
        self.created.append({"id": block["id"], "type": kind, **({"ref": ref} if ref else {})})
        self.counts["added"] += 1
        if images:
            self._run({"op": "gallery_place", "grid": block["id"], "image_ids": images, "order": images})
            self.counts["placed"] += len(images)

    def update(self, raw: dict) -> None:
        found = self._block(raw)
        kind = found.get("type")
        if kind == "grid" and "image_ids" in raw:
            raise ToolError("Images go into a grid with place.")
        self._only(raw, (_GALLERY_FIELDS.get(kind, set()) - {"image_ids"}) | {"block"}, f"a {kind}")
        fields: dict = {}
        if "title" in raw:
            fields["title"] = _str(raw, "title", required=True, max_len=_MAX_NAME)
        if "text" in raw:
            fields["text"] = _str(raw, "text", required=True, max_len=_MAX_NAME if kind == "heading" else _MAX_TEXT)
        if "level" in raw:
            fields["level"] = _level(raw)
        if "cols" in raw:
            fields["cols"] = _int(raw, "cols", 3, 2, 8)
        if "collapsed" in raw:
            fields["collapsed"] = _flag(raw, "collapsed")
        if "grid" in raw:  # null: summarise the whole gallery again
            fields["gridId"] = self._grid(raw)["id"] if raw["grid"] is not None else None
        if not fields:
            raise ToolError("Nothing to update — give the fields to change.")
        self._run({"op": "gallery_set", "id": found["id"], "set": fields})
        self.counts["updated"] += 1

    def move(self, raw: dict) -> None:
        self._only(raw, {"block", "before", "after"}, "move")
        found = self._block(raw)
        stack = gallery_layout.blocks(self.doc)
        at = next(i for i, b in enumerate(stack) if b is found)
        moving = stack[at:gallery_layout.span(stack, at)]  # a section takes the blocks under it along
        section = found.get("type") == "section"
        rest, at = self._landing(raw, moving, section)
        if section and at < len(rest) and rest[at].get("type") != "section" and any(
                b.get("type") == "section" for b in rest[:at]):
            raise ToolError("A section moves before or after another section, not in among its blocks.")
        order = [b["id"] for b in [*rest[:at], *moving, *rest[at:]] if b.get("id")]
        self._run({"op": "gallery_order", "order": order})
        self.counts["moved"] += 1

    def remove(self, raw: dict) -> None:
        self._only(raw, {"block"}, "remove")
        self._run({"op": "gallery_remove", "id": self._block(raw)["id"]})
        self.counts["removed"] += 1

    def place(self, raw: dict) -> None:
        self._only(raw, {"image_ids", "grid", "index"}, "place")
        images = self._images(raw)
        grid = self._grid(raw)
        moving = set(images)
        rest = [i for i in grid["imageIds"] if i in self.in_gallery and i not in moving]  # as the app shows the album
        at = _int(raw, "index", len(rest), 0, 100_000)
        self._run({"op": "gallery_place", "grid": grid["id"], "image_ids": images,
                   "order": [*rest[:at], *images, *rest[at:]]})
        self.counts["placed"] += len(images)

    def unplace(self, raw: dict) -> None:
        self._only(raw, {"image_ids"}, "unplace")
        images = self._images(raw)
        self._run({"op": "gallery_unplace", "image_ids": images})
        self.counts["unplaced"] += len(images)

    def cover(self, raw: dict) -> None:
        self._only(raw, {"image_id"}, "cover")
        image_id = _str(raw, "image_id", required=True)
        if image_id not in self.in_gallery:
            raise ToolError(f"No gallery image “{image_id}” in this work.")
        self._run({"op": "cover", "image_id": image_id})
        self.counts["cover"] = 1

    def summary(self) -> str:
        c = self.counts
        parts = [f"{_plural(c[k], 'block')} {k}" for k in ("added", "updated", "moved", "removed") if c[k]]
        if c["placed"]:
            parts.append(f"{_plural(c['placed'], 'image')} placed")
        if c["unplaced"]:
            parts.append(f"{_plural(c['unplaced'], 'image')} to Quick access")
        if c["cover"]:
            parts.append("cover set")
        return ", ".join(parts)


_ARRANGE: dict[str, Callable[[_Arrange, dict], None]] = {
    "add": _Arrange.add, "update": _Arrange.update, "move": _Arrange.move, "remove": _Arrange.remove,
    "place": _Arrange.place, "unplace": _Arrange.unplace, "cover": _Arrange.cover,
}


def _edit_gallery(settings: Settings, args: dict) -> Written | dict:
    raw_ops = _items(args, "ops", _MAX_GALLERY_OPS)
    done: list[_Arrange] = []

    def change(doc: WorkDoc) -> list[dict]:
        was = json.dumps([gallery_layout.stack(doc), doc.preview_image_id])
        had = len(gallery_layout.stack(doc))
        edit = _Arrange(doc)
        done[:] = [edit]
        for n, raw in enumerate(raw_ops, 1):
            op = raw.get("op")
            step = _ARRANGE.get(op) if isinstance(op, str) else None
            try:
                if step is None:
                    raise ToolError(f"'op' must be one of: {', '.join(_ARRANGE)}.")
                step(edit, raw)
            except ToolError as exc:
                raise ToolError(f"Nothing changed — op {n} ({op}): {exc}") from None
        if len(gallery_layout.stack(doc)) > max(_MAX_GALLERY_BLOCKS, had):  # one the user grew bigger can still shrink
            raise ToolError(f"Nothing changed — a gallery holds at most {_MAX_GALLERY_BLOCKS} blocks.")
        return edit.ops if json.dumps([gallery_layout.stack(doc), doc.preview_image_id]) != was else []

    doc, ops = workedit.edit_work(settings, _str(args, "work_id", required=True), change)
    edit = done[0]
    result = {"created": edit.created, "gallery": _gallery_view(doc)}
    if not ops:
        return result
    return Written(result, "Arranged the gallery", f"of {workedit.title_of(doc)} · {edit.summary()}", work_id=doc.id)


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
        combo = Combo(id=workedit.new_id("combo"), name=_unique_name(name, {c.name for c in doc.combos}), blocks=blocks,
                      origin="agent")
        doc.combos.insert(0, combo)  # newest first, as the widget lists them
        made[:] = [combo]
        return [{"op": "combo", "combo": combo}]

    doc, _ = workedit.edit_work(settings, _str(args, "work_id", required=True), change)
    combo = made[0]
    return Written({"id": combo.id, "name": combo.name, "blocks": len(combo.blocks)}, "Saved a combination",
                   f"“{combo.name}” in {workedit.title_of(doc)}", work_id=doc.id)


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

    doc, _ = workedit.edit_work(settings, _str(args, "work_id", required=True), change)
    combo = edited[0]
    return Written({"id": combo.id, "name": combo.name, "blocks": len(combo.blocks)}, "Edited a combination",
                   f"“{combo.name}” in {workedit.title_of(doc)}", work_id=doc.id)


def _delete_combination(settings: Settings, args: dict) -> Written:
    combo_id = _str(args, "combination_id", required=True)
    gone: list[str] = []

    def change(doc: WorkDoc) -> list[dict]:
        gone[:] = [_find_combo(doc, combo_id).name]
        doc.combos = [c for c in doc.combos if c.id != combo_id]
        return [{"op": "combo_deleted", "id": combo_id}]

    doc, _ = workedit.edit_work(settings, _str(args, "work_id", required=True), change)
    return Written({"deleted": combo_id}, "Deleted a combination", f"“{gone[0]}” from {workedit.title_of(doc)}",
                   work_id=doc.id)


# ---- generation (generation.py: the Anlas rules, one at a time) ----
_GEN_PARAMS = ("model", "width", "height", "steps", "scale", "sampler", "n_samples", "noise_schedule", "cfg_rescale",
               "quality_toggle", "uc_preset")


def _recipe(settings: Settings, doc: WorkDoc, args: dict) -> tuple[list[ComboBlock], str | None]:
    """The blocks to generate from — a combination of the work, or blocks given in the call — and the combination's
    name. Frozen and empty blocks stay out of the prompt, as at the station."""
    combo_id = _str(args, "combination_id")
    if combo_id and "blocks" in args:
        raise ToolError("Give either 'combination_id' or 'blocks', not both.")
    if combo_id:
        combo = _find_combo(doc, combo_id)
        blocks, name = combo.blocks, combo.name
    elif "blocks" in args:
        blocks, name = _combo_blocks(settings, args), None
    else:
        raise ToolError("Give a 'combination_id' from this work, or 'blocks' to generate from.")
    live = [b.model_copy(update={"text": b.text.strip()}) for b in blocks if not b.frozen and b.text.strip()]
    if not any(b.polarity == "positive" for b in live):
        raise ToolError("Nothing to generate: the blocks hold no positive prompt.")
    return live, name


def _preset(settings: Settings, args: dict) -> PresetParams | None:
    if not (preset_id := _str(args, "preset_id")):
        return None
    found = next((p for p in presets.list_presets(settings) if p.id == preset_id), None)
    if found is None:
        raise ToolError(f"No preset “{preset_id}” — list_presets has them.")
    return found.params


def _dedupe(doc: WorkDoc, preset: PresetParams | None, args: dict) -> bool:
    """The station's "unique tags": the call's choice, else the preset's, else the work's."""
    if "dedupe" in args:
        return _flag(args, "dedupe")
    return preset.dedupe if preset is not None else bool(doc.params.get("dedupe"))


def _gen_params(doc: WorkDoc, args: dict, prompt: str, negative: str = "",
                preset: PresetParams | None = None) -> GenerateParams:
    """The work's generation settings (the station's), a preset's over them, the call's own on top — except the
    image count: one image unless the call asks for more (a station's or preset's count would quietly multiply
    the cost)."""
    values = {k: doc.params[k] for k in _GEN_PARAMS if k in doc.params and k != "n_samples"}
    if preset is not None:
        values.update(preset.model_dump(include=set(_GEN_PARAMS) - {"n_samples"}))
    values.update({k: args[k] for k in (*_GEN_PARAMS, "seed") if k in args})
    try:
        params = GenerateParams(prompt=prompt, negative_prompt=negative, **values)
    except ValidationError as exc:
        err = exc.errors()[0]
        raise ToolError(f"Bad generation setting '{err['loc'][0]}': {err['msg']}.") from exc
    if params.model not in {m.id for m in get_catalog().models}:
        raise ToolError(f"Unknown model “{params.model}” — get_catalog lists them.")
    return params


_MAX_SEED = 4_294_967_295


def _recipe_params(doc: WorkDoc, args: dict, blocks: list[ComboBlock], preset: PresetParams | None,
                   dedupe: bool) -> GenerateParams:
    """The prompt joined (and its tags merged, with "unique tags") as the station does it, and a seed fixed here so
    every image's recipe reproduces — image k gets seed + k, which must stay a valid seed too."""
    def join(polarity: str) -> str:
        joined = ", ".join(b.text for b in blocks if b.polarity == polarity)
        return dedupe_prompt(joined) if dedupe else joined

    params = _gen_params(doc, args, join("positive"), join("negative"), preset)
    if params.seed is None:
        return params.model_copy(update={"seed": secrets.randbelow(_MAX_SEED - params.n_samples + 2)})
    if params.seed + params.n_samples - 1 > _MAX_SEED:
        raise ToolError(f"With {params.n_samples} images the seed can be at most {_MAX_SEED - params.n_samples + 1}.")
    return params


def _client(settings: Settings) -> generation.Client:
    client = get_client(settings)
    if client.is_mock and not settings.novelai.mock:  # no token: the app would only make placeholder images
        raise ToolError("No NovelAI token is set in the app — ask the user to add it in Settings → NovelAI.")
    return client


def _has_opus(client: generation.Client, *, for_estimate: bool = False) -> bool:
    try:
        return generation.has_opus(client, for_estimate=for_estimate)
    except (NovelAIError, httpx.HTTPError) as exc:
        raise ToolError(f"Could not read the NovelAI subscription: {exc}") from exc


def _cost_of(params: GenerateParams, opus: bool) -> int:
    return anlas_cost(params.width, params.height, params.steps, params.n_samples, opus=opus)


def _estimate_cost(settings: Settings, args: dict) -> dict:
    work_id = _str(args, "work_id")
    doc = service.load_work(settings, work_id) if work_id else WorkDoc(id="-")
    params = _gen_params(doc, args, prompt="-", preset=_preset(settings, args))  # the cost doesn't depend on the prompt
    opus = _has_opus(_client(settings), for_estimate=True)
    cost = _cost_of(params, opus)
    cfg = store.load_config(settings)
    refused = generation.refusal(cost, True, cfg, opus) if cost else None
    return {"anlas": cost, "free": cost == 0, "params": _shown_params(params), "paid_generations": cfg.spending.paid,
            "max_per_request": cfg.spending.max_per_request, "daily_budget": cfg.spending.daily_budget,
            "spent_today": store.spent_today(cfg), "refused_even_with_paid": refused[0] if refused else None}


# The station's settings an agent may set — all but the seed (each generation picks its own unless the user fixes one).
_STATION_KEYS = ("model", "width", "height", "steps", "scale", "sampler", "n_samples", "noise_schedule", "cfg_rescale",
                 "quality_toggle", "uc_preset", "dedupe")


def _station_check(values: dict, given: set[str]) -> dict:
    """The station's settings, typed (strictly: no "832" for 832) — and the ones being set checked against what the
    app offers for the model. What the station holds already stays as it is, as the app's own settings panel keeps a
    value the model doesn't offer (marked unavailable) rather than dropping it."""
    try:
        p = GenerateParams.model_validate({"prompt": "-", **{k: values[k] for k in _GEN_PARAMS if k in values}},
                                          strict=True)
    except ValidationError as exc:
        err = exc.errors()[0]
        raise ToolError(f"Bad generation setting '{err['loc'][0]}': {err['msg']}.") from exc
    catalog = get_catalog()
    spec = next((m for m in catalog.models if m.id == p.model), None)
    if "model" in given and spec is None:
        raise ToolError(f"Unknown model “{p.model}” — get_catalog lists them.")
    step = catalog.dim_limits.step
    if any(k in given and getattr(p, k) % step for k in ("width", "height")):
        raise ToolError(f"Width and height must be multiples of {step}.")
    for key, label in (("steps", "steps"), ("scale", "a scale")):
        span = getattr(spec, key) if spec else None
        if key in given and span and not span.min <= getattr(p, key) <= span.max:
            raise ToolError(f"{spec.label} takes {label} of {span.min:g}–{span.max:g}.")
    samplers = spec.samplers if spec else [s.id for s in catalog.samplers]  # an unknown model: the app's whole list
    if "sampler" in given and p.sampler not in samplers:
        raise ToolError(f"No sampler “{p.sampler}” for this model — get_catalog lists them.")
    if "noise_schedule" in given and p.noise_schedule not in {n.value for n in catalog.noise_schedules}:
        raise ToolError(f"Unknown noise schedule “{p.noise_schedule}”.")
    uc_presets = spec.uc_presets if spec else [u.value for u in catalog.uc_presets]
    if "uc_preset" in given and p.uc_preset not in uc_presets:
        raise ToolError(f"No UC preset {p.uc_preset} for this model — get_catalog lists them.")
    return {**p.model_dump(include=set(_GEN_PARAMS)), "dedupe": _flag(values, "dedupe")}


def _shown(value: object) -> str:
    return ("on" if value else "off") if isinstance(value, bool) else str(value)


def _update_work_params(settings: Settings, args: dict) -> Written | dict:
    preset_id = _str(args, "preset_id")
    preset = _preset(settings, args)
    given = {k: args[k] for k in _STATION_KEYS if k in args}
    if preset is None and not given:
        raise ToolError("Give the settings to change, or a preset_id.")
    new = {**(preset.model_dump() if preset else {}), **given}  # as the app applies a preset, the call's on top
    changed: dict = {}

    def change(doc: WorkDoc) -> list[dict]:
        station = {k: doc.params[k] for k in _STATION_KEYS if k in doc.params}
        checked = _station_check({**station, **new}, set(new))
        changed.clear()
        changed.update({k: checked[k] for k in new if doc.params.get(k) != checked[k]})
        if not changed:
            return []
        doc.params.update(changed)
        if preset is None:
            return [{"op": "params", "set": dict(changed)}]
        # A preset lands whole in the open app, as its Apply does — over the user's unsaved station edits too.
        return [{"op": "params", "set": {k: checked[k] for k in new}, "preset_id": preset_id}]

    doc, ops = workedit.edit_work(settings, _str(args, "work_id", required=True), change)
    result = {"work_id": doc.id, "params": _recipe_of(doc.params), "changed": list(changed)}
    if not ops:
        return result
    names = presets.list_presets(settings) if preset else []
    via = next((f" from “{p.name}”" for p in names if p.id == preset_id), "")
    shown = ", ".join(f"{k} {_shown(v)}" for k, v in changed.items())  # values: some raise what Generate costs
    return Written(result, "Changed the generation settings", f"of {workedit.title_of(doc)}{via} · {shown}",
                   work_id=doc.id)


def _shown_params(params: GenerateParams) -> dict:
    return {k: getattr(params, k) for k in ("model", "width", "height", "steps", "n_samples")}


def _rescue(settings: Settings, pngs: list[bytes]) -> Path:
    """Paid-for images that could not be saved into the work go to the Downloads folder instead of being lost."""
    dest = Path(appconfig.load(settings)["download_dir"])
    dest.mkdir(parents=True, exist_ok=True)
    stamp = int(time.time())
    for k, png in enumerate(pngs, start=1):
        (dest / f"novelai-agent-{stamp}-{k}.png").write_bytes(png)
    return dest


def _send(settings: Settings, client: generation.Client, params: GenerateParams, cost: int) -> list[bytes]:
    """Send the generation. Its cost is counted before it goes out — an answer lost on the way may still have
    been charged — and given back only when NovelAI clearly refused it."""
    if cost:
        store.add_spent(settings, cost)
    rate_limited = False
    try:
        return anyio.from_thread.run(client.generate, params)
    except NovelAIError as exc:
        rate_limited = isinstance(exc, NovelAIRateLimitError)
        refused = exc.http_status in (400, 401, 402, 429)
        if cost and refused:
            store.add_spent(settings, -cost)
        busy = " (another generation may be running on the account — the app's own, too)" if rate_limited else ""
        raise GenerationFailed(f"{exc}{busy}", 0 if refused else cost) from exc
    except (httpx.HTTPError, ValueError, zipfile.BadZipFile) as exc:  # no answer, or no readable images in it
        raise GenerationFailed(f"NovelAI request failed: {exc} — it may still have been charged.", cost) from exc
    finally:
        generation.finished(rate_limited=rate_limited)  # also: the balance moved, a cached subscription is stale


def _generate_images(settings: Settings, args: dict) -> Written:
    work_id = _str(args, "work_id", required=True)
    asked_to_pay = _flag(args, "paid")
    doc = service.load_work(settings, work_id)
    blocks, combo_name = _recipe(settings, doc, args)
    preset = _preset(settings, args)
    dedupe = _dedupe(doc, preset, args)
    params = _recipe_params(doc, args, blocks, preset, dedupe)
    client = _client(settings)
    with generation.slot():
        cfg = store.load_config(settings)
        if not (cfg.enabled and cfg.permissions.generate):  # switched off since this call was let in
            raise Refused("“Generate images” is turned off in Settings → Agents.", "switched off meanwhile")
        opus = _has_opus(client)  # read now: the account may have changed since the last generation
        cost = _cost_of(params, opus)
        if cost and (why := generation.refusal(cost, asked_to_pay, cfg, opus)):
            raise Refused(*why)
        pngs = _send(settings, client, params, cost)

    components = [Component(**b.model_dump(exclude={"frozen"})) for b in blocks]
    recipe = {**params.model_dump(exclude={"prompt", "negative_prompt"}), "dedupe": dedupe}
    made: list[Image] = []

    def change(work: WorkDoc) -> list[dict]:
        snaps = [Snapshot(id=workedit.new_id("snap"), hash="|".join(f"{c.polarity}:{c.text}" for c in components),
                          components=components, assembled_positive=params.prompt,
                          assembled_negative=params.negative_prompt, params={**recipe, "seed": params.seed + k})
                 for k in range(len(pngs))]  # NovelAI makes sample k from seed + k
        made[:] = [Image(id=workedit.new_id("img"), snapshot_id=s.id, role="gallery", ar=params.width / params.height,
                         image_b64=base64.b64encode(png).decode("ascii")) for s, png in zip(snaps, pngs)]
        work.snapshots += snaps
        work.images += made  # in no grid yet: they show in the gallery's Quick access
        work.preview_image_id = work.preview_image_id or made[0].id
        return [{"op": "images_added", "images": made, "snapshots": snaps}]

    try:
        doc, _ = workedit.edit_work(settings, work_id, change)
    except (HTTPException, OSError) as exc:  # deleted meanwhile, a vault move, a full disk, …
        where = _rescue(settings, pngs)
        reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
        raise GenerationFailed(f"Generated, but saving into the work failed ({reason}) — the images were saved to "
                               f"{where} instead.", cost) from exc
    detail = f"in {workedit.title_of(doc)}" + (f" from “{combo_name}”" if combo_name else "")
    if cost:
        detail += f" · {params.width}×{params.height}, {params.steps} steps"
    result = {"work_id": doc.id, "images": [{"id": im.id, "seed": params.seed + k} for k, im in enumerate(made)],
              "params": _shown_params(params), "anlas": cost, "spent_today": store.spent_today(store.load_config(settings))}
    return Written(result, f"Generated {_plural(len(made), 'image')}", detail, work_id=doc.id, cost=cost)


_GEN_FIELDS = {
    "model": {"type": "string", "description": "a model id from get_catalog; default: the work's"},
    "width": {"type": "integer", "minimum": 64, "maximum": 2048}, "height": {"type": "integer", "minimum": 64, "maximum": 2048},
    "steps": {"type": "integer", "minimum": 1, "maximum": 50}, "scale": {"type": "number", "minimum": 0, "maximum": 10},
    "sampler": {"type": "string"},
    "n_samples": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1,
                  "description": "images in this one generation; only the first can be free"},
    "noise_schedule": {"type": "string", "enum": [n.value for n in get_catalog().noise_schedules]},
    "cfg_rescale": {"type": "number", "minimum": 0, "maximum": 1},
    "quality_toggle": {"type": "boolean"}, "uc_preset": {"type": "integer", "minimum": 0, "maximum": 7},
    "dedupe": {"type": "boolean",
               "description": "“unique tags”: merge repeated tags, summing their weights; default: the preset's or "
                              "the work's"},
    "preset_id": {"type": "string",
                  "description": "a generation preset (list_presets): its settings apply over the work's, the "
                                 "fields given here over both; its image count is not used"},
}
_TAG_FILTER = {"type": "array", "items": {"type": "string"}, "maxItems": _MAX_TAGS, "description": "all of these"}
_GALLERY_REF = {"type": "string", "description": "a gallery block's id (get_gallery), or a ref given earlier in this call"}


_BLOCK_FIELDS = {
    "category": {"type": "string", "description": "category slug or display name (list_categories); a name the "
                                                  "Library doesn't have yet creates that category; default Custom"},
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
    Tool("list_works", "List works",
         "Works in the vault, most recently updated first. 'query' finds a work by its title or what it holds — its "
         "gallery's section titles, headings and notes, its images' prompts, descriptions and tags; a work found by "
         "what it holds says where in 'match' (a section, heading or note, or how many of its images).",
         {"type": "object", "properties": {"query": {"type": "string"}, **_PAGING}}, "read", _list_works),
    Tool("get_work", "Get a work",
         "One work's overview: title, its generation settings (the station's), cover image, saved block "
         "combinations (names — list_combinations has their blocks), starred Library blocks and how many gallery "
         "images it has. The images themselves: list_images; how the gallery arranges them: get_gallery.",
         {"type": "object", "properties": {"work_id": {"type": "string"}}, "required": ["work_id"]},
         "read", _get_work),
    Tool("list_images", "List a work's images",
         "A work's gallery images, newest first, paged: metadata and the full recipe (model, size, steps, seed, "
         "sampler, quality tags, UC preset, …) of each, and the combinations of the work it was made from (those "
         "whose blocks are its prompt's — a combination edited since no longer matches). Tags match the image's own "
         "and those of the blocks it was made from (block_tags). include_prompt adds the prompt and its blocks — "
         "pages are smaller then.",
         {"type": "object", "properties": {
             "work_id": {"type": "string"}, "tags": _TAG_FILTER, "favorite": {"type": "boolean", "default": False},
             "combination_id": {"type": "string", "description": "only images made from this combination"},
             "include_prompt": {"type": "boolean", "default": False},
             "page": _PAGING["page"],
             "per_page": {"type": "integer", "minimum": 1, "maximum": MAX_IMAGES_PAGE,
                          "description": f"default 50; with include_prompt: default 10, at most {MAX_PROMPTS_PAGE}"}},
          "required": ["work_id"]},
         "read", _list_images),
    Tool("search_images", "Search images",
         "Gallery images across all works by tags (all of them) and/or favourite, newest first, paged — with "
         "the work each one is in.",
         {"type": "object", "properties": {"tags": _TAG_FILTER, "favorite": {"type": "boolean", "default": False},
                                           **_PAGING}},
         "read", _search_images),
    Tool("get_image", "View an image",
         "A downscaled PNG of one image of a work, to look at it (e.g. before describing or tagging it). With "
         "'crop', a region of it at the image's own resolution — a close look at small details.",
         {"type": "object", "properties": {
             "work_id": {"type": "string"}, "image_id": {"type": "string"},
             "width": {"type": "integer", "minimum": 64, "maximum": 1024, "default": 512,
                       "description": "the largest width sent back"},
             "crop": {"type": "object", "description": "a region in the image's pixels", "properties": {
                 "x": {"type": "integer", "minimum": 0}, "y": {"type": "integer", "minimum": 0},
                 "width": {"type": "integer", "minimum": 16}, "height": {"type": "integer", "minimum": 16}},
                 "required": ["x", "y", "width", "height"]}},
          "required": ["work_id", "image_id"]},
         "read", _get_image),
    Tool("contact_sheet", "View images side by side",
         f"Up to {_SHEET_MAX} gallery images of a work in one numbered picture (default: the newest "
         f"{_SHEET_DEFAULT}) — to compare or pick images in one look; the text that comes with it says which "
         "number is which image id.",
         {"type": "object", "properties": {
             "work_id": {"type": "string"},
             "image_ids": {"type": "array", "items": {"type": "string"}, "maxItems": _SHEET_MAX},
             "cols": {"type": "integer", "minimum": 1, "maximum": 8, "default": 6},
             "tile": {"type": "integer", "minimum": 64, "maximum": 320, "default": 192,
                      "description": "each image's width in px"}},
          "required": ["work_id"]},
         "read", _contact_sheet),
    Tool("get_gallery", "Get a work's gallery layout",
         "How a work's gallery arranges its images, as the app shows it: the block stack in order — sections (a "
         "section holds the blocks under it, up to the next one), headings, text, dividers, metadata summaries and "
         "image grids (albums: their images in order, a column count) — then Quick access (gallery images in no grid, "
         "newest first) and the cover image (the work's picture in the Works list).",
         {"type": "object", "properties": {"work_id": {"type": "string"}}, "required": ["work_id"]},
         "read", _get_gallery),
    Tool("get_catalog", "Model catalog",
         "NovelAI models, samplers, noise schedules, UC presets and resolution presets the app knows.",
         {"type": "object", "properties": {}}, "read", _get_catalog),
    Tool("list_combinations", "List combinations",
         "A work's saved block combinations (ready block sets for the generation station), newest first.",
         {"type": "object", "properties": {"work_id": {"type": "string"}}, "required": ["work_id"]},
         "read", _list_combinations),
    Tool("list_presets", "List generation presets",
         "The user's generation presets (built-in and their own): settings, which one is the default and which "
         "are favourites. Pass a preset_id to generate_images or estimate_cost to use one.",
         {"type": "object", "properties": {}}, "read", _list_presets),
    Tool("get_account", "NovelAI account",
         "The user's NovelAI subscription: tier, remaining Anlas, what generates for free — and what agents "
         "spent today against the user's daily budget.",
         {"type": "object", "properties": {}}, "read", _get_account),
    Tool("count_tokens", "Count prompt tokens",
         "How many tokens a prompt takes for a model, with the quality tags and UC preset generation adds — "
         "against the model's limits (a longer prompt gets cut).",
         {"type": "object", "properties": {
             "model": {"type": "string", "description": "a model id from get_catalog; default: the app's"},
             "positive": {"type": "string", "maxLength": _MAX_PROMPT},
             "negative": {"type": "string", "maxLength": _MAX_PROMPT},
             "quality_toggle": {"type": "boolean", "default": True},
             "uc_preset": {"type": "integer", "minimum": 0, "maximum": 7, "default": 4},
             "dedupe": {"type": "boolean", "default": False,
                        "description": "count it as “unique tags” sends it (repeated tags merged)"}}},
         "read", _count_tokens),
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
    Tool("edit_gallery", "Arrange a work's gallery",
         "Arrange a work's gallery (get_gallery shows it). The ops run in order; if one fails, nothing changes. A "
         "block is named by its id, or by the 'ref' an earlier add of this call gave it. Ops:\n"
         "- add {type, ref?, before|after?}: section {title, collapsed?}, heading {text, level 1|2}, text {text}, "
         "divider, grid {cols 2–8 (default 3), image_ids?, collapsed?}, meta {grid?: summarise that grid — default the "
         "whole gallery}; at the end unless placed. Blocks after a section belong to it, up to the next one — so a "
         "section added among another's blocks takes the ones after it; after a section, a section lands past its "
         "blocks.\n"
         "- update {block, …}: title, text, level, cols, collapsed or grid (null: the whole gallery) — as its type has.\n"
         "- move {block, before|after?}: a section moves with the blocks under it, before or after another section "
         "(after one: past its blocks).\n"
         "- remove {block}: a grid's images go back to Quick access; a section's blocks join the one above.\n"
         "- place {image_ids, grid, index?}: into that grid, out of any other or Quick access, at that position among "
         "its other images (default: the end) — also how to reorder a grid.\n"
         "- unplace {image_ids}: back to Quick access.\n"
         "- cover {image_id}: the work's cover.\n"
         "Returns the new blocks' ids and the gallery as get_gallery shows it.",
         {"type": "object", "properties": {"work_id": {"type": "string"}, "ops": {
             "type": "array", "minItems": 1, "maxItems": _MAX_GALLERY_OPS, "items": {"type": "object", "properties": {
                 "op": {"type": "string", "enum": list(_ARRANGE)},
                 "type": {"type": "string", "enum": list(_GALLERY_TYPES)},
                 "block": _GALLERY_REF, "before": _GALLERY_REF, "after": _GALLERY_REF,
                 "ref": {"type": "string", "maxLength": 100,
                         "description": "add: a name for the new block that later ops of this call can use"},
                 "title": {"type": "string", "maxLength": _MAX_NAME}, "text": {"type": "string", "maxLength": _MAX_TEXT},
                 "level": {"type": "integer", "enum": [1, 2]}, "cols": {"type": "integer", "minimum": 2, "maximum": 8},
                 "collapsed": {"type": "boolean"},
                 "grid": {"type": ["string", "null"], "description": "a grid's id or ref"},
                 "image_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_PAGE},
                 "index": {"type": "integer", "minimum": 0}, "image_id": {"type": "string"}},
                 "required": ["op"]}}},
          "required": ["work_id", "ops"]},
         "works_write", _edit_gallery),
    Tool("update_work_params", "Set a work's generation settings",
         "Set the generation settings a work keeps — the station's: what the user generates with when they open the "
         "work, and what generate_images and estimate_cost start from. A preset (preset_id) applies as the app's "
         "Apply does, image count included, and the fields given go on top. Checked against what the model offers "
         "(get_catalog). The seed is not set here.",
         {"type": "object", "properties": {
             "work_id": {"type": "string"},
             **{k: v for k, v in _GEN_FIELDS.items() if k in _STATION_KEYS},
             "model": {"type": "string", "description": "a model id from get_catalog"},
             "n_samples": {"type": "integer", "minimum": 1, "maximum": 4, "description": "images per generation"},
             "preset_id": {"type": "string", "description": "a generation preset (list_presets) to apply"}},
          "required": ["work_id"]},
         "works_write", _update_work_params),
    Tool("star_blocks", "Star blocks in a work",
         "Star Library blocks in a work — the work's quick picks in the app's prompt widget (get_work lists them as "
         "favorite_block_ids) — or unstar them with starred: false.",
         {"type": "object", "properties": {
             "work_id": {"type": "string"},
             "block_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_BATCH},
             "starred": {"type": "boolean", "default": True}},
          "required": ["work_id", "block_ids"]},
         "works_write", _star_blocks),
    Tool("move_images", "Move images to another work",
         "Move gallery images to another work — or copy them (copy: true) — each with its recipe, tags, description "
         "and favourite flag. They get new ids (returned) and land in that work's Quick access.",
         {"type": "object", "properties": {
             "from_work_id": {"type": "string"}, "to_work_id": {"type": "string"},
             "image_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": _MAX_MOVE},
             "copy": {"type": "boolean", "default": False}},
          "required": ["from_work_id", "to_work_id", "image_ids"]},
         "works_write", _move_images),
    Tool("delete_images", "Delete images", "Remove gallery images from a work for good (files included).",
         {"type": "object", "properties": {"work_id": {"type": "string"}, "image_ids": {
             "type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": MAX_PAGE}},
          "required": ["work_id", "image_ids"]},
         "works_delete", _delete_images),
    Tool("delete_work", "Delete a work",
         "Delete a whole work for good: its images and their files, combinations and layout. If the user has it open, "
         "the app lets it go — changes they hadn't saved stay with them, as a new work.",
         {"type": "object", "properties": {"work_id": {"type": "string"}}, "required": ["work_id"]},
         "works_delete", _delete_work),
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
    # generation
    Tool("estimate_cost", "Estimate a generation's cost",
         "The Anlas a generation would cost with these settings (default: the work's, or a preset's), whether it is "
         "free, and the user's limits for paid agent generations.",
         {"type": "object", "properties": {"work_id": {"type": "string"}, **_GEN_FIELDS}}, "read", _estimate_cost),
    Tool("generate_images", "Generate images",
         "Generate with NovelAI from a combination of the work (combination_id) or from blocks, with the work's "
         "generation settings — or a preset's (preset_id) — unless overridden; one image unless n_samples says "
         "more. The prompt is built as the station builds it, “unique tags” included. The images land in the work's gallery (Quick access) with their recipe. Free "
         "generations (Opus: up to 1024×1024 px, 28 steps, one image) always run; a paid one runs only when the user "
         "agreed to spend Anlas on it — then pass paid: true — and allows paid ones in Settings, within their limits. "
         "One generation at a time, a few seconds apart: for a series, call again for each.",
         {"type": "object", "properties": {
             "work_id": {"type": "string"}, "combination_id": {"type": "string"}, "blocks": _COMBO_BLOCKS,
             "seed": {"type": "integer", "minimum": 0, "maximum": 4_294_967_295}, **_GEN_FIELDS,
             "paid": {"type": "boolean", "default": False,
                      "description": "spend Anlas — only when the user asked for a paid (higher-quality) generation"}},
          "required": ["work_id"]},
         "generate", _generate_images),
]}


REACHES_NOVELAI = frozenset({"generate_images", "estimate_cost", "get_account"})  # the rest stays in the vault


def run(tool: Tool, settings: Settings, args: dict) -> Any:
    """Run a tool, turning service-level HTTP errors into agent-facing ToolErrors."""
    try:
        return tool.handler(settings, args)
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc


def image_block(result: ImageResult) -> dict:
    return {"type": "image", "mimeType": result.mime, "data": base64.b64encode(result.data).decode("ascii")}
