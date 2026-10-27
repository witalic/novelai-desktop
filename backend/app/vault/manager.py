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
from app.vault import index, layout
from app.vault.models import AppSettings, MoveStatus, PatchSettings, VaultConfig, VaultInfo

log = logging.getLogger(__name__)


def get_config(settings: Settings) -> VaultConfig:
    """The vault list — readable whether or not the active vault can be reached right now (its row says so), so the
    user can always switch."""
    try:
        active_path = appconfig.active_vault(settings)  # ensures a default exists on first run
    except appconfig.VaultUnavailable:
        active_path = None
    data = appconfig.load(settings)
    vaults = list(data["vaults"])
    active = data["active_vault"]
    if settings.vault.dir:  # env override (dev/tests): reflect it even if not persisted
        active = str(active_path)
        if active not in vaults:
            vaults = [active, *vaults]
    infos = []
    for v in vaults:
        reachable = appconfig.reachable(Path(v))  # asked once per folder (and the active one just was)
        infos.append(VaultInfo(dir=v, initialized=reachable and vaultcfg.is_initialized(Path(v)),
                               writable=reachable and vaultcfg.writable(Path(v)), reachable=reachable,
                               active=(v == active)))
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


def delete_vault(settings: Settings, dir_str: str, keep_files: bool = False) -> VaultConfig:
    """Physically delete the vault folder and drop it from the list (destructive — the UI warns). ``keep_files``: only
    drop it from the list — a vault that can't be reached, which nothing here may then touch should it come back."""
    if _move["active"]:  # don't rmtree a vault while a move is copying/removing folders
        raise HTTPException(status_code=409, detail="A vault move is in progress — try again in a moment.")
    target = Path(dir_str).expanduser()
    ts = str(target)
    data = appconfig.load(settings)
    if ts not in data["vaults"]:
        raise HTTPException(status_code=404, detail="Vault is not in the list.")
    if not keep_files:
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
    index.discard(index.location(appconfig.cache_dir(settings), target))  # its index goes with it from the list
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


def sweep_index_cache(settings: Settings) -> None:
    """Remove the cached indexes of vaults no longer in the list — one a move or delete couldn't remove at the time (a
    request still had it open), or a vault's folder renamed outside the app. They hold prompt text, and nothing would
    open them again. Run once per start."""
    cache = appconfig.cache_dir(settings)
    listed = [*appconfig.load(settings)["vaults"], *([settings.vault.dir] if settings.vault.dir else [])]
    keep = {index.location(cache, Path(v)) for v in listed}
    for db in (cache / "index").glob("*.sqlite"):
        if db not in keep:
            index.discard(db)


def _carried(f: Path) -> bool:
    """What a move copies: the vault's own files — not OS litter or writes still in flight (*.tmp). The index isn't in
    the vault; the moved one gets its own, built from the works at the first open."""
    return not (layout.is_os_litter(f) or f.suffix == ".tmp")


def _run_move(settings: Settings, src: Path, dst: Path) -> None:
    """Copy the vault, switch to the copy once it is whole, then remove the source. In that order: a source that can't
    be fully removed (a file another program holds, as Explorer does Thumbs.db on a share) only leaves litter behind —
    never a half-deleted vault still in use. A failed copy leaves the vault where it was and takes the partial copy
    away again (the target was empty), so the move can simply be retried."""
    relinked = False
    existed = dst.exists()  # an empty folder the user picked stays, emptied again
    try:
        files = [f for f in src.rglob("*") if f.is_file() and _carried(f)]
        _move["total"] = len(files)
        dst.mkdir(parents=True, exist_ok=True)
        for d in (d for d in src.rglob("*") if d.is_dir()):
            (dst / d.relative_to(src)).mkdir(parents=True, exist_ok=True)
        for f in files:
            target = dst / f.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(f, target)
            except FileNotFoundError:
                if f.exists():
                    raise
                log.info("Skipped %s while moving the vault — it went away meanwhile", f.name)
            _move["done"] += 1

        def relink(d: dict) -> None:
            d["vaults"] = list(dict.fromkeys([str(dst) if v == str(src) else v for v in d["vaults"]]))
            if d["active_vault"] == str(src):
                d["active_vault"] = str(dst)
        appconfig.update(settings, relink)
        relinked = True
        index.discard(index.location(appconfig.cache_dir(settings), src))
        if held := layout.remove_tree(src):
            log.warning("Moved the vault to %s, but %d file(s) in use by another program stay in %s: %s",
                        dst, len(held), src, ", ".join(held[:5]))
    except Exception as exc:  # noqa: BLE001 — surface any failure to the poller, don't crash the thread
        _move["error"] = str(exc)
        log.exception("Vault move failed")
        if not relinked and dst.exists():
            layout.remove_tree(dst)
            if existed:
                dst.mkdir(exist_ok=True)
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
