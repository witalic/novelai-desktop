"""Console logging setup. Operational output goes through ``logging``, never ``print`` (``rules/code-style.md``)."""
import logging
import sys

from app.settings import Settings

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_configured = False


def force_utf8_console() -> None:
    """Make stdout/stderr UTF-8 so Cyrillic log lines don't crash on a cp1251 Windows console."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def setup_logging(settings: Settings) -> None:
    """Idempotent root logging config: one console handler at the configured level."""
    global _configured
    force_utf8_console()
    root = logging.getLogger()
    root.setLevel(settings.log.level.upper())
    if _configured:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
    _configured = True
    # A vault/SQLite log sink can attach here once the vault lands.
