"""A work's gallery layout, as the backend reads and arranges it (agents, edits from the app's other views). The layout
is the gallery zone on the work's canvas — frontend-owned and stored opaquely (``frontend/src/types.ts``
``GalleryBlock``): a stack of typed blocks, where a section holds the blocks under it up to the next section, and a grid
is an album owning an ordered ``imageIds`` list. Each gallery image sits in one grid at most; the ones in no grid show in
Quick access.

``apply`` replays one gallery operation — the very ones the open app replays over its copy of the work
(``frontend/src/vault/agentOps.ts``), with the same rules, so what the backend reads back is what the app shows. A batch
replayed again changes nothing, and an operation about a block or an image that is gone is skipped.
"""
import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.vault.models import WorkDoc

ZONE_ID = "gallery"  # the app's gallery zone node (frontend vault/serialize.ts GALLERY)


def zone(doc: WorkDoc) -> dict | None:
    nodes = doc.canvas.get("nodes")
    if not isinstance(nodes, list):
        return None
    return next((n for n in nodes if isinstance(n, dict) and n.get("type") == "zone"
                 and isinstance(n.get("data"), dict) and n["data"].get("role") == "gallery"), None)


def _seed(doc: WorkDoc) -> list[dict]:
    """A gallery zone without a block stack shows one album of every gallery image, oldest first — as the app heals
    it (``workToCanvas``)."""
    ids = [im.id for im in sorted((im for im in doc.images if im.role == "gallery"), key=lambda im: im.created_at)]
    return [{"id": "gb-seed", "type": "grid", "imageIds": ids, "cols": 3}]


def stack(doc: WorkDoc) -> list[dict]:
    """The block stack as the app shows it (read-only use; ``blocks`` is the one to change)."""
    z = zone(doc)
    if z is None:
        return []
    blocks = z["data"].get("blocks")
    return [b for b in blocks if isinstance(b, dict)] if isinstance(blocks, list) else _seed(doc)


def blocks(doc: WorkDoc) -> list[dict]:
    """The block stack to change. A work that has no gallery zone yet (made outside the app and never opened there)
    gets one, unplaced: the app lays its widgets out around it the first time it opens the work."""
    z = zone(doc)
    if z is None:
        nodes = doc.canvas.get("nodes")
        if not isinstance(nodes, list):
            doc.canvas["nodes"] = nodes = []
        z = {"id": ZONE_ID, "type": "zone", "data": {"role": "gallery", "blocks": []}}
        nodes.append(z)
    data = z["data"]
    if not isinstance(data.get("blocks"), list):
        data["blocks"] = _seed(doc)
    stack = data["blocks"]
    stack[:] = [b for b in stack if isinstance(b, dict)]  # in place: a caller may hold this list across apply()
    for b in stack:
        if b.get("type") == "grid" and not isinstance(b.get("imageIds"), list):
            b["imageIds"] = []
    return stack


def grids(stack: list[dict]) -> list[dict]:
    return [b for b in stack if b.get("type") == "grid" and isinstance(b.get("imageIds"), list)]


def _text(value: object) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


@dataclass(frozen=True)
class Placement:
    """Where a gallery image sits: its album (``grid_id``; ``number`` — its place among the gallery's albums from 1,
    as the app's Outline names it "Grid N"), the nearest heading above the album within its section, the section."""

    grid_id: str
    number: int
    heading: str | None
    section: str | None


def placements(doc: WorkDoc) -> dict[str, Placement]:
    """Each placed gallery image's album, as the app shows the gallery: an image in two albums (a malformed copy)
    belongs to the first. Gallery images missing here are unsorted — in Quick access."""
    gallery_ids = {im.id for im in doc.images if im.role == "gallery"}
    out: dict[str, Placement] = {}
    section = heading = None
    number = 0
    for b in stack(doc):
        kind = b.get("type")
        if kind == "section":
            section, heading = _text(b.get("title")), None
        elif kind == "heading":
            heading = _text(b.get("text"))
        elif kind == "grid":
            number += 1
            gid, ids = b.get("id"), b.get("imageIds")
            for i in ids if isinstance(ids, list) else []:
                if isinstance(i, str) and i in gallery_ids and i not in out:  # a hand-edited copy may hold anything
                    out[i] = Placement(gid if isinstance(gid, str) and gid else f"grid-{number}", number, heading,
                                       section)
    return out


_WORDS = {"section": ("section", "title"), "heading": ("heading", "text"), "text": ("note", "text")}


def texts(doc: WorkDoc) -> list[tuple[str, str]]:
    """What the gallery says in words, in order: ("section" | "heading" | "note", text)."""
    out = []
    for b in stack(doc):
        kind = b.get("type")
        if isinstance(kind, str) and (words := _WORDS.get(kind)) and (text := _text(b.get(words[1]))):
            out.append((words[0], text))
    return out


def span(stack: list[dict], at: int) -> int:
    """Where the block at ``at`` ends: a section takes the blocks under it along, up to the next section."""
    if stack[at].get("type") != "section":
        return at + 1
    end = at + 1
    while end < len(stack) and stack[end].get("type") != "section":
        end += 1
    return end


def _insert(stack: list[dict], items: list[dict], before: str | None) -> None:
    at = next((i for i, b in enumerate(stack) if b.get("id") == before), len(stack)) if before else len(stack)
    stack[at:at] = items


def _reorder(items: list, order: list[str], key: Callable[[Any], Any]) -> None:
    """Put the listed items in this order, in the slots they hold; every other item keeps its slot. An item listed but
    held twice (a malformed copy) keeps one slot."""
    listed = set(order)
    present: dict = {}
    for x in items:
        present.setdefault(key(x), x)
    queue = [present[k] for k in dict.fromkeys(order) if k in present]
    out = []
    for x in items:
        if key(x) not in listed:
            out.append(x)
        elif queue:
            out.append(queue.pop(0))
    items[:] = out


def _unplace(stack: list[dict], ids: set[str], keep: dict | None = None) -> None:
    for g in grids(stack):
        if g is not keep:
            g["imageIds"] = [i for i in g["imageIds"] if i not in ids]


def apply(doc: WorkDoc, op: dict) -> None:
    """Replay one gallery operation on ``doc``:

    - ``gallery_add {block, before}`` — a new block, before that block (None: at the end); a grid comes empty.
    - ``gallery_set {id, set}`` — fields of a block; a None value removes the field.
    - ``gallery_remove {id}`` — a block; a grid's images go back to Quick access.
    - ``gallery_order {order}`` — the blocks listed, in this order (a move).
    - ``gallery_place {grid, image_ids, order}`` — gallery images into a grid, out of any other; the grid's images
      listed, in this order.
    - ``gallery_unplace {image_ids}`` — out of every grid, into Quick access.
    - ``cover {image_id}`` — the work's cover.

    An order sets where the listed things stand among each other, in the slots they hold — what the copy has besides
    keeps its place. So a whole batch replays to the same layout however often it runs.
    """
    kind = op["op"]
    gallery_ids = {im.id for im in doc.images if im.role == "gallery"}
    if kind == "cover":
        if op["image_id"] in gallery_ids:
            doc.preview_image_id = op["image_id"]
        return
    stack = blocks(doc)
    by_id: dict = {}
    for b in stack:
        by_id.setdefault(b.get("id"), b)
    if kind == "gallery_add":
        if op["block"]["id"] not in by_id:  # else: replayed
            _insert(stack, [copy.deepcopy(op["block"])], op.get("before"))
    elif kind == "gallery_set":
        if (block := by_id.get(op["id"])) is not None:
            for key, value in op["set"].items():
                if value is None:
                    block.pop(key, None)
                else:
                    block[key] = copy.deepcopy(value)
    elif kind == "gallery_remove":
        stack[:] = [b for b in stack if b.get("id") != op["id"]]
    elif kind == "gallery_order":
        _reorder(stack, op["order"], lambda b: b.get("id"))
    elif kind == "gallery_place":
        grid = by_id.get(op["grid"])
        if grid is None or grid.get("type") != "grid":
            return  # the grid is gone: the images stay where they are
        ids = [i for i in dict.fromkeys(op["image_ids"]) if i in gallery_ids]
        _unplace(stack, set(ids), keep=grid)
        album = grid["imageIds"]
        album += [i for i in ids if i not in album]
        _reorder(album, op["order"], lambda i: i)
    elif kind == "gallery_unplace":
        _unplace(stack, set(op["image_ids"]))
    else:
        raise ValueError(f"Unknown gallery operation {kind!r}")
