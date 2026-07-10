---
paths:
  - "backend/**"
---

# Backend (FastAPI sidecar)

The sidecar Electron spawns. Serves the API and the built frontend single-origin at `/app/`.

- **Run / test** (from repo root; `.venv` lives at the root):
  - install: `.venv\Scripts\python.exe -m pip install -e backend[dev]`
  - run: `.venv\Scripts\python.exe -m app` (add `--port N` for a manual-dev override)
  - test: `.venv\Scripts\python.exe -m pytest backend`
- **Port contract:** Electron chooses a free loopback port and passes it as env `NAI_API__PORT`; the sidecar
  binds `settings.api.host` (default `127.0.0.1`). Electron polls `GET /health` until 200 before loading `/app/`.
- **Config:** `app/settings.py` (pydantic-settings, prefix `NAI_`, nested `__`, optional `backend/.env`).
  **Secrets never go in Settings or `.env`** — they belong in the OS keychain (`rules/security.md`); the keychain
  accessor lands with the first module that reads a secret (the NovelAI client), not before.
- **Logging, not print** (`rules/code-style.md`) — via `app/logging_setup.py`; console is forced UTF-8.
- **Single origin, loopback-only, guarded:** no CORS. The Electron shell provisions a per-launch secret
  (env `NAI_API__AUTH_TOKEN`) and sets it as a `SameSite=Strict; HttpOnly` `nai_auth` cookie; a global
  middleware then requires that cookie on every `/api` request and rejects non-loopback `Host` headers
  (anti DNS-rebinding). `/health` stays open (polled before the UI loads). With no token provisioned
  (dev `python -m app`, tests) the guard is a pass-through. A non-loopback bind logs a WARNING.
- **Feature seams:** new features attach as routers under `app/routers/` and packages `app/novelai|vault|ai/`
  created when their code lands — no empty stub packages.
- **Tests are offline** — `httpx.ASGITransport`, no network, no real keychain.
