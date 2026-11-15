"""The log file: the installed app's sidecar has no console, so the file is its only record."""
import logging
import sys

from app.logging_setup import file_handler, log_file
from app.settings import Settings, VaultSettings


def test_the_log_goes_to_a_rotating_file_in_the_cache_dir(tmp_path, monkeypatch):
    settings = Settings(vault=VaultSettings(state_dir=str(tmp_path)), _env_file=None)
    assert log_file(settings) == tmp_path / "logs" / "backend-dev.log"  # a dev run's own file
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    handler = file_handler(settings)
    assert handler is not None and log_file(settings) == tmp_path / "logs" / "backend.log"  # the installed app's
    try:
        handler.emit(logging.LogRecord("app.novelai.client", logging.WARNING, __file__, 1,
                                       "Connecting to NovelAI failed (%s) — retrying once", ("ConnectError",), None))
    finally:
        handler.close()
    line = log_file(settings).read_text("utf-8")
    assert "WARNING" in line and "Connecting to NovelAI failed (ConnectError) — retrying once" in line


def test_a_log_file_that_cant_be_written_leaves_the_console(tmp_path):
    blocker = tmp_path / "logs"
    blocker.write_text("a file where the folder should be", "utf-8")
    assert file_handler(Settings(vault=VaultSettings(state_dir=str(tmp_path)), _env_file=None)) is None
