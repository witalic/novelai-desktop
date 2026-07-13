"""Static NovelAI model catalog — the single source of truth for model / sampler / resolution / step
knowledge, served read-only at ``/api/catalog``.

NovelAI has no models-list endpoint (``rules/novelai-api.md``), so this is hand-maintained. It is pure
static data — no NovelAI call, no keychain — consumed by the param UI, the cost estimate, and (Phase C)
the tokenizer. Per-model ``tokenizer``/``token_limit`` are authored here for the first time: v4/v4.5 use
**T5**, v3 uses CLIP. Scope is v4.5 for now — v3 is intentionally omitted (``build_body`` only emits the
v4 structured shape), so it is not offered until its request branch lands.
"""
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel


class Range(BaseModel):
    min: float
    max: float
    step: float = 1
    default: float


class Resolution(BaseModel):
    group: Literal["Portrait", "Landscape", "Square"]
    tier: str
    width: int
    height: int


class Sampler(BaseModel):
    id: str
    label: str


class Labeled(BaseModel):
    value: str
    label: str


class UcPreset(BaseModel):
    value: int
    label: str


class DimLimits(BaseModel):
    min: int = 64
    max: int = 2048
    step: int = 64


class ModelSpec(BaseModel):
    id: str
    label: str
    family: Literal["v3", "v4"]
    tokenizer: Literal["t5", "clip"]
    token_limit: int = 512  # positive budget (base caption + character captions share it)
    negative_token_limit: int = 512  # the negative prompt is counted separately
    samplers: list[str]  # sampler ids offered for this model (SMEA variants only exist on v3)
    uc_presets: list[int]  # ucPreset values offered for this model (Furry Focus is Full-only, not Curated)
    steps: Range
    scale: Range


class Catalog(BaseModel):
    models: list[ModelSpec]
    samplers: list[Sampler]  # master label table (a model's `samplers` references these ids)
    resolutions: list[Resolution]
    uc_presets: list[UcPreset]
    noise_schedules: list[Labeled]
    dim_limits: DimLimits
    default_model: str


# ---- the data ----------------------------------------------------------------------------------

_SAMPLERS = [
    Sampler(id="k_euler_ancestral", label="Euler Ancestral"),
    Sampler(id="k_euler", label="Euler"),
    Sampler(id="k_dpmpp_2s_ancestral", label="DPM++ 2S Ancestral"),
    Sampler(id="k_dpmpp_2m_sde", label="DPM++ 2M SDE"),
    Sampler(id="k_dpmpp_2m", label="DPM++ 2M"),
    Sampler(id="k_dpmpp_sde", label="DPM++ SDE"),
]
_V45_SAMPLERS = [s.id for s in _SAMPLERS]  # v4.5 offers the full set (no SMEA)

_RESOLUTIONS = [
    Resolution(group="Portrait", tier="Small", width=512, height=768),
    Resolution(group="Portrait", tier="Normal", width=832, height=1216),
    Resolution(group="Portrait", tier="Large", width=1024, height=1536),
    Resolution(group="Portrait", tier="Wallpaper", width=1088, height=1920),
    Resolution(group="Landscape", tier="Small", width=768, height=512),
    Resolution(group="Landscape", tier="Normal", width=1216, height=832),
    Resolution(group="Landscape", tier="Large", width=1536, height=1024),
    Resolution(group="Landscape", tier="Wallpaper", width=1920, height=1088),
    Resolution(group="Square", tier="Small", width=640, height=640),
    Resolution(group="Square", tier="Normal", width=1024, height=1024),
    Resolution(group="Square", tier="Large", width=1472, height=1472),
]

_UC_PRESETS = [
    UcPreset(value=4, label="Heavy"),
    UcPreset(value=5, label="Light"),
    UcPreset(value=7, label="Furry Focus"),
    UcPreset(value=6, label="Human Focus"),
    UcPreset(value=3, label="None"),
]
_UC_FULL = [4, 5, 7, 6, 3]      # Full offers Furry Focus
_UC_CURATED = [4, 5, 6, 3]      # Curated has no Furry Focus (sending ucPreset 7 to it is undefined)

_NOISE = [
    Labeled(value="karras", label="karras (recommended)"),
    Labeled(value="exponential", label="exponential"),
    Labeled(value="polyexponential", label="polyexponential"),
]

_STEPS = Range(min=1, max=50, step=1, default=28)
_SCALE = Range(min=0, max=10, step=0.5, default=5)

_MODELS = [
    ModelSpec(id="nai-diffusion-4-5-full", label="NAI Diffusion 4.5 — Full",
              family="v4", tokenizer="t5", samplers=_V45_SAMPLERS, uc_presets=_UC_FULL, steps=_STEPS, scale=_SCALE),
    ModelSpec(id="nai-diffusion-4-5-curated", label="NAI Diffusion 4.5 — Curated",
              family="v4", tokenizer="t5", samplers=_V45_SAMPLERS, uc_presets=_UC_CURATED, steps=_STEPS, scale=_SCALE),
]

_DEFAULT_MODEL = "nai-diffusion-4-5-full"


@lru_cache
def get_catalog() -> Catalog:
    """The catalog singleton (immutable static data — never mutate the returned model)."""
    return Catalog(
        models=_MODELS,
        samplers=_SAMPLERS,
        resolutions=_RESOLUTIONS,
        uc_presets=_UC_PRESETS,
        noise_schedules=_NOISE,
        dim_limits=DimLimits(),
        default_model=_DEFAULT_MODEL,
    )
