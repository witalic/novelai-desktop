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

CURRENT = 3


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


_MIGRATIONS: dict[int, Callable[[dict], dict]] = {1: _migrate_1_to_2, 2: _migrate_2_to_3}


def load_doc(text: str) -> WorkDoc:
    """Parse a ``work.json`` payload, applying linear migrations up to :data:`CURRENT`.

    A document written by a NEWER build (version > CURRENT) loads as-is with a warning —
    unknown fields are ignored by validation, so an older app degrades instead of crashing.
    """
    raw = json.loads(text)
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
