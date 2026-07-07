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
    body = build_body(GenerateParams(prompt="1girl", negative_prompt="bad"))
    assert body["model"] == "nai-diffusion-4-5-full"
    assert body["action"] == "generate"
    assert body["parameters"]["v4_prompt"]["caption"]["base_caption"] == "1girl"
    assert body["parameters"]["v4_negative_prompt"]["caption"]["base_caption"] == "bad"
    assert isinstance(body["parameters"]["seed"], int)  # random seed filled in


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
