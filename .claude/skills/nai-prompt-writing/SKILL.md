---
name: nai-prompt-writing
description: Write or refine a NovelAI image prompt as Danbooru-style tags (v4/v4.5). Use whenever producing the `text` of a prompt or a library block — turns intent into concrete, model-known visual tags with correct syntax, order, and weighting.
---

# Writing NovelAI prompts

NovelAI does **not** understand natural language. It is trained on **Danbooru-style tags**, not
sentences or figurative descriptions. A prompt is a comma-separated list of concrete visual tags.

## Core rule: tags describe what is DRAWN, not what is MEANT

Every tag must name something a viewer could **draw without knowing the context**.

| Narrative (never use) | Visual (use) |
|---|---|
| `lost in thought` | `vacant expression, unfocused eyes` |
| `feeling of dread` | `wide eyes, sweat, trembling, pale skin` |
| `forbidden pleasure` | `closed eyes, arched back, flushed cheeks` |
| `best payment ever` | `money, cash, bills scattered` |

**Validation:** "Can this be drawn without knowing the story?" No → rewrite it as visual tags.

## Syntax

- Tags are separated by **comma + space**: `standing, arms crossed, looking at viewer`.
- Case is ignored (`Blue Eyes` = `blue eyes`).
- **Order affects weight** — tags earlier in the list have more impact.

### Recommended order
```
[quality] -> [character/count] -> [series/source] -> [outfit] -> [pose] -> [action] -> [expression] -> [environment] -> [style] -> [technical]
```
Example: `best quality, amazing quality, 1girl, solo, {character}, school uniform, standing, arms crossed, smiling, classroom, anime screencap`

## Strengthening & weakening

- `{tag}` strengthen ×1.05 per brace · `[tag]` weaken ÷1.05 per brace.
- **Numerical weight (v4+, prefer this):** `1.3::tag::` — more precise than braces. `::` also closes open braces.
- Strengthen only **1–3 scene-defining elements** (`{red eyes}`, `1.3::wet skin::`). Strengthening everything lowers quality; >5 braces is unpredictable.

## Quality tags (v4.5)

- Positive prompts start with: `best quality, amazing quality, masterpiece, very aesthetic`.
- Negative prompts include: `worst quality, bad quality, displeasing, very displeasing`.
- (In this app the qualityToggle can prepend quality tags — don't duplicate them inside a block whose job isn't quality.)

## Avoid

1. **Narrative instead of visual** (most frequent): `erotic tension` → `parted lips, heavy-lidded eyes, flushed cheeks, leaning forward`.
2. **Over-strengthening:** `{{{{{{red hair}}}}}}` → `{{red hair}}` or `1.3::red hair::`.
3. **Conflicting tags:** `standing, kneeling, sitting` → keep one pose.
4. **Duplication:** `wet, soaking wet, drenched, wet body` → `{wet}, glistening skin`.
5. **Abstract concepts:** `forbidden, darkness within` → `shadow, dark background, conflicted expression`.
6. **Context bloat:** 30 precise tags beat 80 vague ones — cut secondary tags, not key ones (v4/v4.5 budget ~512 tokens).

## Tag knowledge

Prefer tags the model actually knows. A tag that exists on **Danbooru** with a healthy image count is
almost always known. Unknown/obscure tags are ignored or interpreted unpredictably — drop or replace them.

## Checklist before finishing a prompt

- [ ] Every tag is visual (drawable without context), not narrative.
- [ ] No duplicate tags; no conflicting poses/outfits.
- [ ] 1–3 key elements strengthened (numeric `::` preferred), nothing over-strengthened.
- [ ] Reasonable length — precise over exhaustive.
- [ ] Tags are real Danbooru-style tags the model likely knows.
