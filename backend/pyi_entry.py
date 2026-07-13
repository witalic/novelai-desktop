"""PyInstaller entry point for the frozen sidecar — same as ``python -m app`` (uvicorn), but as a
standalone binary the packaged Electron app spawns (no Python/venv on the user's machine)."""
import os
import sys

# A windowed (console=False) PyInstaller build has no console, so sys.stdout/sys.stderr are None. uvicorn
# and the logging setup write to them on startup, which would raise "Unhandled exception in script" and pop
# an error dialog. Point them at the null device so the sidecar runs silently instead of crashing. Dev and
# console builds keep their real streams (these are only None under the windowed freeze).
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")  # noqa: SIM115 — process-lifetime sink, no close needed
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")  # noqa: SIM115

from app.__main__ import main

if __name__ == "__main__":
    main()
