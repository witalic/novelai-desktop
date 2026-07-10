"""Path helpers — id validation, slugs, a traversal-safe join, and an atomic text write."""
import os
import re
import threading
from pathlib import Path

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")  # \Z (not $) so a trailing newline can't sneak through


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
