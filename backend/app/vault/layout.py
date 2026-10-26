"""Path helpers — id validation, slugs, a traversal-safe join, an atomic text write, and a folder removal that a file
another program holds can't stop halfway."""
import os
import re
import shutil
import sys
import threading
from pathlib import Path

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")  # \Z (not $) so a trailing newline can't sneak through
# What the OS drops into folders it shows (Explorer's thumbnail cache and folder settings, Finder's) — never vault data.
OS_LITTER = frozenset({"thumbs.db", "desktop.ini", ".ds_store"})


def is_os_litter(path: Path) -> bool:
    return path.name.lower() in OS_LITTER


def remove_tree(path: Path) -> list[str]:
    """Delete a folder as far as it goes: a file another program holds open (Explorer's Thumbs.db on a network share)
    stays, and the rest is removed. Returns the names of the files left behind."""
    held: list[str] = []

    def skip(_func: object, name: str, _exc: object) -> None:
        if not Path(name).is_dir():  # a folder that stays because of what is in it isn't news
            held.append(Path(name).name)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=skip)
    else:
        shutil.rmtree(path, onerror=lambda func, name, info: skip(func, name, info))
    return held


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a per-writer temp + os.replace so a crash or concurrent writer never leaves the file
    truncated (matters for the durable JSON: work/block/category manifests and sidecars)."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, "utf-8")
    os.replace(tmp, path)


def valid_id(value: str) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def slugify(title: str) -> str:
    # Unicode-aware: keep letters/digits from any script (Cyrillic, etc.), not just ASCII, so
    # non-Latin names don't all collapse to "untitled". \w includes "_"; runs of anything else → "-".
    slug = re.sub(r"[^\w]+", "-", (title or "").strip().lower(), flags=re.UNICODE).strip("-_")
    return slug[:48] or "untitled"


def safe_join(root: Path, *parts: str) -> Path:
    """Join under ``root`` and reject any result that escapes it (``..``, absolute)."""
    resolved = root.joinpath(*parts).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ValueError("path escapes vault root")
    return resolved
