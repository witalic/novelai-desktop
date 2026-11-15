import io
import json
import zipfile

import httpx
import pytest

from app.novelai import client as client_module
from app.novelai import errors
from app.novelai._png import solid_png
from app.novelai.client import NovelAIClient, build_body
from app.novelai.models import GenerateParams


def _client(handler) -> NovelAIClient:
    return NovelAIClient("tok", base_url="https://x", timeout_s=5, transport=httpx.MockTransport(handler))


def test_build_body_v4_shape():
    body = build_body(GenerateParams(prompt="1girl", negative_prompt="bad", quality_toggle=False, uc_preset=3))
    p = body["parameters"]
    assert body["model"] == "nai-diffusion-5-full"
    assert body["action"] == "generate"
    assert p["params_version"] == 4
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


def test_medium_effort_is_sent_with_the_settings_novelai_fixes_for_it():
    asked = GenerateParams(prompt="1girl", model="nai-diffusion-5-full-medium", steps=28,
                           sampler="k_dpmpp_2s_ancestral", cfg_rescale=0.2, uc_preset=3)  # a Fav-like preset
    p = build_body(asked)["parameters"]
    assert (p["steps"], p["sampler"], p["cfg_rescale"], p["tag_hint_uc_preset"]) == (14, "k_euler_ancestral", 0.0, 2)
    assert p["uc"].startswith("nsfw, lowres, artistic error")  # Heavy, as for V5 Full
    assert asked.for_model().model_dump(include={"steps", "sampler", "cfg_rescale", "uc_preset"}) == {
        "steps": 14, "sampler": "k_euler_ancestral", "cfg_rescale": 0.0, "uc_preset": 4}
    full = GenerateParams(prompt="x", steps=28, sampler="k_dpmpp_2s_ancestral", cfg_rescale=0.2)
    assert full.for_model() == full  # a model that fixes nothing keeps what was asked


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


def _failing_first(times: int, ok: httpx.Response):
    """A transport whose first ``times`` connections can't be made — as NovelAI's host now and then drops a handshake."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) <= times:
            raise httpx.ConnectError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol")
        return ok
    return handler, calls


async def test_a_connection_that_could_not_be_made_is_tried_once_more(monkeypatch):
    monkeypatch.setattr(client_module, "_RETRY_PAUSE_S", 0.0)
    handler, calls = _failing_first(1, httpx.Response(200, json={"tier": 3, "active": True}))
    assert (await _client(handler).get_subscription())["tier"] == 3
    assert calls == ["/user/subscription"] * 2


async def test_a_second_failed_connection_is_the_callers_to_report(monkeypatch):
    monkeypatch.setattr(client_module, "_RETRY_PAUSE_S", 0.0)
    handler, calls = _failing_first(2, httpx.Response(200, content=b""))
    with pytest.raises(httpx.ConnectError) as caught:
        await _client(handler).generate(GenerateParams(prompt="p"))
    assert len(calls) == 2  # never a third
    assert errors.describe(caught.value).startswith("ConnectError: [SSL")


async def test_a_request_that_may_have_left_is_never_sent_twice(monkeypatch):
    monkeypatch.setattr(client_module, "_RETRY_PAUSE_S", 0.0)
    for lost in (httpx.ReadTimeout("no answer"), httpx.ReadError(""), httpx.RemoteProtocolError("Server disconnected")):
        calls: list[str] = []

        def handler(request: httpx.Request, exc: Exception = lost) -> httpx.Response:
            calls.append(request.url.path)
            raise exc
        with pytest.raises(type(lost)):
            await _client(handler).generate(GenerateParams(prompt="p"))
        assert len(calls) == 1, lost  # NovelAI may have charged it: a retry could charge again


async def test_the_stream_retries_a_connection_and_hands_its_answer_back(monkeypatch):
    monkeypatch.setattr(client_module, "_RETRY_PAUSE_S", 0.0)
    events = (b'data: {"event_type": "intermediate", "samp_ix": 0, "step_ix": 3, "image": "aaa"}\n\n'
              b'data: {"event_type": "final", "samp_ix": 0, "image": "bbb"}\n\n')
    closed: list[bool] = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield events

        async def aclose(self) -> None:
            closed.append(True)

    handler, calls = _failing_first(1, httpx.Response(200, stream=Body()))
    got = [e async for e in _client(handler).generate_stream(GenerateParams(prompt="p"))]
    assert [(e["type"], e["image"]) for e in got] == [("intermediate", "aaa"), ("final", "bbb")]
    assert len(calls) == 2 and closed  # tried once more, and the answer given back to the pool


async def test_the_stream_reports_novelai_refusing_it():
    stream = _client(lambda request: httpx.Response(402, text="not enough Anlas")).generate_stream(
        GenerateParams(prompt="p"))
    with pytest.raises(errors.NovelAINoAnlasError):
        await anext(stream)


def test_an_error_without_text_is_named_by_its_type():
    assert errors.describe(httpx.ReadTimeout("")) == "ReadTimeout"
    assert errors.describe(errors.NovelAINoAnlasError("NovelAI returned 402: x")) == "NovelAI returned 402: x"


async def test_requests_share_one_pool_per_event_loop_until_it_closes():
    first = client_module._pooled("https://x", 5)
    assert client_module._pooled("https://x", 5) is first
    await client_module.close_pools()
    assert first.is_closed and client_module._pooled("https://x", 5) is not first
    await client_module.close_pools()
