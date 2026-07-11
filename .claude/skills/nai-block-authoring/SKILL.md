---
name: nai-block-authoring
description: Turn prompts/works into reusable NovelAI library blocks as import-ready JSON for this app. Use when preparing block files to import into the vault (categories, tags, NSFW flagging, polarity, JSON shape). Pairs with the nai-prompt-writing skill for the tag text itself.
---

# Authoring library blocks

A **block** is a small, reusable, self-contained group of related tags for **one concept** — a
character, an outfit, a lighting setup, a pose, an art style. Blocks are mixed and matched on the
canvas, so each one must stand alone and stay focused.

- ✅ One logical unit: `hot pink babydoll, sheer, lace, garter belt, thigh highs`
- ❌ A whole scene in one block (character + outfit + pose + background) — split it into several blocks.
- ❌ A single lone tag with nothing related to it — a block groups the tags that belong together.

Write the tag `text` itself with the **nai-prompt-writing** skill (visual Danbooru tags, correct
weights, no narrative/duplication).

## Output: import-ready JSON

Emit a JSON **array** of block objects (or one object). Do **not** set an `id` — the importer assigns
a fresh one. Shape:

```json
{
  "category": "outfit",
  "name": "Hot pink babydoll",
  "text": "1.3::babydoll::, hot pink babydoll, sheer, lace, garter belt, white thigh highs",
  "polarity": "positive",
  "tags": ["lingerie", "babydoll", "hot pink", "thigh highs"]
}
```

| field | rule |
|---|---|
| `category` | one slug (see below). |
| `name` | short human label for the library (2–5 words), e.g. "Hot pink babydoll". |
| `text` | the prompt tags — authored per **nai-prompt-writing**. |
| `polarity` | `"positive"` (default) or `"negative"` (undesired-content blocks). |
| `tags` | top-level descriptors — see the tags rule. |

## Categories

Pick the slug that names the block's primary role. Slugs are prefixed by group (`body-*`, `outfit-*`,
`scene-*`, `nsfw-*`):

| slug | for |
|---|---|
| `character` | a specific character / person + defining traits |
| `body` | body type / physique / proportions / anatomy |
| `body-skin` | skin tone / finish / rendering (porcelain, glossy, tan, subsurface) |
| `body-hair` | hairstyle / color / detail |
| `body-face` | face shape, eyes, expression |
| `body-state` | physical states — wet, blushing, sweat, steam, tears, flushed |
| `outfit` | garments / clothing sets / lingerie |
| `outfit-fabric` | material / texture (silk, lace, satin, wet fabric) |
| `outfit-accessory` | jewelry, glasses, hats, worn props |
| `pose` | body posture / gesture |
| `action` | activity / interaction (running, embracing) |
| `composition` | subject count / framing (1girl, solo, duo) |
| `scene-environment` | location / background / setting |
| `scene-lighting` | light setup / mood |
| `scene-camera` | shot / angle / lens / framing |
| `scene-effects` | visual effects (bokeh, chromatic aberration, depth of field, particles) |
| `scene-color` | color palette / grading |
| `style` | art style / rendering / artist signature |
| `nsfw`, `nsfw-act`, `nsfw-fluids` | explicit content — see NSFW below |
| `negative` | undesired-content blocks (always `polarity: "negative"`) |

Rules:
1. **Misfit → `custom`.** A coherent unit that fits none of the above goes in `custom` (still a focused
   block, not a dumping ground).
2. **Explicit request wins.** If the user asks for a specific category (even a new slug), use it verbatim —
   new categories are created automatically on import.

## Naming: style-specific vs general

A block is one of two kinds — name and tag it accordingly:
- **Style-specific** — the block belongs to / defines one particular style (its anchor, its signature
  rendering choices). Prefix the `name` with the style and tag it: `"Nixeu — skin rendering"`, tag `nixeu`.
- **General / reusable across styles** — a standard, swappable option (a common lighting mood like *golden
  hour*, a generic material like *lace*, a generic body-state like *wet*, a generic effect). Give it a
  **neutral name** (`"Golden hour"`, `"Lace & sheer fabric"`) and **do NOT** add the style name or style tag.

When unsure: if a different style could reuse the block unchanged, it's general.

## NSFW

Most NSFW content maps to a **standard semantic category** — put it there and add an **`nsfw` tag**:
- explicit anatomy / breasts → `body` + `nsfw`; a lewd pose → `pose` + `nsfw`; revealing lingerie →
  `outfit` + `nsfw`.

Use a dedicated NSFW **category** only for content that doesn't fit a standard one:
- `nsfw-act` — sexual acts. `nsfw-fluids` — fluids. `nsfw` — general / uncategorisable explicit.

Every NSFW block — whatever its category — carries the **`nsfw` tag**.

## Tags = top-level characteristics

`tags` are the block's searchable, high-level descriptors — the axes someone would filter by. Cover the
relevant ones, keep them broad (not a copy of every tag in `text`):

- **character** name, **source / series** (genshin impact, arknights),
- **setting**, **color palette**, **clothing elements**,
- **states** (wet, blushing), **poses / actions**, **style / medium**, and `nsfw` when applicable.

Keep it to a handful of meaningful tags per block — enough to find and group it, not an exhaustive dump.

## Checklist

- [ ] One focused concept per block; big scenes split into several blocks.
- [ ] `text` follows nai-prompt-writing (visual tags, weights, no duplication).
- [ ] Category = the block's primary role (prefixed slug); `custom` only when nothing fits; explicit request wins.
- [ ] Style-specific blocks carry the style name + tag; general/reusable blocks have neutral names and no style tag.
- [ ] NSFW → the semantic category (`body`/`pose`/`outfit`/…) or `nsfw`/`nsfw-act`/`nsfw-fluids`; always an `nsfw` tag.
- [ ] `tags` are broad top-level descriptors (character/source/setting/palette/outfit/state/pose/style).
- [ ] `polarity` correct; no `id` field.
