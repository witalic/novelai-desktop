"""Persisted app settings in the OS config dir: the vault list + active vault, UI prefs (theme,
accent), autosave interval, download folder, agent access (MCP). Secrets (the NovelAI and agent
tokens) live in the OS keychain, never here. Rebuildable data (the vault indexes) goes to the OS cache dir.

Resilient by design: a corrupt ``settings.json`` or a missing/wrong-typed key never breaks the app —
every field falls back to a sane default (see ``_coerce``). The config dir is OS-conventional
(Windows ``%APPDATA%``, macOS ``~/Library/Application Support``, Linux ``$XDG_CONFIG_HOME``/``~/.config``).
"""
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path

from fastapi import HTTPException

from app.agent import config as agentcfg
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


def cache_dir(settings: Settings) -> Path:
    """Where this machine keeps what it can rebuild (the vault indexes): local, never roamed with the profile or kept
    in a vault — a vault may sit on a network share, where SQLite has no business."""
    if settings.vault.state_dir:  # test / explicit override
        return Path(settings.vault.state_dir)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "novelai-desktop"


def defaults(settings: Settings) -> dict:
    return {
        "vaults": [],
        "active_vault": None,
        "autosave_interval_s": 300,
        "theme": "dark",
        "accent": "#0c66e4",
        "download_dir": settings.download_dir,  # env default (~/Downloads) unless overridden
        "agent": agentcfg.coerce(None),
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
    d["agent"] = agentcfg.coerce(raw.get("agent"))
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
    """The settings — all defaults only when there is no file yet (or it is corrupt). A file that exists but can't be
    read right now (held by another program a moment) raises instead: taken for a first run, it would be written over
    with defaults, the vault list and agent settings gone."""
    path = config_dir(settings) / _FILE
    with _lock:  # never mid-replace by one of our own writers
        for attempt in range(3):
            try:
                raw = json.loads(path.read_text("utf-8"))
                break
            except (FileNotFoundError, json.JSONDecodeError):
                raw = None  # first run, or corrupt → all defaults
                break
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(0.05)
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


class VaultUnavailable(HTTPException):
    """The active vault's folder can't be reached (a NAS asleep, the network down, a drive unplugged). Never a reason
    to switch to another vault — the user decides that in Settings → Vaults; the app waits for it to come back."""

    def __init__(self, folder: str) -> None:
        super().__init__(status_code=503, detail=f"The vault folder {folder} can't be reached.")


_PROBE_TIMEOUT_S = 2.0
_PROBE_TTL_S = 2.0
_probe_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="vault-probe")
_probes: dict[str, tuple[float, Future]] = {}
_probe_lock = threading.Lock()


def reachable(folder: Path) -> bool:
    """Whether a vault folder answers. A share whose host is down can take far longer than a request should wait to say
    so, holding one of the server's threads all the while — so this gives up after 2 s (unreachable), asks once for
    everyone waiting on the same folder, and remembers the answer for 2 s (a gallery's thumbnails don't each ask)."""
    key = str(folder)
    now = time.monotonic()
    with _probe_lock:
        probe = _probes.get(key)
        if probe is None or (probe[1].done() and now - probe[0] > _PROBE_TTL_S):
            probe = (now, _probe_pool.submit(folder.is_dir))
            _probes[key] = probe
    try:
        return bool(probe[1].result(timeout=_PROBE_TIMEOUT_S))
    except (FutureTimeout, OSError):
        return False


def active_vault(settings: Settings) -> Path:
    """Resolve the active vault, creating + registering the default on first run. A chosen one that can't be reached is
    a VaultUnavailable — waited for, never replaced."""
    if settings.vault.dir:  # explicit env / test override wins
        target = Path(settings.vault.dir)
        if not vaultcfg.is_initialized(target):
            vaultcfg.init_vault(target)
        return target
    data = load(settings)
    chosen = data.get("active_vault")
    if chosen:
        target = Path(chosen)
        if not reachable(target):
            raise VaultUnavailable(chosen)
        if not vaultcfg.is_initialized(target):
            # Every vault is marked when it's added. An unmarked folder with no works in it is not the vault but where
            # it should be — an empty mount point of a share that isn't mounted — so it's waited for, not filled.
            if not (target / "works").is_dir():
                raise VaultUnavailable(chosen)
            vaultcfg.init_vault(target)
        return target
    # first run: the default vault, registered atomically
    default = vaultcfg.proposed_default(settings)
    vaultcfg.init_vault(default)

    def register(d: dict) -> None:
        d["vaults"] = _dedupe([*d["vaults"], str(default)])
        d["active_vault"] = str(default)
    update(settings, register)
    return default
