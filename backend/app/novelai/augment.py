"""What NovelAI prepends to a prompt, for TOKEN COUNTING only (the API applies these server-side at
generation — we never send them). Used by the tokenize endpoint so the usage indicator matches the web
UI. UC-preset negatives are the verbatim v4.5 strings from the NovelAI web client (via
aedial/novelai-api), verified token-for-token against the web UI. The qualityToggle string isn't
published (the API adds it), but it is a fixed 8 T5 tokens for v4.5 (calibrated against the web UI).
"""
_DEFAULT_MODEL = "nai-diffusion-4-5-full"  # fallback for an unknown model id

# ucPreset value → undesired-content negative prepended to the user's negative. Values: None=3,
# Heavy=4, Light=5, Human Focus=6, Furry Focus=7 (curated has no Furry Focus).
_UC_FULL = {
    3: "",
    4: ("nsfw, lowres, artistic error, film grain, scan artifacts, worst quality, bad quality, "
        "jpeg artifacts, very displeasing, chromatic aberration, dithering, halftone, screentone, "
        "multiple views, logo, too many watermarks, negative space, blank page"),
    5: ("nsfw, lowres, artistic error, scan artifacts, worst quality, bad quality, jpeg artifacts, "
        "multiple views, very displeasing, too many watermarks, negative space, blank page"),
    6: ("nsfw, lowres, artistic error, film grain, scan artifacts, worst quality, bad quality, "
        "jpeg artifacts, very displeasing, chromatic aberration, dithering, halftone, screentone, "
        "multiple views, logo, too many watermarks, negative space, blank page, @_@, mismatched pupils, "
        "glowing eyes, bad anatomy"),
    7: ("nsfw, {worst quality}, distracting watermark, unfinished, bad quality, {widescreen}, upscale, "
        "{sequence}, {{grandfathered content}}, blurred foreground, chromatic aberration, sketch, everyone, "
        "[sketch background], simple, [flat colors], ych (character), outline, multiple scenes, "
        "[[horror (theme)]], comic"),
}
_UC_CURATED = {
    3: "",
    4: ("blurry, lowres, upscaled, artistic error, film grain, scan artifacts, worst quality, bad quality, "
        "jpeg artifacts, very displeasing, chromatic aberration, halftone, multiple views, logo, "
        "too many watermarks, negative space, blank page"),
    5: ("blurry, lowres, upscaled, artistic error, scan artifacts, jpeg artifacts, logo, too many watermarks, "
        "negative space, blank page"),
    6: ("blurry, lowres, upscaled, artistic error, film grain, scan artifacts, bad anatomy, bad hands, "
        "worst quality, bad quality, jpeg artifacts, very displeasing, chromatic aberration, halftone, "
        "multiple views, logo, too many watermarks, @_@, mismatched pupils, glowing eyes, negative space, "
        "blank page"),
    7: "",  # curated has no Furry Focus preset
}
_UC_NEGATIVES = {
    "nai-diffusion-4-5-full": _UC_FULL,
    "nai-diffusion-4-5-curated": _UC_CURATED,
}
_QUALITY_TOKENS = {
    "nai-diffusion-4-5-full": 8,
    "nai-diffusion-4-5-curated": 8,
}


def uc_negative(model: str, uc_preset: int) -> str:
    """The undesired-content string NovelAI prepends to the negative for this model + ucPreset."""
    return _UC_NEGATIVES.get(model, _UC_NEGATIVES[_DEFAULT_MODEL]).get(uc_preset, "")


def quality_tokens(model: str) -> int:
    """Tokens the qualityToggle prepend adds to the positive count for this model."""
    return _QUALITY_TOKENS.get(model, _QUALITY_TOKENS[_DEFAULT_MODEL])
