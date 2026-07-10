"""Persisted app settings in the OS config dir: the vault list + active vault, UI prefs (theme,
accent), autosave interval, download folder. Secrets (the NovelAI token) live in the OS keychain,
never here.

Resilient by design: a corrupt ``settings.json`` or a missing/wrong-typed key never breaks the app —
every field falls back to a sane default (see ``_coerce``). The config dir is OS-conventional
(Windows ``%APPDATA%``, macOS ``~/Library/Application Support``, Linux ``$XDG_CONFIG_HOME``/``~/.config``).
"""
import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from app.settings import Settings
from app.vault import config as vaultcfg

log = logging.getLogger(__name__)
_FILE = "settings.json"
_lock = threading.RLock()  # serialize read-modify-write of settings.json across threads (patch, move, first-run)


def config_dir(settings: Settings) -> Path:
    if settings.vault.state_dir:  # test / explicit override
        return Path(settings.vault.state_dir)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "novelai-desktop"


def defaults(settings: Settings) -> dict:
    return {
        "vaults": [],
        "active_vault": None,
        "autosave_interval_s": 300,
        "theme": "dark",
        "accent": "#0c66e4",
        "download_dir": settings.download_dir,  # env default (~/Downloads) unless overridden
    }


def _coerce(raw: object, settings: Settings) -> dict:
    """Merge stored values over defaults, dropping anything missing or of the wrong type."""
    d = defaults(settings)
    if not isinstance(raw, dict):
        return d
    if isinstance(raw.get("vaults"), list):
        d["vaults"] = _dedupe([str(x) for x in raw["vaults"] if isinstance(x, str)])
    if isinstance(raw.get("active_vault"), str):
        d["active_vault"] = raw["active_vault"]
    iv = raw.get("autosave_interval_s")
    if isinstance(iv, (int, float)) and 30 <= iv <= 24 * 3600:
        d["autosave_interval_s"] = int(iv)
    for key in ("theme", "accent", "download_dir"):
        if isinstance(raw.get(key), str) and raw[key]:
            d[key] = raw[key]
    return d


def _dedupe(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def load(settings: Settings) -> dict:
    try:
        raw = json.loads((config_dir(settings) / _FILE).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = None  # missing or corrupt → all defaults
    return _coerce(raw, settings)


def save(settings: Settings, data: dict) -> dict:
    coerced = _coerce(data, settings)
    cfg = config_dir(settings)
    cfg.mkdir(parents=True, exist_ok=True)
    # Atomic write with a per-writer temp name: a crash / concurrent writer mid-write must never truncate
    # this file (a corrupt settings.json falls back to all-defaults → the vault registry silently vanishes),
    # and two writers must not fight over a shared temp path.
    with _lock:
        tmp = cfg / f"{_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(json.dumps(coerced, ensure_ascii=False, indent=2), "utf-8")
        os.replace(tmp, cfg / _FILE)
    return coerced


def update(settings: Settings, mutate: Callable[[dict], None]) -> dict:
    """Atomic read-modify-write of the settings file, serialized across threads — the only safe way to edit
    it when patch(), the move thread, and first-run registration can all run concurrently."""
    with _lock:
        data = load(settings)
        mutate(data)
        return save(settings, data)


def patch(settings: Settings, changes: dict) -> dict:
    def apply(data: dict) -> None:
        for key, value in changes.items():
            if value is not None:
                data[key] = value
    return update(settings, apply)


def active_vault(settings: Settings) -> Path:
    """Resolve the active vault, creating + registering the default on first run — never blocks."""
    if settings.vault.dir:  # explicit env / test override wins
        target = Path(settings.vault.dir)
        if not vaultcfg.is_initialized(target):
            vaultcfg.init_vault(target)
        return target
    data = load(settings)
    chosen = data.get("active_vault")
    if chosen and Path(chosen).is_dir():
        target = Path(chosen)
        if not vaultcfg.is_initialized(target):
            vaultcfg.init_vault(target)
        return target
    # first run (or the active folder vanished): fall back to the default and register it atomically
    default = vaultcfg.proposed_default(settings)
    vaultcfg.init_vault(default)

    def register(d: dict) -> None:
        d["vaults"] = _dedupe([*d["vaults"], str(default)])
        d["active_vault"] = str(default)
    update(settings, register)
    return default
