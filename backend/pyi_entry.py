"""PyInstaller entry point for the frozen sidecar — same as ``python -m app`` (uvicorn), but as a
standalone binary the packaged Electron app spawns (no Python/venv on the user's machine)."""
from app.__main__ import main

if __name__ == "__main__":
    main()
