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

    uvicorn.run("app.main:app", host=host, port=port, log_level=settings.log.level.lower())


if __name__ == "__main__":
    main()
