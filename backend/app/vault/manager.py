"""Vault management: list/add/switch/delete (physical) vaults, move a vault (background, with
progress), and read/patch persisted app settings. State lives in ``app.appconfig`` (OS config dir).
"""
import logging
import shutil
import threading
from pathlib import Path

from fastapi import HTTPException

from app import appconfig
from app.settings import Settings
from app.vault import config as vaultcfg
from app.vault.models import AppSettings, MoveStatus, PatchSettings, VaultConfig, VaultInfo

log = logging.getLogger(__name__)


def get_config(settings: Settings) -> VaultConfig:
    active_path = appconfig.active_vault(settings)  # ensures a default exists on first run
    data = appconfig.load(settings)
    vaults = list(data["vaults"])
    active = data["active_vault"]
    if settings.vault.dir:  # env override (dev/tests): reflect it even if not persisted
        active = str(active_path)
        if active not in vaults:
            vaults = [active, *vaults]
    infos = [
        VaultInfo(dir=v, initialized=vaultcfg.is_initialized(Path(v)),
                  writable=vaultcfg.writable(Path(v)), active=(v == active))
        for v in vaults
    ]
    return VaultConfig(active=active, vaults=infos, proposed_default=str(vaultcfg.proposed_default(settings)))


def add_vault(settings: Settings, dir_str: str) -> VaultConfig:
    target = Path(dir_str).expanduser()
    error = vaultcfg.validate_dir(target)
    if error:
        raise HTTPException(status_code=400, detail=error)
    vaultcfg.init_vault(target)

    def add(d: dict) -> None:
        d["vaults"] = list(dict.fromkeys([*d["vaults"], str(target)]))
        d["active_vault"] = str(target)  # a freshly added vault becomes active
    appconfig.update(settings, add)
    return get_config(settings)


def set_active(settings: Settings, dir_str: str) -> VaultConfig:
    target = str(Path(dir_str).expanduser())

    def activate(d: dict) -> None:
        if target not in d["vaults"]:
            raise HTTPException(status_code=404, detail="Vault is not in the list — add it first.")
        d["active_vault"] = target
    appconfig.update(settings, activate)
    return get_config(settings)


def delete_vault(settings: Settings, dir_str: str) -> VaultConfig:
    """Physically delete the vault folder and drop it from the list (destructive — the UI warns)."""
    target = Path(dir_str).expanduser()
    ts = str(target)
    data = appconfig.load(settings)
    if ts not in data["vaults"]:
        raise HTTPException(status_code=404, detail="Vault is not in the list.")
    if target.is_dir() and not vaultcfg.is_initialized(target):
        raise HTTPException(status_code=400, detail="Refusing to delete a folder that isn't a vault.")
    try:
        if target.is_dir():
            shutil.rmtree(target)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Could not delete the folder: {exc}") from exc

    def drop(d: dict) -> None:
        d["vaults"] = [v for v in d["vaults"] if v != ts]
        if d["active_vault"] == ts:
            d["active_vault"] = d["vaults"][0] if d["vaults"] else None
    appconfig.update(settings, drop)
    return get_config(settings)


# ---- move a vault (background copy + rmtree, with progress) ----
_move: dict = {"active": False, "total": 0, "done": 0, "error": None}
_move_lock = threading.Lock()


def move_status() -> MoveStatus:
    return MoveStatus(**_move)


def start_move(settings: Settings, src_str: str, dst_str: str) -> MoveStatus:
    src = Path(src_str).expanduser()
    dst = Path(dst_str).expanduser()
    data = appconfig.load(settings)
    if str(src) not in data["vaults"]:
        raise HTTPException(status_code=404, detail="Source vault is not in the list.")
    if not src.is_dir():
        raise HTTPException(status_code=400, detail="Source folder does not exist.")
    if not dst.is_absolute():
        raise HTTPException(status_code=400, detail="Target path must be absolute.")
    if dst == src:
        raise HTTPException(status_code=400, detail="Target is the same as the source.")
    # Nesting is catastrophic: copying src into a subdir of itself, then rmtree(src), deletes the copy too.
    rsrc, rdst = src.resolve(), dst.resolve()
    if rdst.is_relative_to(rsrc) or rsrc.is_relative_to(rdst):
        raise HTTPException(status_code=400, detail="Target must not be inside the source (or vice versa).")
    if dst.exists() and any(dst.iterdir()):
        raise HTTPException(status_code=400, detail="Target folder must be empty or not exist.")
    with _move_lock:
        if _move["active"]:
            raise HTTPException(status_code=409, detail="A move is already in progress.")
        _move.update(active=True, total=0, done=0, error=None)
    threading.Thread(target=_run_move, args=(settings, src, dst), daemon=True).start()
    return move_status()


def _run_move(settings: Settings, src: Path, dst: Path) -> None:
    try:
        _move["total"] = sum(1 for f in src.rglob("*") if f.is_file())
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.rglob("*"):
            target = dst / f.relative_to(src)
            if f.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
                _move["done"] += 1
        shutil.rmtree(src)

        def relink(d: dict) -> None:
            d["vaults"] = list(dict.fromkeys([str(dst) if v == str(src) else v for v in d["vaults"]]))
            if d["active_vault"] == str(src):
                d["active_vault"] = str(dst)
        appconfig.update(settings, relink)
    except Exception as exc:  # noqa: BLE001 — surface any failure to the poller, don't crash the thread
        _move["error"] = str(exc)
        log.exception("Vault move failed")
    finally:
        _move["active"] = False


# ---- app settings ----
def get_settings(settings: Settings) -> AppSettings:
    d = appconfig.load(settings)
    return AppSettings(autosave_interval_s=d["autosave_interval_s"], theme=d["theme"],
                       accent=d["accent"], download_dir=d["download_dir"])


def patch_settings(settings: Settings, body: PatchSettings) -> AppSettings:
    d = appconfig.patch(settings, body.model_dump(exclude_none=True))
    return AppSettings(autosave_interval_s=d["autosave_interval_s"], theme=d["theme"],
                       accent=d["accent"], download_dir=d["download_dir"])
