"""Path helpers — id validation, slugs, and a traversal-safe join confined under the vault root."""
import re
from pathlib import Path

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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
