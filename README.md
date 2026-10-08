# novelai-desktop

A desktop app (Electron) that wraps the **NovelAI image API** with an integrated, Obsidian-like
**vault** for your prompts and works — every generation and its exact recipe are saved to disk in a
structured, searchable store as you create.

> **Status:** v1.0. Image generation, the vault (works · block/category library · multi-vault
> manager · thumbnails), the Vue-Flow prompt-composition canvas, a structured **gallery** for each
> work, a full **Works** browser with view/edit, and the Electron shell are implemented and covered
> by tests. A Claude prompt-authoring assistant is planned. Scope for now is **images only**.
> The phased plan lives in [`ROADMAP.md`](ROADMAP.md).

---

## Why

NovelAI generates great images, but the loop of *generate → save somewhere → lose the prompt that
worked* is hard to manage. This app stitches generation and storage into one cycle:

- a single, comfortable GUI over NovelAI image generation;
- every work and prompt lands in a structured, searchable store the moment you make it;
- a reproducible **recipe** (resolved prompt + params + seed) travels with every image.

## Features

- **Image generation** through NovelAI (V5 by default, V4.5 as legacy) with full parameter control
  (size, sampler, steps, scale/CFG, seed, negative prompt) and streamed progress. An offline
  **mock mode** lets you work without spending Anlas.
- **Prompt-composition canvas** (Vue Flow): a generation station with category-coloured prompt
  blocks that assemble the prompt live, a **freeze** state to keep a block without sending it, an
  output stack, and one-click save into the vault.
- **Vault** — files on disk (a work is a folder with `work.json` + PNGs + thumbnails) over a
  rebuildable SQLite index: paginated work listing, delete with image garbage-collection, and
  multiple vaults with safe folder moves.
- **Prompt library** — categories, tags, and reusable prompt blocks you drop onto the canvas.
- **Structured gallery** — each work's images organise into a composable stack of typed blocks
  (sections · headings · notes · image grids/albums · metadata) with an outline and a Quick-access
  bin, instead of a loose pile.
- **Works browser** — search, sort, and delete works; open any work in a full-page **view** or
  **edit** mode that reuses the same gallery stack, with autosave.
- **Server-side thumbnails** (`?w=`, disk-cached) with crisp rendering at any zoom, and off-viewport
  node culling for heavy works.
- **Cost transparency** — NovelAI states (out of Anlas / rate-limited) surface directly in the UI.

**Planned** (see [`ROADMAP.md`](ROADMAP.md)): an importer for existing generation folders
(PNG metadata → recipe), a global gallery over the index, a **Claude** prompt assistant, and an open
recipe-exchange format. Out of scope for now: text/video generation, collaboration, cloud sync.

## Architecture

```
┌──────────────────────────────┐
│  Electron shell (shell/)      │  loads the UI at http://127.0.0.1:PORT/app/ (a free port)
│                               │  spawns the sidecar; per-launch cookie secret + CSP + nav lockdown
└──────────────┬────────────────┘
               │ localhost HTTP + WebSocket (WS: generation progress)
┌──────────────▼────────────────┐
│  FastAPI sidecar (backend/)   │
│   ├─ novelai/  — thin image-API client (+ mock, SSE stream)
│   ├─ vault/    — files on disk + SQLite index
│   └─ routers/  — /api/* + /health
│                               │  serves the built Vue static at /app/
└──────────────┬────────────────┘
               ▼
         NovelAI image API       (ai/ — Claude — planned, not built yet)
```

**Key decisions**

- **Single origin.** FastAPI serves both the API and the built frontend at `/app/`; Electron just
  opens that URL. No CORS, one process serves everything.
- **The local API is not open.** The sidecar binds loopback only; the shell mints a fresh secret per
  launch, delivers it as a `SameSite=Strict; HttpOnly` cookie, and the backend requires it on every
  `/api` call and rejects non-loopback `Host` headers. `/health` stays open and echoes
  `sha256(token)` so the shell can confirm it reached its own sidecar.
- **Vault = files + index.** The source of truth is the files on disk, so the store survives losing
  the index. SQLite is only a fast, rebuildable index for listing/tags/search; writes are atomic and
  paths are traversal-safe.
- **Thin, first-party NovelAI client.** There is no official REST API, so the app calls the same
  private endpoint the web client uses behind a small wrapper (no heavy dependency); community
  libraries are used only as a reference for undocumented fields.

## Stack

| Layer | Technology |
|---|---|
| Shell | Electron |
| Backend | Python 3.11+ · FastAPI · `httpx` (async) · Pillow · SQLite · pytest |
| Frontend | Vue 3 + Vite + TypeScript (SFC) · `@vue-flow/core` · vitest |
| External APIs | NovelAI image API (unofficial) · Anthropic (Claude, later) |

## Project layout

```
novelai/
├─ backend/          # FastAPI sidecar (Python)
│  └─ app/
│     ├─ novelai/    # thin image-API client (+ mock)
│     ├─ vault/      # on-disk store + SQLite index
│     ├─ routers/    # /api/* + /health
│     └─ …           # appconfig, settings, keychain, main
├─ frontend/         # Vue 3 + Vite + TS → builds to frontend/dist
│  └─ src/           # views · components · composables · vault (serialize/ids)
├─ shell/            # Electron (main · api · preload · config)
├─ run.py            # dev launcher
└─ ROADMAP.md        # phased plan
```

User data (the vault, generated images) lives **outside git**.

## Getting started

The backend runs from a `.venv` (the system Python lacks the dependencies):

```bash
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e backend[dev]

python run.py              # build the frontend → launch Electron
python run.py --web        # backend + browser
python run.py --backend    # backend only
python run.py --no-build   # skip the frontend rebuild

.venv\Scripts\python.exe -m pytest backend   # backend tests (offline)

cd frontend
npm install
npm run dev                # Vite dev server
npm run build              # vue-tsc --noEmit && vite build
npm test                   # vitest
```

You need a NovelAI account with an active subscription (Anlas) to generate real images; without a
token the app runs in mock mode. Add your **persistent API token** (NovelAI → Account → Get
Persistent API Token) in the app's Settings — it is stored in the OS keychain, never in files.

## Security & privacy

- **Secrets live only in the OS keychain.** The NovelAI persistent token and the (future) Anthropic
  key are stored via `keyring` — never in source, logs, or git.
- **Token, not password.** The app authenticates to NovelAI with a persistent account token, so it
  never handles your password.
- **The local API is not exposed** to the machine — loopback-only, guarded by a per-launch cookie
  secret (see Architecture).
- **The vault is private.** Prompts and images are your content; they are never sent anywhere except
  the API they are bound for, and never committed to version control.
- **Good API citizen.** The app respects NovelAI rate limits (backs off on 429) and Anlas cost
  (402 = out of Anlas); it never generates in tight loops.

## License

Personal project — no license granted yet.
