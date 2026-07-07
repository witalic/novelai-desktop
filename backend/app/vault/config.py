"""Vault location: resolve/persist which folder is the active vault, and initialise its tree.

Resolution order for the active dir: explicit env (``NAI_VAULT__DIR``) > runtime pointer file >
None (not chosen yet). The pointer is app-config (not a secret) — it lives in the OS config dir,
never in the keychain or ``.env`` (``rules/security.md``).
"""
import json
import logging
import os
from pathlib import Path

from app.settings import Settings

log = logging.getLogger(__name__)

_MARKER = ".vault.json"
_SCHEMA_VERSION = 1
_STATE_FILE = "vault_state.json"


def proposed_default() -> Path:
    docs = Path.home() / "Documents"
    base = docs if docs.is_dir() else Path.home()
    return base / "novelai-vault"


def _config_dir(settings: Settings) -> Path:
    if settings.vault.state_dir:
        return Path(settings.vault.state_dir)
    appdata = os.environ.get("APPDATA")
    root = Path(appdata) if appdata else (Path.home() / ".config")
    return root / "novelai-desktop"


def read_pointer(settings: Settings) -> Path | None:
    try:
        data = json.loads((_config_dir(settings) / _STATE_FILE).read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    chosen = data.get("vault_dir")
    return Path(chosen) if chosen else None


def write_pointer(settings: Settings, vault_dir: Path) -> None:
    cfg = _config_dir(settings)
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / _STATE_FILE).write_text(json.dumps({"vault_dir": str(vault_dir)}), "utf-8")


def active_vault_dir(settings: Settings) -> Path | None:
    if settings.vault.dir:
        return Path(settings.vault.dir)
    return read_pointer(settings)


def is_initialized(vault_dir: Path) -> bool:
    return (vault_dir / _MARKER).is_file()


def writable(vault_dir: Path) -> bool:
    """Read-only writability check (no side effects) — safe for GET."""
    return vault_dir.is_dir() and os.access(vault_dir, os.W_OK)


def validate_dir(vault_dir: Path) -> str | None:
    """Return an error message if the folder can't be used, else None (may create the folder)."""
    if not vault_dir.is_absolute():
        return "Path must be absolute."
    try:
        vault_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"Cannot create folder: {exc}"
    if not vault_dir.is_dir():
        return "Not a folder."
    probe = vault_dir / ".write_test"
    try:
        probe.write_text("ok", "utf-8")
        probe.unlink()
    except OSError:
        return "Folder is not writable."
    return None


def init_vault(vault_dir: Path) -> None:
    for sub in ("works", "library/blocks", "presets"):
        (vault_dir / sub).mkdir(parents=True, exist_ok=True)
    marker = vault_dir / _MARKER
    if not marker.is_file():
        marker.write_text(json.dumps({"schema_version": _SCHEMA_VERSION}), "utf-8")
    log.info("Initialised vault at %s", vault_dir)
