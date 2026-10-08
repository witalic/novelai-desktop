---
name: nai-prompt-writing
description: Write or refine a NovelAI image prompt — V5 (the app's default; natural language + Danbooru tags) or legacy V4.5 (tags). Use whenever producing the `text` of a prompt or a library block — turns intent into concrete, drawable content with correct syntax, order, weighting, and token budget.
---

# Writing NovelAI prompts

Two model generations are live in this app — know which one the prompt targets:

| | **V5** (default) | **V4.5** (legacy) |
|---|---|---|
| Understands | natural language **and** Danbooru tags | Danbooru tags (natural language only partly) |
| Languages | English, Japanese (others work less reliably) | English only — no Japanese, no emoji |
| Token budget | Full 1471 · Curated 703 (Qwen) — `1.3::` markers count too | 512 (T5) — weight markers don't count |
| In-image text | long text, styled and placed in words | short text |

The canvas shows the live token count against the model's budget — stay under it.

## Core rule: describe what is DRAWN, not what is MEANT

Every tag — and every sentence — must name something a viewer could **draw without knowing the context**.

| Narrative (never use) | Visual (use) |
|---|---|
| `lost in thought` | `vacant expression, unfocused eyes` |
| `feeling of dread` | `wide eyes, sweat, trembling, pale skin` |
| `forbidden pleasure` | `closed eyes, arched back, flushed cheeks` |
| `best payment ever` | `money, cash, bills scattered` |
| `she is drowning in sorrow` | `a girl crying in the rain, head lowered` |

**Validation:** "Can this be drawn without knowing the story?" No → rewrite it as visual content.

## Tags vs natural language (V5)

NovelAI's V5 guidance: describe the image you have in mind — V5 follows natural language far better than
V4.5 — while tags stay fully supported and give brevity and consistency. Prompts in this app are assembled
from library blocks joined with `, `, so play to both:

- **Tags for reusable attributes** — character, hair, outfit, style, lighting, camera. Short, consistent,
  and they compose cleanly with other blocks.
- **Natural language for what tags can't say** — relations and layout (`the girl on the left hands an
  umbrella to the boy`), telling several characters apart, panel layouts, how and where text appears.
- A sentence must stand on its own (commas join it to other blocks) and avoid inner commas — use "and" /
  "with": with *Unique tags* on, every comma-separated piece is treated as a tag.
- **V4.5:** stay tag-first; keep sentences out.

## Syntax

- Tags are separated by **comma + space**: `standing, arms crossed, looking at viewer`. Case is ignored.
- **Order affects weight** — earlier content has more impact; put the subject and its key traits first.
- `|` is reserved (prompt mixing) — never use it inside a tag (NovelAI renamed `v` to `peace sign` for this).

Recommended tag order:
```
[character/count] -> [series/source] -> [outfit] -> [pose] -> [action] -> [expression] -> [environment] -> [style] -> [technical]
```
Example: `1girl, solo, hu tao (genshin impact), school uniform, standing, arms crossed, smiling, classroom, anime screencap`

## Strengthening & weakening

- **Numeric weight — prefer it:** `1.3::tag::`. Values below 1 weaken; a negative value pushes a concept
  away (`-0.8::feet::` — NovelAI's own presets do this). NovelAI's V5 example: `2.1::transparent background::`.
- `{tag}` / `[tag]` (×/÷1.05 per brace) still work — NovelAI's own V5 presets use them — but are coarse;
  prefer numeric weights in new prompts.
- Strengthen only **1–3 scene-defining elements**. Over-weighting lowers quality, and on V5 every marker
  also spends tokens.

## Quality tags & undesired content — the app adds them

- **Add quality tags** (toggle) appends the model's preset at generation — V5 and V4.5 Full:
  `very aesthetic, masterpiece, no text`; V4.5 Curated also `-0.8::feet::, rating:general`.
- The **Undesired content** preset (Heavy / Light / Human Focus / Furry Focus) is prepended to the
  negative; on Full models the app also adds `nsfw` there unless the prompt itself contains `nsfw`.
- So never put quality tags (`masterpiece`, `very aesthetic`, `best quality`, …) or the stock junk list
  (`worst quality, bad quality, jpeg artifacts, …`) into prompts or blocks — add only what a preset lacks.
- The quality preset includes `no text`: if an image must show text and it won't appear, turn the toggle off.

## In-image text

- `Text: <words>` — everything after `Text:` is drawn as text, so it goes **last** (in this app: the last
  positive block). The app keeps the quality tags in front of it.
- V5 renders much longer text; style and place it in words:
  `a handwritten speech bubble with green text on white next to the purple-haired girl's head, Text: Hello, world!`
- NovelAI's web client turns quoted strings ("…") into a `Text:` block automatically — this app doesn't
  yet, so write `Text:` yourself.

## V5 tags worth knowing

| tag | effect |
|---|---|
| `high complexity` | normal, polished images — the usual choice |
| `low complexity` · `ultra complexity` | more stylized looks (simpler · denser); `medium complexity` sits between |
| `depthness` | deeper shading |
| `attractive male` | what it says |
| `transparent background` | transparent (alpha) background; if weak, `2.1::transparent background::` |
| `alpha transparency` | see-through elements — magic effects, fire, umbrellas |
| `has alpha` | abstract: use the alpha channel somehow |
| `meta:novel era` · `meta:golden era` | subtle era bias — less modern · slightly more modern |
| `visual novel art` · `visual novel bg` · `visual novel cg` · `visual novel chibi` · `visual novel sprite` | visual-novel styles |

## Several characters & comics

- Per-character prompts and positions aren't wired in this app yet — put everyone in the prompt: count tags
  (`2girls, 1boy`), then (V5) one sentence per character tied to a place (`on the left`, `in the background`)
  so traits don't bleed between them.
- Comics (V5): describe the page in words — `a four-panel comic page, panel 1: …` — and add `Text:` last.

## Avoid

1. **Narrative instead of visual** (most frequent): `erotic tension` → `parted lips, heavy-lidded eyes, flushed cheeks, leaning forward`.
2. **Over-strengthening:** `{{{{{{red hair}}}}}}` → `1.3::red hair::`.
3. **Conflicting content:** `standing, kneeling, sitting` → keep one pose.
4. **Duplication:** `wet, soaking wet, drenched, wet body` → `1.2::wet::, glistening skin`.
5. **Abstract concepts:** `forbidden, darkness within` → `shadow, dark background, conflicted expression`.
6. **Context bloat:** precise beats exhaustive — cut secondary content, not key content. Watch the budget
   (V5 Full 1471 · V5 Curated 703 · V4.5 512 tokens).

## Tag knowledge

Prefer tags the model actually knows. A tag that exists on **Danbooru** with a healthy image count is
almost always known; NovelAI also has its own tags (the V5 table above). Unknown or obscure tags are
ignored or read unpredictably — drop them, or on V5 describe the thing in plain words instead.

## Checklist before finishing a prompt

- [ ] Written for the right model — sentences only when V5 is the target.
- [ ] Everything is visual (drawable without context), not narrative.
- [ ] No duplicates, no conflicting poses/outfits; no quality tags or stock UC the app already adds.
- [ ] 1–3 key elements strengthened, numeric `::` preferred; nothing over-strengthened.
- [ ] `Text:` (if any) written explicitly and last.
- [ ] Within the model's token budget.
