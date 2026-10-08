"""Preset storage: named bundles of generation params (no prompt, no seed).

Presets are few and searched only by name, so there is NO SQLite index — user presets are one JSON
per file under ``presets/<id>.json`` (dir created by ``config.init_vault``), and the mutable
``default``/``favorite`` flags live in an overlay ``presets/.state.json``. The overlay is the only
honest home for those flags: they also apply to **built-ins**, which are code-shipped and read-only,
so they have no writable file of their own. Files on disk are authoritative; built-ins are merged in
read-only on every read.
"""
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app import appconfig
from app.settings import Settings
from app.vault import layout, manager
from app.vault.models import Preset, PresetDoc, PresetParams

log = logging.getLogger(__name__)

_STATE_FILE = ".state.json"
_state_lock = threading.Lock()  # serialize overlay writes (rare); file writes are already atomic

# Code-shipped starters (stable ids). Read-only: never written to disk, only merged into the list.
# All on v5 (guidance ~7, its default); v4.5 stays selectable in the model picker and user presets.
_BUILTINS: list[PresetDoc] = [
    PresetDoc(id="builtin-anime-full", name="Anime · Full", params=PresetParams(
        model="nai-diffusion-5-full", width=832, height=1216, steps=28, scale=7.0,
        sampler="k_euler_ancestral", uc_preset=4)),
    PresetDoc(id="builtin-curated-soft", name="Curated · Soft", params=PresetParams(
        model="nai-diffusion-5-curated", width=832, height=1216, steps=28, scale=7.0,
        sampler="k_dpmpp_2s_ancestral", uc_preset=5)),
    PresetDoc(id="builtin-fast-draft", name="Fast draft", params=PresetParams(
        model="nai-diffusion-5-full", width=640, height=640, steps=18, scale=6.0,
        sampler="k_euler", quality_toggle=False, uc_preset=5)),
    PresetDoc(id="builtin-wallpaper", name="Wallpaper", params=PresetParams(
        model="nai-diffusion-5-full", width=1088, height=1920, steps=32, scale=7.0,
        sampler="k_dpmpp_2m_sde", uc_preset=4)),
    PresetDoc(id="builtin-furry", name="Furry Focus", params=PresetParams(
        model="nai-diffusion-5-full", width=832, height=1216, steps=28, scale=7.0,
        sampler="k_euler_ancestral", uc_preset=7)),
]
_FALLBACK_DEFAULT = _BUILTINS[0].id  # Anime · Full, when no default is set / points at a deleted preset


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _vault(settings: Settings) -> Path:
    return appconfig.active_vault(settings)


def _guard_no_move() -> None:
    if manager.move_status().active:
        raise HTTPException(status_code=409, detail="A vault move is in progress — try again in a moment.")


def _dir(vault: Path) -> Path:
    return vault / "presets"


def _scan_user(vault: Path) -> list[PresetDoc]:
    """User preset docs, skipping the dotfile overlay and any unreadable file (never 500 a list)."""
    out: list[PresetDoc] = []
    d = _dir(vault)
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            if f.name.startswith("."):  # pathlib glob matches leading dots — keep .state.json out
                continue
            try:
                out.append(PresetDoc.model_validate_json(f.read_text("utf-8")))
            except Exception:  # noqa: BLE001 — a corrupt preset must not abort the whole list
                log.warning("Skipping unreadable preset: %s", f)
    return out


def _read_state(vault: Path) -> dict:
    p = _dir(vault) / _STATE_FILE
    if p.is_file():
        try:
            data = json.loads(p.read_text("utf-8"))
            if isinstance(data, dict):
                return {"default_id": data.get("default_id"), "favorites": list(data.get("favorites") or [])}
        except (OSError, ValueError):
            log.warning("Corrupt preset state at %s — treating as empty", p)
    return {"default_id": None, "favorites": []}


def _write_state(vault: Path, state: dict) -> None:
    d = _dir(vault)
    d.mkdir(parents=True, exist_ok=True)
    payload = {"default_id": state.get("default_id"), "favorites": sorted(set(state.get("favorites") or []))}
    layout.atomic_write_text(d / _STATE_FILE, json.dumps(payload))


def _existing_ids(vault: Path) -> set[str]:
    return {b.id for b in _BUILTINS} | {u.id for u in _scan_user(vault)}


def list_presets(settings: Settings) -> list[Preset]:
    vault = _vault(settings)
    state = _read_state(vault)
    user = _scan_user(vault)
    ids = {b.id for b in _BUILTINS} | {u.id for u in user}
    default_id = state["default_id"] if state["default_id"] in ids else _FALLBACK_DEFAULT
    favorites = set(state["favorites"]) & ids
    out = [
        Preset(id=b.id, name=b.name, params=b.params, builtin=True,
               favorite=b.id in favorites, is_default=b.id == default_id)
        for b in _BUILTINS
    ]
    out += [
        Preset(id=u.id, name=u.name, params=u.params, builtin=False,
               favorite=u.id in favorites, is_default=u.id == default_id,
               created_at=u.created_at, updated_at=u.updated_at)
        for u in user
    ]
    return out


def save_preset(settings: Settings, doc: PresetDoc) -> dict:
    if not layout.valid_id(doc.id) or doc.id.startswith("builtin-"):
        raise HTTPException(status_code=400, detail="Invalid preset id.")
    if not doc.name.strip():
        raise HTTPException(status_code=400, detail="Preset name is required.")
    _guard_no_move()
    vault = _vault(settings)
    d = _dir(vault)
    d.mkdir(parents=True, exist_ok=True)
    path = layout.safe_join(d, f"{doc.id}.json")
    doc.updated_at = _now()
    if not doc.created_at:  # keep the original creation time on updates
        prev = _scan_one(path)
        doc.created_at = (prev.created_at if prev else "") or doc.updated_at
    layout.atomic_write_text(path, doc.model_dump_json())
    return {"id": doc.id}


def _scan_one(path: Path) -> PresetDoc | None:
    if path.is_file():
        try:
            return PresetDoc.model_validate_json(path.read_text("utf-8"))
        except Exception:  # noqa: BLE001
            return None
    return None


def delete_preset(settings: Settings, preset_id: str) -> dict:
    if not layout.valid_id(preset_id) or preset_id.startswith("builtin-"):
        raise HTTPException(status_code=400, detail="Built-in presets can't be deleted.")
    _guard_no_move()
    vault = _vault(settings)
    path = layout.safe_join(_dir(vault), f"{preset_id}.json")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Preset not found.")
    path.unlink()
    with _state_lock:  # drop the deleted id from the overlay (default falls back to Anime · Full)
        state = _read_state(vault)
        if state["default_id"] == preset_id:
            state["default_id"] = None
        state["favorites"] = [f for f in state["favorites"] if f != preset_id]
        _write_state(vault, state)
    return {"id": preset_id, "deleted": True}


def set_default(settings: Settings, preset_id: str) -> dict:
    _guard_no_move()
    vault = _vault(settings)
    if preset_id not in _existing_ids(vault):
        raise HTTPException(status_code=404, detail="Preset not found.")
    with _state_lock:
        state = _read_state(vault)
        state["default_id"] = preset_id  # exactly one default — a single value, not a set
        _write_state(vault, state)
    return {"id": preset_id}


def set_favorite(settings: Settings, preset_id: str, favorite: bool) -> dict:
    _guard_no_move()
    vault = _vault(settings)
    if preset_id not in _existing_ids(vault):
        raise HTTPException(status_code=404, detail="Preset not found.")
    with _state_lock:
        state = _read_state(vault)
        favs = set(state["favorites"])
        favs.add(preset_id) if favorite else favs.discard(preset_id)
        state["favorites"] = sorted(favs)
        _write_state(vault, state)
    return {"id": preset_id, "favorite": favorite}
