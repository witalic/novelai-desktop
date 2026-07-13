# Roadmap — novelai-desktop

The through-line: the product's core is **vault → snapshot → reproducible recipe**. Everything else
(galleries, import, LLM, sharing, a social layer) consumes that core, so it stabilises and is pinned as
a contract first, and only then grows features. Phases are ordered by dependency; each has an exit
criterion.

---

## Phase 0 — Stabilisation (fix what undermines trust in the core)

Goal: no path to silent data loss; the local API is not a weapon against the user.

- Autosave race (C1): `dirty` clears only if `changeKey()` did not change across the `await`; a
  `markDirty` mid-save sets `pendingResave`.
- After a successful save, rewrite saved images' `data:` URLs to vault URLs — kills multi-MB flushes and
  narrows the C1 window.
- `vaultReady` re-read on `onActivated` / after configuring a vault; keydown handler tied to
  `onActivated`/`onDeactivated`.
- Vault move: containment checks on dst/src; refuse move/delete of the active vault.
- Per-launch shared secret shell ↔ sidecar: env → cookie → reject without it; a `/health` identity sig
  (also closes CSRF/rebinding and the port TOCTOU).
- `httpx` in runtime deps; full id in the work directory name; a lock on index create/rebuild +
  `INSERT OR IGNORE` for tags.
- A catch-all in the SSE generator + a `mock` flag in stream events; `reader.cancel()` in a `finally`
  on the frontend.

**Exit:** every Critical/High from review closed; the first-run concurrency and autosave-race tests green.

> ✅ **Closed.** Two independent code-review passes were worked through end-to-end.

---

## Phase 1 — The recipe as a contract (the foundation for everything after)

Goal: the work/snapshot schema is a typed, versioned, public-in-spirit format.

- Type `WorkDoc`: `CanvasNode`, `PersistedImage`, `Snapshot`, `Block` — TS interfaces + Pydantic models,
  kept in sync by hand. `vue-tsc --noEmit` in the build.
- An explicit schema split: **domain** (prompts, params, images, snapshots) / **layout** (positions,
  zones, sizes) / **transient** (never persisted). A whitelist in `serialize.ts`, not "save all of data".
- Zone semantics: zones define *role*, not *survival* — the whole canvas serialises; anything out of a
  zone lives as scratch.
- Snapshot freeze semantics: pin the invariant "a snapshot holds resolved text + params + seed at
  generation time" plus the block refs it was assembled from. Editing a Library block never mutates past
  snapshots.
- Fix the multi-sample seed: per-sample `seed + k` in final SSE events and snapshots.
- `schema_version` + a minimal migration mechanism (linear `migrate_N_to_M` functions on the backend,
  applied at read time).
- Persist images' `ar`; restore drafts' `created_at`.
- Unit tests for `serialize.ts` round-trip properties (canvas → doc → canvas, lossless).

**Exit:** a recipe saved today is guaranteed to read and reproduce after any future schema change;
round-trip tests green.

> ✅ **Closed 2026-07-10.** Read-time migrations (`vault/migrate.py`), the domain/layout/transient
> whitelist in `serialize.ts`, scratch persistence (`Image.role`), persisted `ar`, server-owned block
> version bump, the freeze invariant and the multi-sample seed all under offline tests. Beyond plan:
> zones now define *role*, out-of-zone content lives as scratch (owner decision, pinned in this phase).
> The schema has since reached **v6** (grids own `imageIds`); the server also stamps `schema_version`.

---

## Phase 2 — Structured galleries and widgets

Goal: works are documents, not piles of images; one data model behind every view. The finished
structure becomes the sink for the Phase 3 importer.

- Principle: the canvas gallery widget, Works, and the global gallery are **views over one
  `WorkDoc`/index**, with no state stores of their own.
- Gallery widget: a block structure (headings, notes, descriptions), order and grouping — part of the
  layout layer from Phase 1.
- Prompt widget: search over the vault, filter by tags/categories, drop a block onto the canvas (the
  Library store is already there — categories, tags, examples).
- Global gallery: browse images by tag/work/category over the SQLite index (FTS already exists); list
  virtualization in both the global gallery and the canvas widgets.
- An explicit, visible rule mapping a zone's block order → the tag order in the prompt.
- List races and `useConfirm` close here, since this phase multiplies async lists.

**Exit:** a work with 50+ images and a structured gallery opens/scrolls smoothly; the same image is seen
from the canvas, from Works, and from the global gallery without drift.

> 🟡 **Largely delivered (v1.0, 2026-07-13).** Gallery widget — a block stack (sections · headings ·
> notes · image-grid albums · metadata) with an Outline and Quick access, extracted as the shared
> `GalleryStack`. Prompt widget — a direct vault browser (search, tag/category filters, drop a block).
> The generation station — a per-block freeze state, vault-order category rail, and greying of blocks
> already in the composition. A full **Works** tab — a list (search/sort/delete) plus a full-page
> view/edit over one `WorkDoc` through that same `GalleryStack`, with autosave (optimistic-locked
> against concurrent edits). `useConfirm` and the list races are closed.
> **Remaining:** the global gallery over the index + work filters by tag/content (need the gallery
> indexed into `index.db`); virtualization of very large lists.

---

## Phase 3 — Importer (a vault around existing libraries)

Goal: "drag a folder of old generations, get a structured vault with recipes." The main adoption
channel. Import lands on the finished Phase 2 structure: the result is structured galleries, not a pile
of files.

- Core: PNGs with valid NovelAI metadata — tEXt/iTXt (`Description` = prompt, `Comment` = JSON with
  uc/seed/sampler/steps/scale/model, v4 per-character structures) → a snapshot.
- Second pass: stealth pnginfo (alpha channel) for files with stripped chunks.
- Dedup by file hash; an idempotent re-import of the same folder.
- Provenance: `generated` / `imported` / `orphan` (no recipe). Orphans get their own status and a natural
  queue for enrichment in Phase 4.
- Structure mapping: an imported folder → a structured gallery (grouping by date/model/shared prompt as
  a starting layout the user then edits with the Phase 2 tools).
- Copy-into-vault with a space estimate up front; a background job with status polling, progress, and
  cancel (reuse the move pattern).
- An honest post-import report: how many have a recipe, how many orphans, what failed to parse and why.
- Out of scope for the phase: non-PNG formats, EXIF normalisation — backlog.

**Exit:** importing a folder of several thousand PNGs runs in the background without crashes; each file
either has a recipe or is honestly flagged an orphan; the result shows immediately as structured
galleries.

---

## Phase 4 — LLM assistant (skills)

Goal: a cheap model as a tool over tags and recipes, with Anlas-level transparency.

- A thin provider abstraction (so "Haiku" doesn't grow into the code); the key in the keychain; a
  `backend/app/ai/` module per the target architecture.
- Skills v1 (text, cheap): refactor a prompt, block variations, "idea → a set of tags", critique a
  prompt composition. Token streaming over the existing SSE/WS pattern.
- Transparency as a product requirement: any send off-machine is an explicit user action; a visible
  token-cost counter (the Anlas-transparency pattern).
- Skills v2 (vision, opt-in, batched): auto-tagging and grouping the Phase 3 orphans — the "import +
  enrich" loop.

**Exit:** the user has no background off-machine request; each skill shows exactly what was sent and what
it cost.

---

## Phase 5 — A recipe-exchange format

Goal: the recipe lives outside the app; the foundation of a sharing service without the service itself.

- An open export/import recipe format (JSON with the Phase 1 `schema_version`): resolved text, params,
  seed, optional block refs and a preview.
- Two-way compatibility with NovelAI PNG metadata: export a recipe *into* PNG chunks of our own saves,
  import from any NovelAI image (already there from Phase 3).
- Sharing v0: a recipe as a file/link — valuable with no backend at all.
- Format docs — in English, public: both the spec for a future service and an argument in a conversation
  with Anlatan.

**Exit:** a recipe can be handed to another user as a file, and they reproduce the work (modulo their own
Anlas).

---

## Horizon (beyond the core, decide later)

- A **sharing service** for recipes (text — cheap) → images (expensive: hosting + moderation).
- **Talks with Anlatan** — start early (legalising the unofficial API resolves the main platform risk);
  the pitch is "opt-in sharing on top of a private vault".
- A **social layer** — only after critical mass; moderation: classify *images* (not tags) with a local
  model + community voting only for neutral topics (ratings, featuring), safety decisions with a human
  admin and strict upload rules.

---

## Cross-cutting (every phase)

- Tests ship with the feature, not after: concurrency, serialization round-trips, stream error paths.
- Every schema change is a migration + a migration test.
- Secrets: keychain-only; transparency of off-machine sends — invariant.
- Commit discipline per `rules/git.md`: a phase is a series of logically-complete blocks.

## Order and dependencies

```
Phase 0 ──► Phase 1 ──► Phase 2 ──► Phase 3 ──► Phase 4 (vision part)
                │                        └──► Phase 5
                └──► Phase 4 (text skills)
```

The Phase 4 text skills, after Phase 1, can run in parallel with 2–3; Phase 5 depends on 1 and 3. The
shortest path to "a product worth showing the community" is 0 → 1 → 2 → 3 — structure first, so the
importer becomes the demo hook "your old generations become a structured library in a minute", not a
pile of files.
