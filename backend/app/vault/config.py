"""Vault filesystem helpers: initialise a vault's tree, validate a folder, probe writability.

Which folder is *active* (and the list of known vaults) is owned by ``app.appconfig`` — it persists
that in the OS config dir. Secrets never live here or there; they belong in the keychain.
"""
import json
import logging
import os
from pathlib import Path

from app.settings import Settings

log = logging.getLogger(__name__)

_MARKER = ".vault.json"
_SCHEMA_VERSION = 1


def proposed_default(settings: Settings) -> Path:
    if settings.vault.default_dir:  # test / explicit override
        return Path(settings.vault.default_dir)
    docs = Path.home() / "Documents"
    base = docs if docs.is_dir() else Path.home()
    return base / "novelai-vault"


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
    # Don't scatter a vault tree into an existing non-empty folder (e.g. the user's Documents root) — require
    # an empty folder, or one that is already a vault.
    if not is_initialized(vault_dir) and any(vault_dir.iterdir()):
        return "Folder is not empty — choose an empty folder or an existing vault."
    return None


def init_vault(vault_dir: Path) -> None:
    for sub in ("works", "library/blocks", "presets"):
        (vault_dir / sub).mkdir(parents=True, exist_ok=True)
    marker = vault_dir / _MARKER
    if not marker.is_file():
        marker.write_text(json.dumps({"schema_version": _SCHEMA_VERSION}), "utf-8")
    log.info("Initialised vault at %s", vault_dir)
