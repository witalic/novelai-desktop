"""Anlas cost estimate of an image generation — the server's copy of ``frontend/src/presets/cost.ts`` (keep the
two in step), for deciding what an agent may spend before anything is sent.

Community-reverse-engineered formula (novelai-api / novelai-python): an ESTIMATE — NovelAI can re-tune the
constants. Opus (tier 3, active) gets the first sample free at ≤ 1024×1024 and ≤ 28 steps.
"""
import math

_A = 2951823174884865e-21  # linear-in-area term
_B = 5.753298233447344e-7  # area × steps term
FREE_AREA = 1024 * 1024
FREE_STEPS = 28


def anlas_cost(width: int, height: int, steps: int, n_samples: int, *, opus: bool) -> int:
    r = max(width * height, 65536)  # floor at 256×256
    per_sample = max(math.ceil(_A * r + _B * r * steps), 2)  # ≥ 2 Anlas per sample
    free = opus and steps <= FREE_STEPS and r <= FREE_AREA
    return per_sample * (max(1, n_samples) - (1 if free else 0))
