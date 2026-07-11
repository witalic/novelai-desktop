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
  Current model `nai-diffusion-4-5-full`. **v4/v4.5 differ from v3** — v4 has per-character prompts
  (`characterPrompts`, `use_coords`, `v4_prompt` / `v4_negative_prompt`); don't assume v3 fields carry over.
- **Own thin client, not a heavy dep.** Use `aedial/novelai-api` (`ImagePreset`) and `LlmKira/novelai-python`
  as human documentation for undocumented fields — do not take them as runtime dependencies.
- **Handle every response:** 200 → unzip → PNG (+ read embedded metadata via Pillow); 400 bad params;
  401 auth; **402 out of Anlas**; 429 rate-limited (back off). Never swallow a non-200 as success.
- **img2img / infill** send the base image as base64 in `parameters` (`image`, plus `mask` for infill),
  with `strength` / `noise`.
- **Mock mode:** the client exposes the same interface without a real call (no Anlas spend) so the UI can be
  built and tested offline. Tests never hit the live API.
