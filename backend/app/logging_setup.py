"""Logging setup: the console, and a rotating file the installed app keeps — its shell starts the sidecar without a
console, so the file is the only record of what went wrong. Operational output goes through ``logging``, never
``print`` (``rules/code-style.md``)."""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.appconfig import cache_dir
from app.settings import Settings

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_FILE_BYTES = 1_000_000
_FILE_BACKUPS = 3
_configured = False

log = logging.getLogger(__name__)


def force_utf8_console() -> None:
    """Make stdout/stderr UTF-8 so Cyrillic log lines don't crash on a cp1251 Windows console."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="backslashreplace")


def log_file(settings: Settings) -> Path:
    """Where the log goes: this machine's cache dir (local, never roamed or kept in a vault). A dev run (``python
    run.py``) keeps a file of its own: running beside the installed app, the two would share one, and on Windows
    neither could roll it over while the other holds it open — every record after that would be lost."""
    name = "backend.log" if getattr(sys, "frozen", False) else "backend-dev.log"
    return cache_dir(settings) / "logs" / name


def file_handler(settings: Settings) -> logging.Handler | None:
    """A size-capped, rotating handler on ``log_file`` — or None when it can't be written (the console still logs)."""
    path = log_file(settings)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=_FILE_BYTES, backupCount=_FILE_BACKUPS, encoding="utf-8")
    except OSError as exc:
        log.warning("Can't write the log file %s: %s", path, exc)
        return None
    handler.setFormatter(logging.Formatter(_FORMAT))
    return handler


def setup_logging(settings: Settings) -> None:
    """Idempotent root logging config: a console handler and the log file, at the configured level."""
    global _configured
    force_utf8_console()
    root = logging.getLogger()
    root.setLevel(settings.log.level.upper())
    if _configured:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
    if (to_file := file_handler(settings)) is not None:
        root.addHandler(to_file)
    _configured = True
