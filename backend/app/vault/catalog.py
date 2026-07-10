"""Prompt-block categories: names + colors, persisted so custom ones survive.

Files are the truth (``library/categories.json``); the SQLite index only counts blocks per
category slug. A built-in default set seeds the palette; users add their own (with any hex color),
recolor any (including built-ins), and delete any empty one. Deleting a built-in tombstones it in
this vault's ``hidden`` list so the code defaults don't just re-seed it.

On-disk shape: ``{"overrides": [{slug,name,color}, ...], "hidden": ["<slug>", ...]}``.
"""
import json
import logging
from pathlib import Path

from app.vault import layout
from app.vault.models import CategoryDoc

log = logging.getLogger(__name__)

# Built-in categories (slug → name/color). Colors mirror the canvas block palette.
DEFAULTS: list[CategoryDoc] = [
    CategoryDoc(slug="style", name="Style", color="#6e5dc6"),
    CategoryDoc(slug="character", name="Character", color="#0c66e4"),
    CategoryDoc(slug="pose", name="Pose", color="#ae4787"),
    CategoryDoc(slug="environment", name="Environment", color="#1f845a"),
    CategoryDoc(slug="lighting", name="Lighting", color="#b65c02"),
    CategoryDoc(slug="camera", name="Camera", color="#12b5a6"),
    CategoryDoc(slug="outfit", name="Outfit", color="#d4537e"),
    CategoryDoc(slug="negative", name="Negative", color="#e2483d"),
    CategoryDoc(slug="custom", name="Custom", color="#738496"),
]
DEFAULT_SLUGS = frozenset(c.slug for c in DEFAULTS)
_FILE = "categories.json"


def _path(vault: Path) -> Path:
    return vault / "library" / _FILE


def _read_raw(vault: Path) -> tuple[list[dict], set[str]]:
    try:
        data = json.loads(_path(vault).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return [], set()
    if isinstance(data, list):  # legacy shape: a bare list of overrides
        return data, set()
    if not isinstance(data, dict):  # a scalar (null/number/string) in the file → treat as empty, not a 500
        return [], set()
    overrides = data.get("overrides", [])
    hidden = data.get("hidden", [])
    # Non-list overrides/hidden (e.g. {"hidden": null}) must also degrade gracefully, not 500.
    return (overrides if isinstance(overrides, list) else []), set(hidden if isinstance(hidden, list) else [])


def _write_raw(vault: Path, overrides: list[dict], hidden: set[str]) -> None:
    path = _path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"overrides": overrides, "hidden": sorted(hidden)}
    layout.atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def read_all(vault: Path) -> list[CategoryDoc]:
    """Defaults (minus hidden) overlaid with user overrides/customs; order: defaults first, then new."""
    overrides, hidden = _read_raw(vault)
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
    return list(by_slug.values())


def upsert(vault: Path, cat: CategoryDoc) -> None:
    """Create or recolor a category (works for built-ins too). Re-creating un-hides a tombstoned slug."""
    overrides, hidden = _read_raw(vault)
    overrides = [c for c in overrides if c.get("slug") != cat.slug]
    overrides.append(cat.model_dump())
    hidden.discard(cat.slug)
    _write_raw(vault, overrides, hidden)


def remove(vault: Path, slug: str) -> bool:
    """Delete a category. Built-ins are tombstoned (hidden) so the code defaults don't re-seed them."""
    overrides, hidden = _read_raw(vault)
    is_default = slug in DEFAULT_SLUGS
    had_override = any(c.get("slug") == slug for c in overrides)
    if not is_default and not had_override:
        return False  # unknown category — nothing to delete
    overrides = [c for c in overrides if c.get("slug") != slug]
    if is_default:
        hidden.add(slug)
    _write_raw(vault, overrides, hidden)
    return True
