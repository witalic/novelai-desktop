import io
import json
import zipfile

import httpx
import pytest

from app.novelai import errors
from app.novelai._png import solid_png
from app.novelai.client import NovelAIClient, build_body
from app.novelai.models import GenerateParams


def _client(handler) -> NovelAIClient:
    return NovelAIClient("tok", base_url="https://x", timeout_s=5, transport=httpx.MockTransport(handler))


def test_build_body_v4_shape():
    body = build_body(GenerateParams(prompt="1girl", negative_prompt="bad", quality_toggle=False, uc_preset=3))
    p = body["parameters"]
    assert body["model"] == "nai-diffusion-4-5-full"
    assert body["action"] == "generate"
    assert body["input"] == p["v4_prompt"]["caption"]["base_caption"] == "1girl"
    assert p["uc"] == p["v4_negative_prompt"]["caption"]["base_caption"] == "bad"
    assert isinstance(p["seed"], int)  # random seed filled in
    # The API ignores these toggles (the presets travel as prompt text) — never send them as if they worked.
    assert "qualityToggle" not in p and "ucPreset" not in p


def test_presets_are_baked_into_the_sent_text():
    body = build_body(GenerateParams(prompt="1girl, solo", negative_prompt="bad hands", quality_toggle=True, uc_preset=4))
    p = body["parameters"]
    assert body["input"] == p["v4_prompt"]["caption"]["base_caption"] == "1girl, solo, very aesthetic, masterpiece, no text"
    neg = p["v4_negative_prompt"]["caption"]["base_caption"]
    assert neg.startswith("nsfw, lowres, artistic error,") and neg.endswith("blank page, bad hands")
    assert p["uc"] == neg
    assert (p["tag_hint_qt"], p["tag_hint_uc_preset"]) == (1, 2)  # standard quality, heavy UC


def test_uc_preset_clamped_to_model_offering():
    # V4.5 Curated has no Furry Focus (7) → build_body must not apply it (undefined on that model, M3).
    curated = build_body(GenerateParams(prompt="x", negative_prompt="bad", model="nai-diffusion-4-5-curated", uc_preset=7))
    assert curated["parameters"]["tag_hint_uc_preset"] == 0 and curated["parameters"]["uc"] == "bad"
    # Full offers Furry Focus → it is applied.
    full = build_body(GenerateParams(prompt="x", model="nai-diffusion-4-5-full", uc_preset=7))["parameters"]
    assert full["tag_hint_uc_preset"] == 5 and "grandfathered content" in full["uc"]
    # A valid preset on Curated is untouched.
    assert build_body(GenerateParams(prompt="x", model="nai-diffusion-4-5-curated", uc_preset=5))["parameters"]["tag_hint_uc_preset"] == 3


@pytest.mark.parametrize(
    "status,exc",
    [
        (401, errors.NovelAIAuthError),
        (402, errors.NovelAINoAnlasError),
        (429, errors.NovelAIRateLimitError),
        (400, errors.NovelAIBadRequestError),
        (500, errors.NovelAIError),
    ],
)
async def test_error_status_maps_to_exception(status, exc):
    client = _client(lambda request: httpx.Response(status, text="upstream error"))
    with pytest.raises(exc):
        await client.generate(GenerateParams(prompt="p"))


async def test_success_unzips_pngs():
    png = solid_png(64, 64)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("image_0.png", png)
    zipped = buf.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content)
        assert sent["parameters"]["steps"] == 28  # default no-Anlas step count
        assert request.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, content=zipped)

    imgs = await _client(handler).generate(GenerateParams(prompt="p"))
    assert len(imgs) == 1
    assert imgs[0][:8] == b"\x89PNG\r\n\x1a\n"
