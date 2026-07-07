"""Thin async client for the (unofficial) NovelAI image API. See ``rules/novelai-api.md``.

We build the request body ourselves rather than depend on a third-party SDK; community SDKs
(``aedial/novelai-api``, ``LlmKira/novelai-python``) are consulted only as field documentation.
"""
import json
import logging
import secrets
from collections.abc import AsyncIterator

import httpx

from app.novelai._png import unzip_pngs
from app.novelai.errors import map_response_error
from app.novelai.models import GenerateParams

log = logging.getLogger(__name__)

_ENDPOINT = "/ai/generate-image"
_STREAM_ENDPOINT = "/ai/generate-image-stream"


def build_body(params: GenerateParams) -> dict:
    """Assemble the NovelAI generate-image body (v4/v4.5 shape) from flat params."""
    seed = params.seed if params.seed is not None else secrets.randbelow(2**32)
    parameters: dict = {
        "params_version": 3,
        "width": params.width,
        "height": params.height,
        "scale": params.scale,
        "sampler": params.sampler,
        "steps": params.steps,
        "n_samples": params.n_samples,
        "ucPreset": params.uc_preset,
        "qualityToggle": params.quality_toggle,
        "dynamic_thresholding": False,
        "cfg_rescale": params.cfg_rescale,
        "noise_schedule": params.noise_schedule,
        "seed": seed,
        "negative_prompt": params.negative_prompt,
        "legacy": False,
        "add_original_image": True,
        "controlnet_strength": 1,
        "legacy_v3_extend": False,
        "prefer_brownian": True,
        # v4/v4.5 structured prompts:
        "v4_prompt": {
            "caption": {"base_caption": params.prompt, "char_captions": []},
            "use_coords": False,
            "use_order": True,
        },
        "v4_negative_prompt": {
            "caption": {"base_caption": params.negative_prompt, "char_captions": []},
        },
        "use_coords": False,
        "characterPrompts": [],
    }
    return {"input": params.prompt, "model": params.model, "action": "generate", "parameters": parameters}


class NovelAIClient:
    is_mock = False

    def __init__(
        self,
        token: str,
        *,
        base_url: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._base_url = base_url
        self._timeout = timeout_s
        self._transport = transport  # injected in tests (httpx.MockTransport)

    async def generate(self, params: GenerateParams) -> list[bytes]:
        body = build_body(params)
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, transport=self._transport
        ) as http:
            resp = await http.post(_ENDPOINT, json=body, headers=headers)
        if resp.status_code != 200:
            raise map_response_error(resp.status_code, resp.text)
        images = unzip_pngs(resp.content)
        log.info(
            "NovelAI generated %d image(s) [%dx%d, %d steps, model=%s]",
            len(images), params.width, params.height, params.steps, params.model,
        )
        return images

    async def generate_stream(self, params: GenerateParams) -> AsyncIterator[dict]:
        """Stream a generation: yields intermediate JPEG previews per diffusion step, then the final PNG.

        Event dicts: ``{type: 'intermediate', step, mime, image}`` / ``{type: 'final', mime, image}``
        where ``image`` is raw base64. The upstream SSE format is undocumented (see rules/novelai-api.md).
        """
        body = build_body(params)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        async with httpx.AsyncClient(
            base_url=self._base_url, timeout=self._timeout, transport=self._transport
        ) as http:
            async with http.stream("POST", _STREAM_ENDPOINT, json=body, headers=headers) as resp:
                if resp.status_code != 200:
                    raise map_response_error(resp.status_code, (await resp.aread()).decode("utf-8", "replace"))
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    kind = data.get("event_type")
                    if kind == "intermediate":
                        yield {"type": "intermediate", "samp": data.get("samp_ix", 0), "step": data.get("step_ix", 0), "mime": "image/jpeg", "image": data.get("image", "")}
                    elif kind == "final":
                        yield {"type": "final", "mime": "image/png", "image": data.get("image", "")}
        log.info("NovelAI stream complete [%dx%d, %d steps]", params.width, params.height, params.steps)
