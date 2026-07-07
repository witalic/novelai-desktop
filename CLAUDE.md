# CLAUDE.md

Lean operational core for novelai-desktop. Domain specifics live as path-scoped rules in
`.claude/rules/` (loaded only when matching files are in play); `code-style`, `security`, and
`git` load every session. Full architecture/design: `README.md`.

## What this is
A desktop app (Electron) wrapping the **NovelAI image API**, with an integrated Obsidian-like
**vault** for prompts/works (files on disk + a rebuildable SQLite index) and a **Claude**
integration for authoring prompts. Scope for now: **images only**.

## Working rules
- **Language:** converse in Ukrainian; code, comments, UI copy, and docs in English. `README` may be Ukrainian.
- **Gates:** agree the plan before writing code. Commit only logically-complete, **tested** blocks — never a
  partial sub-step or a snapshot with dangling references — and only after the owner confirms. See `rules/git.md`.
- **Greenfield:** grow rules as real code lands — don't invent conventions for files that don't exist yet.

## Never
- **Never print/commit secrets** — the NovelAI token and Anthropic key live in the OS keychain, never in files, logs, or git. (→ `rules/security.md`)
- **Never hammer the NovelAI API** — it is unofficial; respect rate limits and Anlas cost. (→ `rules/novelai-api.md`)

## Architecture (brief, target)
Electron shell → spawns a **FastAPI** (Python) sidecar → talks to NovelAI + Anthropic, owns the vault.
- **Backend** (`backend/`): FastAPI · `httpx` (async) for NovelAI + Claude · Pillow for PNG metadata/thumbnails · SQLite index over an on-disk vault.
- **Frontend** (`frontend/`): Vue 3 + Vite + TypeScript (SFC). Built to static, served by FastAPI at `/app/`.
- **Shell** (`shell/`): Electron loads `http://127.0.0.1:PORT/app/`. IPC is localhost HTTP + WebSocket (WS for generation progress + Claude token streaming).

## Commands
- (TBD as the toolchain lands.) Python runs via `.venv` — always call `.venv\Scripts\python.exe` (system python lacks deps).

## Tooling (`.claude/`)
- **Rules** (`.claude/rules/`): `code-style` + `security` + `git` (always) · `novelai-api` (path-scoped). More added as code lands.
- **Agents:** planning → `analyst`, review → `code-reviewer`.
- **Hooks:** `guard-bash` blocks secret leakage.
