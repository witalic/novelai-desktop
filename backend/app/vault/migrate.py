"""Linear, read-time schema migrations for on-disk work documents.

Files are the truth and stay at their written version until the next save (lazy migration —
rewriting on read would collide with the autosave/atomic-write invariants). Both read paths,
``store.read_work`` and ``index.rebuild``, go through :func:`load_doc`, so a v1 work indexes
and loads identically. Each step is a plain ``dict -> dict`` function so it can never depend
on the current Pydantic models — it must keep working after the models move on.
"""
import json
import logging
from collections.abc import Callable

from app.vault.models import WorkDoc

log = logging.getLogger(__name__)

CURRENT = 6


def _migrate_1_to_2(raw: dict) -> dict:
    """v2: images gain a persisted aspect ratio (backfilled from their snapshot's generation
    params) and the transient block flags old builds leaked (``_cw``/``_ch``) are dropped.
    ``Image.role`` needs no backfill — the model default ``gallery`` is correct for v1 docs."""
    snap_params = {s.get("id"): (s.get("params") or {}) for s in raw.get("snapshots", [])}
    for im in raw.get("images", []):
        if im.get("ar") is None:
            p = snap_params.get(im.get("snapshot_id") or "", {})
            w, h = p.get("width"), p.get("height")
            if w and h:
                im["ar"] = w / h
    for node in (raw.get("canvas") or {}).get("nodes", []):
        data = node.get("data")
        if isinstance(data, dict):
            data.pop("_cw", None)
            data.pop("_ch", None)
    return raw


def _migrate_2_to_3(raw: dict) -> dict:
    """v3: the prompt widget drops its pinned-palette child nodes in favour of a per-work
    ``favorites`` list (Library block ids). Convert any library-zone pin that links a vault
    block into a favorite, then remove the now-defunct palette nodes from the canvas. Local
    palette customs (no ``block_id``) can't be favorited — they're dropped with the pins."""
    favs: list[str] = list(dict.fromkeys(raw.get("favorites") or []))
    canvas = raw.get("canvas") or {}
    kept = []
    for node in canvas.get("nodes", []):
        if node.get("type") == "block" and node.get("parentNode") == "library":
            bid = (node.get("data") or {}).get("block_id")
            if bid and bid not in favs:
                favs.append(bid)
            continue  # drop the palette node — the widget no longer renders pins
        kept.append(node)
    canvas["nodes"] = kept
    raw["canvas"] = canvas
    raw["favorites"] = favs
    return raw


def _migrate_3_to_4(raw: dict) -> dict:
    """v4: the station drops its free-placed blocks + positive/negative lanes for a two-zone layout
    (Generation | Composition) with an ordered block list. The station node's ``outputRatio`` becomes
    ``ratio`` (+ default ``axis``/``genFirst``); ``posRatio`` is gone. Composition blocks (children of
    ``station``) lose ``xFrac``/``laneFrac`` and get an order = ``position.y``, seeded from their old
    left-to-right x (the previous assembly order)."""
    nodes = (raw.get("canvas") or {}).get("nodes", [])
    for node in nodes:
        if node.get("type") == "station":
            data = node.get("data") or {}
            data.setdefault("ratio", data.get("outputRatio", 0.3))
            data.pop("outputRatio", None)
            data.pop("posRatio", None)
            data.setdefault("axis", "h")
            data.setdefault("genFirst", True)
            node["data"] = data
    comp = [n for n in nodes if n.get("type") == "block" and n.get("parentNode") == "station"]
    comp.sort(key=lambda n: (n.get("position") or {}).get("x", 0))
    for i, n in enumerate(comp):
        n["position"] = {"x": 0, "y": i * 10}
        d = n.get("data")
        if isinstance(d, dict):
            d.pop("xFrac", None)
            d.pop("laneFrac", None)
    return raw


def _migrate_4_to_5(raw: dict) -> dict:
    """v5: the gallery stops being a free-placed pile of image nodes and becomes a structured,
    composable widget — a stack of typed blocks stored on the gallery zone node's ``data.blocks``.
    Seed a single image-grid block (``source: all``) so a work's existing gallery-role images still
    show. Images themselves are untouched (their ``role: gallery`` already flags them); the now-inert
    free ``position`` on gallery image nodes is harmless and left as-is."""
    for node in (raw.get("canvas") or {}).get("nodes", []):
        if node.get("type") == "zone" and (node.get("data") or {}).get("role") == "gallery":
            data = node.get("data") or {}
            if not data.get("blocks"):
                data["blocks"] = [{"id": "gb-seed", "type": "grid", "source": "all", "cols": 3}]
            node["data"] = data
    return raw


def _migrate_5_to_6(raw: dict) -> dict:
    """v6: image grids become albums — each grid owns an ordered ``imageIds`` list instead of a
    shared ``source`` query, so every gallery image belongs to exactly one grid. Resolve each
    grid's old query against the gallery images (ordered by created_at) and assign each image to
    the FIRST grid whose query it matches (leftovers → the first grid), then drop ``source``."""
    nodes = (raw.get("canvas") or {}).get("nodes", [])
    gal = next((n for n in nodes if n.get("type") == "zone" and (n.get("data") or {}).get("role") == "gallery"), None)
    if not gal:
        return raw
    grids = [b for b in ((gal.get("data") or {}).get("blocks") or []) if b.get("type") == "grid"]
    if not grids:
        return raw
    imgs = [im for im in raw.get("images", []) if (im.get("role") or "gallery") == "gallery"]
    imgs.sort(key=lambda im: im.get("created_at") or "")

    def matches(src: str, im: dict) -> bool:
        if src == "favorites":
            return bool(im.get("favorite"))
        if src.startswith("tag:"):
            return src[4:] in (im.get("tags") or [])
        if src.startswith("group:"):
            return im.get("group") == src[6:]
        return True  # "all" (and anything unknown) matches everything

    for g in grids:
        g["imageIds"] = []
    for im in imgs:
        target = next((g for g in grids if matches(g.get("source", "all"), im)), grids[0])
        target["imageIds"].append(im["id"])
    for g in grids:
        g.pop("source", None)
    return raw


_MIGRATIONS: dict[int, Callable[[dict], dict]] = {
    1: _migrate_1_to_2, 2: _migrate_2_to_3, 3: _migrate_3_to_4, 4: _migrate_4_to_5, 5: _migrate_5_to_6,
}


def load_doc(text: str) -> WorkDoc:
    """Parse a ``work.json`` payload, applying linear migrations up to :data:`CURRENT`.

    A document written by a NEWER build (version > CURRENT) loads as-is with a warning —
    unknown fields are ignored by validation, so an older app degrades instead of crashing.
    """
    raw = json.loads(text)
    raw = _sanitize(raw)  # tolerate a corrupt/hand-edited work.json instead of 500-ing the load (rebuild is lenient too)
    version = int(raw.get("schema_version") or 1)
    if version > CURRENT:
        log.warning(
            "Work %s has schema_version %s > %s (written by a newer build) — loading as-is",
            raw.get("id"), version, CURRENT,
        )
    while version < CURRENT:
        raw = _MIGRATIONS[version](raw)
        version += 1
        raw["schema_version"] = version
    return WorkDoc.model_validate(raw)


def _sanitize(raw: dict) -> dict:
    """Make a hostile/partial ``work.json`` loadable: coerce null collections to [], drop entries that
    aren't dicts or lack an id. The rebuild path already skips such rows, so load should not 500 either."""
    if not isinstance(raw, dict):
        return {}
    if not isinstance(raw.get("canvas"), dict):
        raw["canvas"] = {}
    for key in ("snapshots", "images", "stack", "favorites"):
        v = raw.get(key)
        if not isinstance(v, list):
            raw[key] = []
    for key in ("snapshots", "images", "stack"):
        raw[key] = [e for e in raw[key] if isinstance(e, dict) and e.get("id")]
    return raw
