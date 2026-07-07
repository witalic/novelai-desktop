"""Request model for NovelAI image generation. Kept flat and typed; the client builds the full
(v4/v4.5) request body from it. Defaults match a no-Anlas Opus generation (832x1216, 28 steps)."""
from pydantic import BaseModel, Field


class GenerateParams(BaseModel):
    prompt: str = Field(min_length=1)
    negative_prompt: str = ""
    model: str = "nai-diffusion-4-5-full"
    width: int = Field(default=832, ge=64, le=2048)
    height: int = Field(default=1216, ge=64, le=2048)
    steps: int = Field(default=28, ge=1, le=50)
    scale: float = Field(default=5.0, ge=0, le=30)
    sampler: str = "k_euler_ancestral"
    seed: int | None = Field(default=None, ge=0, le=4_294_967_295)
    n_samples: int = Field(default=1, ge=1, le=4)
    noise_schedule: str = "karras"
    cfg_rescale: float = Field(default=0.0, ge=0, le=1)
    quality_toggle: bool = True
    uc_preset: int = Field(default=4, ge=0, le=7)  # v4.5: Heavy=4, Light=5, None=3, Human=6, Furry=7
