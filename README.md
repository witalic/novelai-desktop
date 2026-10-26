# novelai-desktop

A desktop app (Electron) that wraps the **NovelAI image API** with an integrated, Obsidian-like
**vault** for your prompts and works — every generation and its exact recipe are saved to disk in a
structured, searchable store as you create.

> **Status:** v1.x. Image generation, the vault (works · block/category library · multi-vault
> manager · thumbnails), the Vue-Flow prompt-composition canvas, a structured **gallery** for each
> work, saved **block combinations**, a full **Works** browser with view/edit, **agent access over
> MCP**, and the Electron shell are implemented and covered by tests. Scope for now is **images only**.
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
- **Block combinations** — save the station's block set as a named combination in the work, then
  replace or extend the station with it in one click.
- **Agent access (MCP)** — let Claude Code or any MCP agent work with your vault: browse it, write
  blocks and combinations, tag images, arrange galleries, generate — under per-action switches and
  Anlas limits ([below](#connecting-an-ai-agent-mcp)).
- **Structured gallery** — each work's images organise into a composable stack of typed blocks
  (sections · headings · notes · image grids/albums · metadata) with an outline and a Quick-access
  bin, instead of a loose pile. *Set as cover* picks the image the work shows in the Works list.
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
│   ├─ agent/    — MCP listener for AI agents (opt-in, loopback :8790)
│   └─ routers/  — /api/* + /health
│                               │  serves the built Vue static at /app/
└──────────────┬────────────────┘
               ▼
         NovelAI image API
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
│     ├─ agent/      # MCP listener: tools, permissions, activity, change notices
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

## Connecting an AI agent (MCP)

AI agents on your computer — Claude Code, Claude Desktop, any MCP client — can work with your vault
while the app is open. It is **off by default**.

1. In **Settings → Agents**, switch on *Allow agents to connect*. The app starts a
   loopback-only listener at `http://127.0.0.1:8790/mcp` (if that port is taken, the next free one up
   to 8809 — the page shows the address in use).
2. Copy the ready command from that page and run it in a terminal. For Claude Code it looks like this
   (the copied one carries the real token):

   ```bash
   claude mcp add --scope user --transport http novelai http://127.0.0.1:8790/mcp --header "Authorization: Bearer <agent token>"
   ```

   The **agent token** is a separate key just for agents, kept in the OS keychain — not your NovelAI
   token, which agents never see. *Regenerate* issues a new one and disconnects every agent.
3. Start a new agent session and ask it to work with your Library or works.

**What agents may do** is set per kind of action; everything but deleting is on by default:

| Switch | Tools |
|---|---|
| Browse the Library and works | `search_blocks` `get_blocks` `list_categories` `list_tags` `list_works` `get_work` `list_images` (with the combination each image was made from) `search_images` `get_image` (whole or a close-up crop) `contact_sheet` `get_gallery` `list_combinations` `list_presets` `get_catalog` `get_account` `count_tokens` `estimate_cost` |
| Create and edit blocks | `create_blocks` (the Library import's rules — duplicates are skipped, a new category name makes the category) `update_block` |
| Delete blocks *(off)* | `delete_blocks` |
| Create and edit works | `create_work` `rename_work` `update_work_params` (the station's generation settings, or a preset) `star_blocks` `update_images` `edit_gallery` (sections, headings, text, albums, Quick access, the cover) `move_images` (to another work, or a copy) |
| Create and edit combinations | `create_combination` `update_combination` |
| Delete works, images and combinations *(off)* | `delete_work` `delete_images` `delete_combination` |
| Generate images | `generate_images` (from a combination or blocks, with the work's settings or a preset's; the prompt is built as the station builds it, *unique tags* included) |

**Anlas.** Free generations (Opus: up to 1024×1024 px, ≤ 28 steps, one image) always run while
*Generate images* is on. A paid one runs only when you ask the agent for it (the call says
`paid: true`) **and** *Allow paid generations* is on — within a per-generation cap and a daily budget;
the page shows what was spent today. Over a limit the call is refused, never queued. Agent
generations run one at a time with a pause in between and land in the work's gallery under
**Quick access**, each with its recipe.

**Nothing happens silently.** Every agent action — reads too, and every refused call — is listed
in **Settings → Agent activity** (the last 200, until the app restarts); while you are elsewhere, a
badge on *Settings* counts what's new. Changes show up in the open app at once with a toast, and an agent's edits of
the work you have open merge with your unsaved ones instead of overwriting them.

## Security & privacy

- **Secrets live only in the OS keychain.** The NovelAI persistent token and the (future) Anthropic
  key are stored via `keyring` — never in source, logs, or git.
- **Token, not password.** The app authenticates to NovelAI with a persistent account token, so it
  never handles your password.
- **The local API is not exposed** to the machine — loopback-only, guarded by a per-launch cookie
  secret (see Architecture).
- **The vault is private.** Prompts and images are your content; they are never sent anywhere except
  the API they are bound for, and never committed to version control.
- **Agent access is opt-in and local.** The MCP listener binds loopback only, refuses browser
  requests (any `Origin`), and needs the agent token. What an agent reads goes on to its model
  provider — which is why every read is logged in the app.
- **Good API citizen.** The app respects NovelAI rate limits (backs off on 429) and Anlas cost
  (402 = out of Anlas); it never generates in tight loops.

## License

Personal project — no license granted yet.
