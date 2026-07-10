#!/usr/bin/env python
"""Dev launcher for novelai-desktop.

Rebuilds the frontend first (so ``/app/`` always serves the latest UI — the usual reason a
restart "changes nothing" is a stale ``frontend/dist``), then launches the app.

    python run.py               build the frontend, then launch the Electron desktop app
    python run.py --web         build the frontend, run the backend, open it in a browser
    python run.py --backend     run only the backend (foreground), no browser/Electron
    python run.py --no-build    skip the frontend rebuild (faster restart)

The Electron shell spawns/reuses the backend itself; ``--web`` runs the backend directly.
"""
import argparse
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", 8787
APP_URL = f"http://{HOST}:{PORT}/app/"


def venv_python() -> Path:
    win = ROOT / ".venv" / "Scripts" / "python.exe"
    posix = ROOT / ".venv" / "bin" / "python"
    py = win if win.exists() else posix
    if not py.exists():
        sys.exit(
            "No .venv found. Create it and install the backend:\n"
            "  python -m venv .venv\n"
            "  .venv\\Scripts\\python.exe -m pip install -e backend[dev]"
        )
    return py


def npm() -> str:
    found = shutil.which("npm") or shutil.which("npm.cmd")
    if not found:
        sys.exit("npm not found on PATH — install Node.js to build the frontend / run Electron.")
    return found


def build_frontend() -> None:
    print("• Building frontend → frontend/dist …", flush=True)
    subprocess.run([npm(), "run", "build"], cwd=ROOT / "frontend", check=True)
    print("  frontend built.", flush=True)


def health_ok() -> bool:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/health", timeout=0.5) as r:
            return r.status == 200
    except Exception:
        return False


def run_backend() -> int:
    print(f"• Starting backend on {APP_URL}  (Ctrl-C to stop)", flush=True)
    return subprocess.run([str(venv_python()), "-m", "app"], cwd=ROOT).returncode


def run_web() -> int:
    if health_ok():
        print(f"• Backend already running — opening {APP_URL}", flush=True)
        webbrowser.open(APP_URL)
        return 0
    proc = subprocess.Popen([str(venv_python()), "-m", "app"], cwd=ROOT)
    print(f"• Starting backend on {APP_URL} …", flush=True)
    for _ in range(60):
        if health_ok():
            break
        if proc.poll() is not None:
            return proc.returncode or 1
        time.sleep(0.5)
    else:
        proc.terminate()
        sys.exit("Backend did not become healthy within 30s.")
    print(f"• Opening {APP_URL}", flush=True)
    webbrowser.open(APP_URL)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


def run_electron() -> int:
    print("• Launching Electron app …", flush=True)
    return subprocess.run([npm(), "start"], cwd=ROOT / "shell").returncode


def main() -> None:
    ap = argparse.ArgumentParser(description="Launch novelai-desktop (rebuilds the frontend first).")
    ap.add_argument("--web", action="store_true", help="run backend + open a browser instead of Electron")
    ap.add_argument("--backend", action="store_true", help="run only the backend in the foreground")
    ap.add_argument("--no-build", action="store_true", help="skip rebuilding the frontend")
    args = ap.parse_args()

    if not args.no_build:
        build_frontend()

    if args.backend:
        raise SystemExit(run_backend())
    if args.web:
        raise SystemExit(run_web())
    raise SystemExit(run_electron())


if __name__ == "__main__":
    main()
