# Agent MCP: missing capabilities for full generation and gallery control

A brief for whoever extends the agent listener (`backend/app/agent/`). It lists what an external agent
could **not** do in a real session (building the "Makima" work: 6 pose/outfit variations from a user
preset, 4 paid images each, the best 3 per variation curated into the gallery). Each gap names where
the app already has the feature, so the MCP tool wraps an existing service instead of inventing one.

## Decisions and progress

Owner decisions (2026-10-25):

- **A never-opened work (#4):** the backend writes a minimal gallery zone, and the app fills in the other
  widgets the first time it opens the work (option b).
- **Combinations and presets (#8):** no link for now. Combinations stay block sets; an agent passes
  `preset_id` when it generates, and `update_work_params` sets the station.
- **Optional items taken:** delete a work (#18), move or copy images (#19), the contact sheet (#14). The
  server-side series (#17) is not taken.

Landing in blocks, one commit each:

1. **Reads and generation parity — done:** #5, #6, #7, #9, #10, #12, #13, #14, #15, #16, #21, #22.
   - `group` is dropped from agent output, since nothing in the app reads it.
   - A new category follows the Library's "New category": named as given, with the first free palette hue.
     The importer only slugifies the name instead.
   - `get_work` is now an overview, and `list_images` pages the gallery.
2. **Gallery — done:** #1–#4, #11.
   - `get_gallery` reads the layout; `edit_gallery` takes a batch of ops (add / update / move / remove a
     block, place / unplace images, cover). The batch is all-or-nothing, and a new block can be named by a
     `ref` later in the same call.
   - Backend and app replay the same operations with the same rules (`agent/gallery.py`,
     `vault/agentOps.ts` `applyGalleryOps`). Moves and placements carry the resulting order rather than a
     neighbour, so a batch replayed again lands the same, and the user's own unsaved blocks or images keep
     their slots.
   - A section moves only before or after another section, never in among its blocks.
   - The cover is now a saved choice: the app keeps it while the image is in the gallery, and the user has
     *Set as cover* (`design/gallery-cover.html`).
   - `create_work` writes an unplaced gallery zone; the app lays out the other widgets around it on first
     open (`serialize.ts` `healAnchors`). An emptied gallery no longer re-seeds itself on reload.
   - #11 is derived, not stored: an image belongs to the combinations whose prompt blocks are its own. A
     combination edited since no longer matches.
3. **Station params — done:** #8.
   - `update_work_params` sets the work's station settings: every field but the seed, or a preset (applied
     as the app's Apply does, image count included) with fields on top. Values are typed strictly, and the
     ones being set are checked against what the model offers; a value the station already keeps from
     another model stays, as the app's settings panel keeps it. Only what differs is written, and the
     activity row shows the new values.
   - The open app takes them into its station (a named preset lands whole and becomes the active one); the
     Works editor into its copy.
4. **Housekeeping:** #18, #19, #20.

## What existed in v1.2 (before the blocks above)

21 tools in `backend/app/agent/tools.py` (`TOOLS`), permission-checked per call in `protocol.py`:

- **Reads:** `list_categories`, `list_tags`, `search_blocks`, `get_blocks`, `list_works`, `get_work`,
  `get_image`, `get_catalog`, `list_combinations`, `estimate_cost`.
- **Writes:** `create_blocks`, `update_block`, `delete_blocks`, `create_work`, `rename_work`,
  `update_images` (tags / description / favourite), `delete_images`, `create_combination`,
  `update_combination`, `delete_combination`, `generate_images`.

## Architectural constraints to respect

- **Every work write must replay in the open app.** `_edit_work` saves the doc and records its ops
  (`changes.record_work_edit`); the frontend replays them over its open, possibly unsaved copy in
  `frontend/src/vault/agentOps.ts` (`AgentOp`, `planAgentOps`, `applyAgentOpsToDoc`). A write without
  a matching op type on **both** sides gets overwritten by the app's next autosave. Each new write
  therefore means a new backend op, a new `AgentOp` variant, replay logic, and vitest coverage in
  `agentOps.test.ts`. Replay must be idempotent, because overlapping catch-ups happen.
- **The gallery layout is frontend-owned.** It lives in the work's `canvas`, which the backend stores
  opaquely: the node `type: "zone"` with `data.role: "gallery"` holds `data.blocks: GalleryBlock[]`
  (types in `frontend/src/types.ts`, around lines 211–227). A grid is an album that owns an ordered
  `imageIds`. Each gallery image is in at most one grid, and images in no grid show in Quick access.
  The backend already walks this structure in `_purge_images`.
- **A work created over MCP has no canvas yet.** `_create_work` notes that the app lays out the widgets
  when the work is first opened, so the gallery zone may not exist when an agent wants to arrange it.
- **Generation rules stay as they are:** one at a time, free vs paid, the user's per-request cap and
  daily budget, `paid: true` only with the user's say-so (`generation.py`, `store.py`).
- **Never widen reach without a switch.** New capabilities either sit under an existing permission
  (`PERMISSION_LABELS` in `protocol.py`) or get a new switch. A new switch is a Settings UI change, so
  it starts as a mockup (`design/settings-agent-access.html`) the owner approves.

## Gaps, by priority

### P0: gallery arrangement (blocked the task outright)

1. **Read the gallery layout:** `get_gallery(work_id)` returns the block stack (sections, headings,
   text, dividers, grids with `imageIds` / `cols` / `collapsed`, meta blocks) plus the Quick access ids
   (gallery images in no grid). Today `get_work` says "Canvas layout is not included".
2. **Edit the gallery layout:** `edit_gallery(work_id, ops[])` with granular operations: add / rename /
   remove a section, heading, text or divider; create a grid with a column count; move images into a
   grid at a position; move images back to Quick access; reorder images inside a grid; reorder blocks.
   - **Invariants:** ids are gallery images of this work (never `role: "scratch"`); an image ends up in
     one grid at most; unknown ids are reported, not silently dropped.
   - **Ops, not a whole-list replace,** so the user's unsaved layout edits in the open app survive the
     replay.
   - **Permission:** `works_write`.
3. **Set the work's cover:** `preview_image_id` is set only implicitly (first generated image). Add it
   to the gallery edit or as `set_work_preview`.
4. **Owner decision needed:** what `edit_gallery` does when the work has no gallery zone yet. Option
   (a): refuse with "open the work once". Option (b): the backend writes a minimal gallery zone that
   the frontend adopts on first layout. (b) touches the canvas contract (`vault/serialize.ts`, maybe a
   schema bump with a migration).

### P0: generation-settings parity with the station

5. **Presets.** The user's generation presets (built-in plus `<vault>/presets/*.json`, favourite and
   default flags in `.state.json`, service `backend/app/vault/presets.py`) are invisible to agents. In
   the session the agent had to read the preset file from disk.
   - Add `list_presets` (read).
   - Add `preset_id` to `generate_images` and `estimate_cost`: the preset's params apply, and explicit
     call fields go on top.
6. **Advertise every generation field.** `_GEN_PARAMS` already accepts `noise_schedule` and
   `cfg_rescale`, but `_GEN_FIELDS` (the JSON schema) doesn't list them, so agents can't know.
7. **Unique tags (`dedupe`) isn't applied** by `generate_images` (its description says so). Port
   `frontend/src/canvas/dedup.ts` (`dedupePrompt`) to the backend or share the rule, so agent and
   station prompts match. Then honour the work's or preset's `dedupe`.
8. **Set the work's station params:** `update_work_params` (model, size, steps, scale, sampler,
   noise schedule, CFG rescale, quality toggle, UC preset, dedupe). Needs a new replay op.
   - **Owner decision needed:** combinations hold blocks only, so loading one in the station
     doesn't restore the quality toggle or UC preset the result depends on. Should a combination be
     able to reference a preset? That is a schema change (v8, migration, `serialize.ts`).
9. **Return complete recipes.** `get_work` returns only `_RECIPE_PARAMS` (model, size, steps, scale,
   sampler, seed, n_samples) per image. It omits quality toggle, UC preset, noise schedule and CFG
   rescale, so a recipe can't be reproduced or audited from MCP output alone.

### P1: reading at scale

10. **`get_work` overflows.** It returns every gallery image with its full prompt. On a 100-image work
    the output exceeded the client's tool-result limit, and the agent had to save it to a file and parse
    it.
    - Add `include_images: false`, or a paged `list_images(work_id, tags?, favorite?, page, per_page)`.
    - Return the full prompt only on request.
11. **Link an image to its combination.** Images made from a combination don't say which one. Expose
    the snapshot's combination id and name, or its components' `block_id` and version, so an agent can
    group results per variation without its own bookkeeping.
12. **Cross-work image search.** `GET /api/gallery` (tags, favourite, paging) exists in the app.
    Expose it as `search_images`.
13. **Closer looks.** `get_image` returns a downscaled PNG of at most 1024 px. Spotting a tiny artist
    signature or hand errors needed a full 1024 px fetch. An optional crop region at native resolution
    would be cheaper.
14. **Contact sheet** (nice to have). Reviewing 24 images took 24 `get_image` calls. A tool that
    returns one tiled image of N thumbnails, labelled with their ids, saves round-trips and tokens.

### P1: generation workflow

15. **Account state.** `estimate_cost` reports the user's agent limits and today's agent spend, not
    the Anlas balance or tier. Wrap `GET /api/account/subscription` as `get_account`.
16. **Token budget.** `POST /api/tokenize` exists. Expose `count_tokens(prompt, model)` so an agent can
    check V5 Full (1471), V5 Curated (703) and V4.5 (512) budgets before generating.
17. **Series** (optional). A server-side `generate_series(work_id, combination_id | blocks, count, …)`.
    - Keep all the current rules and add pacing between requests.
    - Stop on 429 / 402.
    - Cap the series spend up front.
    - It removes N round-trips. It must never loosen "never hammer the API".

### P2: works and housekeeping

18. **Delete a work** (under `works_delete`). The app has `DELETE /api/works/{id}`; agents can only
    empty a work image by image.
19. **Move or copy images between works,** for example promoting a test draft into the main work. The
    image's snapshot goes with it, and both works get replay ops.
20. **Per-work starred blocks** (`WorkDoc.favorites`). They are readable (`favorite_block_ids`) but not
    settable.
21. **Categories.** `create_blocks` refuses an unknown category (`_category_of`). The app's importer
    slugifies and creates it (`import_blocks.py`), and the block-authoring skill relies on that
    ("new categories are created automatically on import"). Align them: create on the fly, or add
    `create_category`.
22. **Image `group`** is returned by `get_work` but can't be set. Check whether it is still live now
    that grids own their images. If it is dead, drop it from the output rather than exposing it.

## Out of scope (the app itself lacks these)

img2img, inpainting, vibe transfer, character reference, upscaling, per-character prompts and
positions. Each needs an app feature first; the MCP would only wrap it.

## How to land it

- Read `CLAUDE.md` and `.claude/rules/` (security, novelai-api, backend, frontend, ui-design, git)
  first.
- Agree the plan with the owner before writing code, including the two owner decisions above
  (gallery zone on a never-opened work; combination and preset link).
- Per tool:
  - a schema with limits;
  - a permission;
  - a `Written` result with action and detail (the activity feed and app notice come from it);
  - a replay op in `agentOps.ts`;
  - offline backend tests (`.venv\Scripts\python.exe -m pytest backend`);
  - vitest coverage for the replay.
- Commit only logically complete, tested blocks, after the owner confirms (Conventional Commits).

**Done when an agent can:**
- pick a preset and generate with it;
- read a large work without overflowing;
- see which combination made each image;
- arrange the picks into sections and albums;
- leave the rest in Quick access and set the cover;
- check the account balance.

And the open app must show all of it live, without losing the user's unsaved edits.
