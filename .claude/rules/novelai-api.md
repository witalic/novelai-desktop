---
paths:
  - "backend/app/novelai/**"
---

# NovelAI image API (unofficial)

There is **no official REST spec** — `docs.novelai.net/.../scripting/api-reference` is the in-editor
*scripting SDK* (text/editor), not this. We call the same private endpoint the web client uses.

- **Endpoint:** `POST https://image.novelai.net/ai/generate-image`, `Authorization: Bearer <token>`,
  JSON body; **response is a binary ZIP** containing `image_0.png` (…`_N.png`).
- **Subscription / Anlas:** `GET https://image.novelai.net/user/subscription` (Anlas = remaining
  `trainingStepsLeft.fixedTrainingStepsLeft + purchasedTrainingSteps`; `tier` 0–3, `active`). It is on
  the **image host**, NOT `api.novelai.net` — the account host rejects it with 400 *"Please refresh
  NovelAI.net… update to the image URL"*. No `User-Agent` needed; the default httpx UA works.
- **Request shape:** `{ input, model, action, parameters:{…} }`. `action` ∈ `generate` / `img2img` / `infill`.
  Models: `nai-diffusion-5-full` (default) / `nai-diffusion-5-curated`, legacy `nai-diffusion-4-5-full` /
  `-curated`; `params_version: 4`. **v4+ differ from v3** — v4.5 and v5 share the structured shape with
  per-character prompts (`characterPrompts`, `use_coords`, `v4_prompt` / `v4_negative_prompt`); don't
  assume v3 fields carry over.
- **Quality tags + UC presets are client-side.** The API drops `qualityToggle` / `ucPreset` (they never
  reach the PNG metadata it echoes back): the web client appends the quality tags and prepends the UC text
  itself, sending `tag_hint_qt` / `tag_hint_uc_preset` only as metadata hints. We do the same in
  `novelai/augment.py`, and the tokenize endpoint counts that same text.
- **Ground truth is the web client bundle.** Model ids, per-model defaults, preset strings, token limits and
  feature flags live in the public JS at `novelai.net/image` — read them there instead of guessing (an
  unknown field or model is a 400 at best, wasted Anlas at worst).
- **Generation is not deterministic:** the same seed + params gives different pixels run to run, so a
  pixel A/B between two requests proves nothing — inspect the PNG metadata (`Comment` JSON) instead.
- **Own thin client, not a heavy dep.** Use `aedial/novelai-api` (`ImagePreset`) and `LlmKira/novelai-python`
  as human documentation for undocumented fields — do not take them as runtime dependencies.
- **Handle every response:** 200 → unzip → PNG (+ read embedded metadata via Pillow); 400 bad params;
  401 auth; **402 out of Anlas**; 429 rate-limited (back off). Never swallow a non-200 as success.
- **img2img / infill** send the base image as base64 in `parameters` (`image`, plus `mask` for infill),
  with `strength` / `noise`.
- **Mock mode:** the client exposes the same interface without a real call (no Anlas spend) so the UI can be
  built and tested offline. Tests never hit the live API.
