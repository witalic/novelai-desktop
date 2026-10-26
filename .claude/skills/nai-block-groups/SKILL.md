---
name: nai-block-groups
description: Save a tested NovelAI recipe as a group of Library blocks that work together — a style kit, a character kit, a scene — plus the combination that assembles it. Use when turning a recipe that already gives good results into a reusable base. Pairs with nai-block-authoring (each member block) and nai-prompt-writing (the tag text).
---

# Authoring block groups

A **group** is a set of blocks made to be used together, saved from a recipe that already works. A single
block (**nai-block-authoring**) has to land among any blocks it is mixed with; a group's members always sit
side by side, so what matters is how they balance **each other**. That changes the weighting rule — see
below. Everything else about each member (tags by default, category, naming, tags) follows
nai-block-authoring.

| group | members |
|---|---|
| Style kit | the style block (`<Style> — full`, optionally `— short`) · its negative (`<Style> — no …`) · the general companions the look relies on (a camera block, an anatomy guard) |
| Character kit | the character tag block · features · outfit(s) · character-specific negatives |
| Scene | environment · palette · light for one setting |

## Start from a tested recipe

- Save a group only from a prompt that has already given good results — never design one on paper.
- Split the prompt by **role** into blocks **without rewording it**, and keep the tag order (earlier tags
  weigh more). The assembled group must reproduce the tested prompt.
- A style is ONE block and holds no subject or character traits (nai-block-authoring). Anything specific
  to one character or setting stays out of the style kit.
- If the recipe holds natural-language phrases, convert them to tags first and re-test: tags are the
  default block content and give steadier results.

## Weights in a group

The single-block rule — one `1.3::` anchor per block — is for standalone blocks. In a group:

- **Keep the weights the recipe was tested with.** They were tuned together. Never add an anchor to a
  member just to satisfy the single-block rule: anchors from every member pile up and dilute the 1–3
  strengthened elements the whole prompt should have (nai-prompt-writing).
- **Weight across the group, not per block:** strengthen the 1–3 elements the tests showed the look
  depends on (e.g. `soft shading` for the skin, `cowboy shot` or `from below` for the framing). A member
  may carry no weight at all — negatives often need none.
- **Changing a weight changes the group:** re-test before saving it.

## Save

1. **Library blocks** for the members: style-specific ones carry the group's name prefix and tag;
   general companions keep neutral names (nai-block-authoring).
2. **A combination** in the work that assembles the group in prompt order — its canonical form. For a
   style kit, save a `<Style> — style core` combination plus one example combination per character it
   was proven on.
3. **Combinations copy blocks at their current version.** After editing a member in the Library,
   re-save every combination that uses it, or it keeps the old text.

## Verify

Generate a few free drafts **from the combination itself** (not from hand-typed text) and compare them
with the recipe's results. The group is done only when they match.

## Checklist

- [ ] Saved from a tested recipe; split by role without rewording; tag order kept.
- [ ] Members follow nai-block-authoring; the style is one subject-free block; tags, not sentences.
- [ ] Weights are the tested ones, 1–3 across the whole group; no anchor added just for the rule.
- [ ] Combination saved in prompt order (core + examples for a style kit); re-saved after any member edit.
- [ ] Free drafts from the combination match the recipe.
