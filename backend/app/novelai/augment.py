"""Quality tags + undesired-content (UC) presets — applied HERE, client-side, exactly as NovelAI's web client
does. The image API never adds them itself: it drops ``qualityToggle`` / ``ucPreset`` (they never reach the
PNG metadata it echoes back), so the preset text must already be in the prompt we send. ``build_body`` sends
the augmented text and the tokenize endpoint counts the same text, so the usage indicator matches what is
sent. Strings and rules are verbatim from the web client bundle (2026-10); its counts were calibrated
against the web UI token-for-token.
"""
import re
from typing import NamedTuple

from app.novelai.catalog import get_catalog

_SEP = ", "

# Our ucPreset values: None=3, Heavy=4, Light=5, Human Focus=6, Furry Focus=7.
_NONE = 3

# ---- verbatim web-client strings ---------------------------------------------------------------
_QUALITY = "very aesthetic, masterpiece, no text"  # the web's "standard" quality preset

_HEAVY = ("lowres, artistic error, film grain, scan artifacts, worst quality, bad quality, jpeg artifacts, "
          "very displeasing, chromatic aberration, dithering, halftone, screentone, multiple views, logo, "
          "too many watermarks, negative space, blank page")
_HUMAN = _HEAVY + ", @_@, mismatched pupils, glowing eyes, bad anatomy"
_FURRY = ("{worst quality}, distracting watermark, unfinished, bad quality, {widescreen}, upscale, {sequence}, "
          "{{grandfathered content}}, blurred foreground, chromatic aberration, sketch, everyone, "
          "[sketch background], simple, [flat colors], ych (character), outline, multiple scenes, "
          "[[horror (theme)]], comic")


class _ModelPresets(NamedTuple):
    quality: str          # appended to the positive when the quality toggle is on
    uc: dict[int, str]    # ucPreset → text prepended to the negative (None has no text)
    nsfw: bool            # Full models also prepend "nsfw" to a preset UC unless the prompt asks for nsfw


_PRESETS: dict[str, _ModelPresets] = {
    "nai-diffusion-4-5-full": _ModelPresets(_QUALITY, {
        4: _HEAVY,
        5: "lowres, artistic error, scan artifacts, worst quality, bad quality, jpeg artifacts, multiple views, "
           "very displeasing, too many watermarks, negative space, blank page",
        6: _HUMAN,
        7: _FURRY,
    }, nsfw=True),
    "nai-diffusion-4-5-curated": _ModelPresets(_QUALITY + ", -0.8::feet::, rating:general", {
        4: "blurry, lowres, upscaled, artistic error, film grain, scan artifacts, worst quality, bad quality, "
           "jpeg artifacts, very displeasing, chromatic aberration, halftone, multiple views, logo, "
           "too many watermarks, negative space, blank page",
        5: "blurry, lowres, upscaled, artistic error, scan artifacts, jpeg artifacts, logo, too many watermarks, "
           "negative space, blank page",
        6: "blurry, lowres, upscaled, artistic error, film grain, scan artifacts, bad anatomy, bad hands, "
           "worst quality, bad quality, jpeg artifacts, very displeasing, chromatic aberration, halftone, "
           "multiple views, logo, too many watermarks, @_@, mismatched pupils, glowing eyes, negative space, "
           "blank page",
    }, nsfw=False),
}

# The web's numeric preset ids, sent as ``tag_hint_*`` and echoed into the PNG metadata (NovelAI's image
# importer reads them to restore the toggles and strip the preset text back off the prompt).
_QUALITY_HINT = {True: 1, False: 0}                # standard / none
_UC_HINT = {3: 0, 4: 2, 5: 3, 6: 4, 7: 5}          # none / heavy / light / humanFocus / furryFocus

# An in-image "Text:" block: everything after it is drawn as text, so quality tags go in front of it.
_TEXT_BLOCK = re.compile(r"(?:^|\s|[,.:\[\]{}、。])text:(?!:)", re.IGNORECASE)


class Augmented(NamedTuple):
    positive: str
    negative: str
    quality_hint: int   # tag_hint_qt
    uc_hint: int        # tag_hint_uc_preset


def _offered_uc_preset(model: str, uc_preset: int) -> int:
    """Clamp the ucPreset to one the model offers (V4.5 Curated has no Furry Focus — sending it is
    undefined). Falls back to None or the first offered preset."""
    spec = next((m for m in get_catalog().models if m.id == model), None)
    if spec and uc_preset not in spec.uc_presets:
        return _NONE if _NONE in spec.uc_presets else spec.uc_presets[0]
    return uc_preset


def _with_quality(prompt: str, tags: str) -> str:
    m = _TEXT_BLOCK.search(prompt)
    head, tail = (prompt[:m.start()], prompt[m.start():]) if m else (prompt, "")
    return _SEP.join(p for p in (head.rstrip(" ,"), tags, tail.lstrip(" ,")) if p)


def augment(model: str, positive: str, negative: str, *, quality: bool, uc_preset: int) -> Augmented:
    """The prompt pair exactly as NovelAI's web client would send it. A blank positive stays blank (nothing
    to decorate — generation requires a prompt anyway); an unknown model is sent as typed, since its preset
    strings aren't known."""
    presets = _PRESETS.get(model)
    if presets is None:
        return Augmented(positive, negative, 0, 0)
    uc = _offered_uc_preset(model, uc_preset)
    quality = quality and bool(positive.strip())
    if quality:
        positive = _with_quality(positive, presets.quality)
    prefix = presets.uc.get(uc, "")
    negative = _SEP.join(p for p in (prefix, negative.strip()) if p)
    if presets.nsfw and prefix and "nsfw" not in positive.lower():
        negative = f"nsfw{_SEP}{negative}"
    return Augmented(positive, negative, _QUALITY_HINT[quality], _UC_HINT.get(uc, 0))
