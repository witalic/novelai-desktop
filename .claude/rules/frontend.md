---
paths:
  - "frontend/**"
---

# Frontend (Vue 3 SFC)

The single-page UI FastAPI serves at `/app/`. Vue 3 + Vite + TypeScript, Composition API (`<script setup>`),
`@vue-flow/core` for the canvas composer. Runs inside Electron (per-launch cookie auth on `/api`) and, in
`--web` dev, in a plain browser.

- **Run / test / build** (from `frontend/`):
  - dev: `npm run dev` · typecheck: `npm run typecheck` (`vue-tsc --noEmit`) · test: `npm test` (vitest)
  - build: `npm run build` — runs `vue-tsc --noEmit` **then** `vite build`; type errors fail the build.
  - `python run.py` rebuilds this before launching, so a stale `frontend/dist` never ships. Restart-changed-nothing? Rebuild.
- **Structure:** `views/` (Generate · Works · Library · Settings) · `components/` · `composables/` (useAutosave,
  useImagePipeline, useToast, useConfirm, useTheme) · `vault/` (serialize + ids). `api.ts` is the single
  backend client (throws `ApiError`); `types.ts` holds shared shapes.
- **The vault recipe is a contract.** `vault/serialize.ts` (`canvasToWork` / `workToCanvas`) is the round-trip
  between the canvas and the persisted `WorkDoc`. Persist domain data only — strip transient UI state
  (`expanded`/`editing`, drag flags) via the whitelist; a snapshot must stay reproducible (resolved text +
  params + seed at generation time). Round-trip is covered by `serialize.test.ts` — keep it green when the
  schema changes, and add a migration when the shape changes (see `ROADMAP.md` Phase 1).
- **Autosave is dirty-flagged, not per-keystroke** (`useAutosave.ts`): saves coalesce onto one in-flight
  Promise; `flush()` returns a boolean callers await before New-work / leave / quit. Don't re-save on every
  edit — heavy works would thrash. After a save, just-persisted `data:` URLs are swapped to vault URLs.
- **Images:** never inflate `data:` base64 for display sizing — request server-sized thumbnails (`?w=`) and
  size decode to the node × DPR (`useImagePipeline.ts`). Big works cull off-viewport nodes.
- **Conventions:** `<script setup lang="ts">`; avoid `any` without a reason (vault-layer `WorkDoc` typing is a
  known Phase-1 gap, not a licence to spread it); UI copy in English; theme via CSS tokens, no hard-coded colors
  (`design-prefs`). Tests live beside code as `*.test.ts` (vitest, node env).
