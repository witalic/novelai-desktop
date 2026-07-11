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

Use the **standard categories** by default; pick the one that names the block's primary role:

- `style` — art style / rendering / artist (e.g. `nixeu style, painterly, semi-realistic`).
- `character` — a specific character or person and their defining traits.
- `pose` — body pose / posture / gesture (`kneeling, arms crossed`).
- `outfit` — clothing, lingerie, accessories.
- `environment` — setting / location / background (`classroom, forest, bedroom`).
- `lighting` — light setup (`rim lighting, backlight, soft shadows`).
- `camera` — shot / framing / angle / lens (`from above, close-up, depth of field, bokeh`).
- `negative` — undesired-content blocks (always `polarity: "negative"`).

Rules:
1. **Misfit → `custom`.** If a block is a coherent unit but none of the standard categories fit, put it
   in `custom` (still a *logical* block, not a dumping ground — keep it focused).
2. **Explicit request wins.** If the user asks for a specific category (even a new one), use that slug
   verbatim — new categories are created automatically on import (`slugify(name)`).

## NSFW

NSFW blocks go in the dedicated **`nsfw` category** (not the standard semantic one), so they can be
filtered/isolated as a group. Preserve the semantic type as a tag instead:
- `category`: `"nsfw"`.
- `tags`: include **`nsfw`** AND the **semantic type** the block would otherwise be (`pose`, `outfit`,
  `character`, …) so it's still findable by kind — plus the usual descriptors.

Example: an explicit pose block → `category: "nsfw"`, `tags: ["nsfw", "pose", …]`.

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
- [ ] Category = the block's primary role; `custom` only when nothing standard fits; honor an explicit request.
- [ ] NSFW blocks use `category: "nsfw"` and carry `nsfw` + the semantic type in `tags`.
- [ ] `tags` are broad top-level descriptors (character/source/setting/palette/outfit/state/pose/style).
- [ ] `polarity` correct; no `id` field.
