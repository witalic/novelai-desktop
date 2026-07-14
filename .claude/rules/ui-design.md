---
paths:
  - "frontend/**"
  - "design/**"
---

# UI design — mistakes not to repeat

A ledger of design errors made on this project. Each cost the owner a review round. **Do not repeat
them.** When building or changing any UI, check this list first.

## Process
- **Follow the locked mockup.** If a design is "design locked" in `design/*.html`, build *that*
  structure. Deviating (e.g. rendering a list as canvas nodes when the mockup shows a scrollable
  HTML list) spawns a cascade of bugs and a full rewrite. Re-read the mockup before coding.
- **Verify in the running app, not by reasoning.** For a visual/interaction bug, build + drive the
  app and look — don't guess a fix from the code. (`python run.py --web`, or the browser preview.)
- **Ask the data-model forks upfront.** Decide contentious model questions (what a thing stores,
  how it's organised) with the owner *before* mocking, not after three revisions.

## Controls & affordances
- **No native form controls.** Never ship a bare `<select>` (it pops an OS-styled menu) or native
  scrollbars. Use the token-styled dropdown pattern; scrollbars are themed globally in `tokens.css`.
- **Labels beat cryptic glyphs for non-obvious actions.** A bare `○/◉`, a lone `+`, or two `+`
  side-by-side don't communicate. Use a text label (or text+icon) for anything a user can't infer at
  a glance. Reserve icon-only for universally-understood actions (✕ close, ✎ edit, 🗑 delete).
- **Never two identical-looking controls with different meanings.** A favourite ★ and a "set
  default" ★ in the same card is a trap — the owner will click the wrong one. Give distinct
  meanings distinct forms.
- **Don't remove/hide a control the owner agreed on.** Collapsing an agreed labelled dropdown down
  to a bare caret is a regression, not a simplification.

## Layout
- **Pin action rows to a footer; don't let them shift with content.** Cards/panels with varying
  content (e.g. different chip counts) must keep their actions in a bottom-pinned footer
  (`display:flex;flex-direction:column` + `margin-top:auto` on the actions), always visible — not a
  hover-reveal that reserves/varies layout height. Cards in a row should stretch to equal height so
  footers align.
- **Content scrolls; fixed heights must not clip.** A list/panel that can overflow gets its own
  scroll area. A resizable element's min-size must follow its content (can't be dragged smaller than
  what it holds).
- **Consistent control heights in a row.** Search + dropdown + button on one line must share
  font-size and vertical padding, or they read as misaligned.
- **Collapse with `v-show`, not `v-if`.** `v-if` destroys DOM state (scroll position, open panels,
  input focus) on collapse; `v-show` preserves it.

## Images
- **Never CSS-shrink a full-res image into a small box** (thumbnails, cards, nodes). A big source
  single-step-crushed into a ~150px `<img>` looks soft/pixelated on HiDPI. Two levers, use both:
  - **Vault images:** request a server-sized thumbnail (`?w=<≈ box × devicePixelRatio × 2>`). **But in a
    grid of many, `×2` is too big** — see the many-thumbnails note below; size near the display (`~×1.5`).
  - **Fresh `data:` URLs** can't be server-resized, and `?w=` does nothing for them — so make the
    `<img>` **decode at ~2× the box** (`width/height:200%`) and shrink it back with a transform;
    the decode is high-res and the GPU downscale is crisp. This is the canvas pipeline's trick
    (`useImagePipeline.ts` `.imgfull` + transform) — reuse it for any small image box, not just nodes.
  - **Many fresh `data:` URLs at once** (the generation stack — up to 50): decode-at-2× is **not
    enough**. Chromium downsamples decoded bitmaps under its decode-memory budget → pixelation
    regardless of the CSS trick (vault `?w=` thumbnails escape it because the source is already small).
    Downscale each source **once, client-side** (canvas → small `data:` URL, `canvas/thumb.ts`) and
    render the small copy. Bit me twice; the CSS trick alone masked it until the stack grew.
  - **Many vault `?w=` thumbnails at once** (a Works/gallery grid): the same decode-memory crush hits here,
    and **oversizing makes it worse**. `?w = box × dpr × 2` renders one tile sharp but, across a grid, each
    thumbnail is ~2.6× its display box → Chromium sub-samples them all under budget → the whole grid
    pixelates (one image in the previewer stays sharp — the tell). Size thumbnails **near the display box**
    (`~box × dpr × 1.5`, snapped to buckets): crisp *and* light enough that 30+ tiles fit the budget. Bigger
    is *not* sharper here. `GalleryStack.vue` `thumbSrc`. Cost me two wrong rounds chasing "sharper = bigger."

## Canvas (Vue Flow) nodes
- **Interactive elements inside a node must not select/drag the node.** Vue Flow selects a node on
  `click` (a separate event from `mousedown`/`pointerdown`) and drags from `pointerdown`. Stop
  propagation on all three — but **only when the event started on an interactive element** (button,
  input, textarea, select, a, row), so empty areas of the node still behave like the node (select,
  drag). A blanket `.stop` on the container kills the node's own affordances.
- **Header-only vs body drag is a real choice.** `dragHandle` restricts dragging to one selector;
  don't set it if the owner wants dragging from empty body areas too — rely on the selective
  propagation-stop above instead.
