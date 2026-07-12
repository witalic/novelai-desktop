"""Prompt-block categories: names + colors, persisted so custom ones survive.

Files are the truth (``library/categories.json``); the SQLite index only counts blocks per
category slug. A built-in default set seeds the palette; users add their own (with any hex color),
recolor any (including built-ins), and delete any empty one. Deleting a built-in tombstones it in
this vault's ``hidden`` list so the code defaults don't just re-seed it.

On-disk shape: ``{"overrides": [{slug,name,color}, ...], "hidden": ["<slug>", ...],
"order": ["<slug>", ...]}``. ``order`` is a user-defined category order; slugs not in it sort
to the end (in their default position), so the app reads one order everywhere.
"""
import json
import logging
from pathlib import Path

from app.vault import layout
from app.vault.models import CategoryDoc

log = logging.getLogger(__name__)

# Built-in categories (slug → name/color), grouped by prefix so the rail clusters by axis.
# Colors run in hue families per group (body = warm, outfit = pink, scene = cool, nsfw = crimson).
DEFAULTS: list[CategoryDoc] = [
    CategoryDoc(slug="character", name="Character", color="#0c66e4"),
    # body / subject
    CategoryDoc(slug="body", name="Body", color="#c77d54"),
    CategoryDoc(slug="body-anatomy", name="Body - Anatomy", color="#c0674a"),
    CategoryDoc(slug="body-skin", name="Body - Skin", color="#e0a878"),
    CategoryDoc(slug="body-hair", name="Body - Hair", color="#9c6b3f"),
    CategoryDoc(slug="body-face", name="Body - Face", color="#d99578"),
    CategoryDoc(slug="body-state", name="Body - State", color="#cf7a63"),
    # wardrobe
    CategoryDoc(slug="outfit", name="Outfit", color="#d4537e"),
    CategoryDoc(slug="outfit-fabric", name="Outfit - Fabric", color="#c06a97"),
    CategoryDoc(slug="outfit-accessory", name="Outfit - Accessory", color="#b3789e"),
    # motion / framing
    CategoryDoc(slug="pose", name="Pose", color="#ae4787"),
    CategoryDoc(slug="action", name="Action", color="#9a5ba6"),
    CategoryDoc(slug="composition", name="Composition", color="#7f5aa0"),
    # scene
    CategoryDoc(slug="scene-environment", name="Scene - Environment", color="#1f845a"),
    CategoryDoc(slug="scene-lighting", name="Scene - Lighting", color="#b65c02"),
    CategoryDoc(slug="scene-camera", name="Scene - Camera", color="#12b5a6"),
    CategoryDoc(slug="scene-effects", name="Scene - Effects", color="#2f9e8f"),
    CategoryDoc(slug="scene-color", name="Scene - Color", color="#3f9d6b"),
    # style
    CategoryDoc(slug="style", name="Style", color="#6e5dc6"),
    # nsfw (also carry an `nsfw` tag)
    CategoryDoc(slug="nsfw", name="NSFW", color="#c2255c"),
    CategoryDoc(slug="nsfw-act", name="NSFW - Act", color="#a61e4d"),
    CategoryDoc(slug="nsfw-fluids", name="NSFW - Fluids", color="#d6499a"),
    # meta
    CategoryDoc(slug="negative", name="Negative", color="#e2483d"),
    CategoryDoc(slug="custom", name="Custom", color="#738496"),
]
DEFAULT_SLUGS = frozenset(c.slug for c in DEFAULTS)
_FILE = "categories.json"


def _path(vault: Path) -> Path:
    return vault / "library" / _FILE


def _read_raw(vault: Path) -> tuple[list[dict], set[str], list[str]]:
    try:
        data = json.loads(_path(vault).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], set(), []
    if isinstance(data, list):  # legacy shape: a bare list of overrides
        return data, set(), []
    if not isinstance(data, dict):  # a scalar (null/number/string) in the file → treat as empty, not a 500
        return [], set(), []
    overrides = data.get("overrides", [])
    hidden = data.get("hidden", [])
    order = data.get("order", [])
    # Non-list overrides/hidden/order (e.g. {"hidden": null}) must also degrade gracefully, not 500.
    return (
        overrides if isinstance(overrides, list) else [],
        set(hidden if isinstance(hidden, list) else []),
        [s for s in order if isinstance(s, str)] if isinstance(order, list) else [],
    )


def _write_raw(vault: Path, overrides: list[dict], hidden: set[str], order: list[str]) -> None:
    path = _path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"overrides": overrides, "hidden": sorted(hidden), "order": order}
    layout.atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def read_all(vault: Path) -> list[CategoryDoc]:
    """Defaults (minus hidden) overlaid with user overrides/customs, then sorted by the user's
    ``order`` (slugs not in it keep their default position, at the end)."""
    overrides, hidden, order = _read_raw(vault)
    by_slug: dict[str, CategoryDoc] = {c.slug: c.model_copy() for c in DEFAULTS if c.slug not in hidden}
    for raw in overrides:
        try:
            cat = CategoryDoc.model_validate(raw)
        except Exception:  # noqa: BLE001 — one malformed override must not 500 the whole category list
            log.warning("Skipping malformed category override: %r", raw)
            continue
        if cat.slug in hidden:
            continue
        by_slug[cat.slug] = cat
    cats = list(by_slug.values())
    if order:  # stable sort: ordered slugs first (in order), everything else after in default order
        pos = {slug: i for i, slug in enumerate(order)}
        cats.sort(key=lambda c: pos.get(c.slug, len(order)))
    return cats


def upsert(vault: Path, cat: CategoryDoc) -> None:
    """Create or recolor a category (works for built-ins too). Re-creating un-hides a tombstoned slug."""
    overrides, hidden, order = _read_raw(vault)
    overrides = [c for c in overrides if c.get("slug") != cat.slug]
    overrides.append(cat.model_dump())
    hidden.discard(cat.slug)
    _write_raw(vault, overrides, hidden, order)


def restore(vault: Path, slugs: list[str]) -> list[str]:
    """Un-tombstone the given built-in categories (bring back deleted defaults). Returns those restored."""
    overrides, hidden, order = _read_raw(vault)
    restored = [s for s in slugs if s in DEFAULT_SLUGS and s in hidden]
    if restored:
        for s in restored:
            hidden.discard(s)
        _write_raw(vault, overrides, hidden, order)
    return restored


def remove(vault: Path, slug: str) -> bool:
    """Delete a category. Built-ins are tombstoned (hidden) so the code defaults don't re-seed them.
    The slug stays in ``order`` so a later restore returns it to its place."""
    overrides, hidden, order = _read_raw(vault)
    is_default = slug in DEFAULT_SLUGS
    had_override = any(c.get("slug") == slug for c in overrides)
    if not is_default and not had_override:
        return False  # unknown category — nothing to delete
    overrides = [c for c in overrides if c.get("slug") != slug]
    if is_default:
        hidden.add(slug)
    _write_raw(vault, overrides, hidden, order)
    return True


def reorder(vault: Path, slugs: list[str]) -> None:
    """Persist a user-defined category order (list of slugs)."""
    overrides, hidden, _ = _read_raw(vault)
    _write_raw(vault, overrides, hidden, [s for s in slugs if isinstance(s, str)])
