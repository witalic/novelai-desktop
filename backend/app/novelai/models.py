"""Request model for NovelAI image generation. Kept flat and typed; the client builds the full
(v4-shaped, v4.5/v5) request body from it. Defaults match a no-Anlas Opus generation (832x1216, 28 steps)."""
from pydantic import BaseModel, Field

from app.novelai.catalog import get_catalog


class GenerateParams(BaseModel):
    prompt: str = Field(min_length=1)
    negative_prompt: str = ""
    model: str = "nai-diffusion-5-full"
    width: int = Field(default=832, ge=64, le=2048)
    height: int = Field(default=1216, ge=64, le=2048)
    steps: int = Field(default=28, ge=1, le=50)
    scale: float = Field(default=7.0, ge=0, le=10)  # the default model's catalog scale (the single source of truth)
    sampler: str = "k_euler_ancestral"
    seed: int | None = Field(default=None, ge=0, le=4_294_967_295)
    n_samples: int = Field(default=1, ge=1, le=4)
    noise_schedule: str = "karras"
    cfg_rescale: float = Field(default=0.0, ge=0, le=1)
    quality_toggle: bool = True
    uc_preset: int = Field(default=4, ge=0, le=7)  # Heavy=4, Light=5, None=3, Human=6, Furry=7

    def for_model(self) -> "GenerateParams":
        """These params as the model takes them: what it fixes (V5 Full Medium — 14 steps, Euler Ancestral, the Heavy
        UC preset, no CFG rescale) set as NovelAI's own client sends it, so a recipe records what was generated."""
        spec = next((m for m in get_catalog().models if m.id == self.model), None)
        if spec is None:
            return self
        fixed: dict = {}
        if spec.steps.min == spec.steps.max:
            fixed["steps"] = int(spec.steps.min)
        if len(spec.samplers) == 1:
            fixed["sampler"] = spec.samplers[0]
        if len(spec.uc_presets) == 1:
            fixed["uc_preset"] = spec.uc_presets[0]
        if not spec.cfg_rescale:
            fixed["cfg_rescale"] = 0.0
        return self.model_copy(update=fixed) if fixed else self
