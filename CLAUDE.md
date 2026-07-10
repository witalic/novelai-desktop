# CLAUDE.md

Lean operational core for novelai-desktop. Domain specifics live as path-scoped rules in
`.claude/rules/` (loaded only when matching files are in play); `code-style`, `security`, and
`git` load every session. Full architecture/design: `README.md`.

## What this is
A desktop app (Electron) wrapping the **NovelAI image API**, with an integrated Obsidian-like
**vault** for prompts/works (files on disk + a rebuildable SQLite index) and a planned **Claude**
integration for authoring prompts. Scope for now: **images only**.

Image generation, the vault (works + Library blocks/categories + multi-vault manager + thumbnails),
the Vue-Flow canvas composer, and the Electron shell are **implemented and tested**; Claude
prompt-authoring is deferred. The full phased plan is `ROADMAP.md`; current phase = **Phase 1
(recipe as a typed, versioned contract)**.

## Working rules
- **Language:** converse in Ukrainian; code, comments, UI copy, and docs in English. `README` may be Ukrainian.
- **Gates:** agree the plan before writing code. Commit only logically-complete, **tested** blocks — never a
  partial sub-step or a snapshot with dangling references — and only after the owner confirms. See `rules/git.md`.
- **Greenfield:** grow rules as real code lands — don't invent conventions for files that don't exist yet.

## Never
- **Never print/commit secrets** — the NovelAI token and Anthropic key live in the OS keychain, never in files, logs, or git. (→ `rules/security.md`)
- **Never hammer the NovelAI API** — it is unofficial; respect rate limits and Anlas cost. (→ `rules/novelai-api.md`)

## Architecture (implemented)
Electron shell → spawns a **FastAPI** (Python) sidecar on a free loopback port → talks to NovelAI, owns the vault, serves the built UI. Single origin, no CORS.
- **Backend** (`backend/app/`): FastAPI · `httpx` (async) NovelAI client (real + mock) with SSE stream · Pillow for PNG metadata/disk-cached thumbnails · SQLite index over an on-disk vault. Modules: `novelai/`, `routers/`, `vault/`, `appconfig.py`, `settings.py`, `keychain.py`, `main.py`. (`ai/` — Claude — not built yet.)
- **Frontend** (`frontend/src/`): Vue 3 + Vite + TS (`<script setup>`), `@vue-flow/core` canvas. `views/` (Generate/Works/Library/Settings), `components/`, `composables/`, `vault/` (serialize/ids). Built static, served by FastAPI at `/app/`.
- **Shell** (`shell/`): Electron loads `http://127.0.0.1:PORT/app/`. Per-launch cookie-delivered secret guards `/api`, CSP + nav lockdown + deny-all permissions, health-sig backend-identity check. IPC over localhost HTTP + WS (generation progress; Claude streaming later).

## Commands
- Python runs via `.venv` — always call `.venv\Scripts\python.exe` (system python lacks deps).
- **Run app:** `python run.py` (rebuilds frontend → launches Electron) · `--web` (backend + browser) · `--backend` (backend only) · `--no-build` (skip rebuild).
- **Backend tests:** `.venv\Scripts\python.exe -m pytest backend` (pytest + pytest-asyncio, offline via `httpx.ASGITransport`).
- **Frontend** (`cd frontend`): `npm run dev` · `npm run build` (`vue-tsc --noEmit && vite build`) · `npm test` (vitest) · `npm run typecheck`.
- **Backend deps:** `.venv\Scripts\python.exe -m pip install -e backend[dev]`.

## Tooling (`.claude/`)
- **Rules** (`.claude/rules/`): `code-style` + `security` + `git` (always) · `backend`, `frontend`, `novelai-api` (path-scoped). More added as code lands.
- **Agents:** planning → `analyst`, review → `code-reviewer`.
- **Hooks:** `guard-bash` blocks secret leakage (note: false-positives on `vault` in a path — stage specific subpaths).
