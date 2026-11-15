"""``python -m app`` entrypoint: run the sidecar with uvicorn.

Port contract: Electron passes ``NAI_API__PORT`` in the child env. ``--port`` is a manual-dev override.
"""
import argparse
import logging

import uvicorn

from app.logging_setup import setup_logging
from app.settings import get_settings

log = logging.getLogger("app")
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def main() -> None:
    parser = argparse.ArgumentParser(prog="app")
    parser.add_argument("--port", type=int, default=None, help="override the bind port (manual dev)")
    args = parser.parse_args()

    settings = get_settings()
    host = settings.api.host
    port = args.port if args.port is not None else settings.api.port

    setup_logging(settings)
    if host not in _LOOPBACK:
        log.warning("Binding to non-loopback host %s — the backend has no auth and the vault is private.", host)
    log.info("Starting novelai-desktop backend on http://%s:%d", host, port)

    # Pass the app object, not the "app.main:app" import string: the string form makes uvicorn re-import the
    # module by name, which fails inside a PyInstaller-frozen binary. We use neither reload nor workers, so
    # the object form is equivalent.
    from app.main import app
    # The open app holds an endless event stream (/api/agent/events): a bounded graceful shutdown ends it instead
    # of waiting on it forever. No log_config: uvicorn's own loggers (startup failures, a request's traceback) then
    # reach ours — the console and the log file; no access log, one line per thumbnail would flood the file.
    uvicorn.run(app, host=host, port=port, log_level=settings.log.level.lower(), timeout_graceful_shutdown=3,
                log_config=None, access_log=False)


if __name__ == "__main__":
    main()
