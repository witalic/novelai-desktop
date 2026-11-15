"""Anlas cost of an image generation — NovelAI's own rule as its web client computes it (``novelai.net/image``, read
2026-11-15) and the server's copy of ``frontend/src/presets/cost.ts`` (keep the two in step), for deciding what an
agent may spend before anything is sent. NovelAI can re-tune it: the balance stays the final word.

A V5 image costs 1.5× the area-and-steps base, and NovelAI refuses an image over 140 Anlas. Opus (tier 3, active) gets
one sample free at ≤ 1024×1024 and ≤ 28 steps — on V5 only while its usage limit isn't used up.
"""
import math

_A = 2951823174884865e-21  # linear-in-area term
_B = 5.753298233447344e-7  # area × steps term
_V5_FACTOR = 1.5
MAX_PER_IMAGE = 140
FREE_AREA = 1024 * 1024
FREE_STEPS = 28


def is_v5(model: str) -> bool:
    return model.startswith("nai-diffusion-5")


def per_image(model: str, width: int, height: int, steps: int) -> int:
    area = width * height
    base = math.ceil(_A * area + _B * area * steps)
    return max(math.ceil(base * _V5_FACTOR) if is_v5(model) else base, 2)  # ≥ 2 Anlas per image


def anlas_cost(model: str, width: int, height: int, steps: int, n_samples: int, *, opus: bool,
               v5_limit_reached: bool = False) -> int | None:
    """What a generation costs, or None when NovelAI won't run it (an image over ``MAX_PER_IMAGE``). ``opus``: an
    active Opus subscription; ``v5_limit_reached``: its V5 usage limit is used up, so a V5 sample isn't free."""
    each = per_image(model, width, height, steps)
    if each > MAX_PER_IMAGE:
        return None
    free = (opus and not (v5_limit_reached and is_v5(model))
            and width * height <= FREE_AREA and steps <= FREE_STEPS)
    return each * (max(1, n_samples) - (1 if free else 0))
