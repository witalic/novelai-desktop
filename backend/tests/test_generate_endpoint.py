import base64

import httpx
import pytest

from app.main import app
from app.novelai._png import solid_png
from app.settings import NovelAISettings, Settings, VaultSettings, get_settings


@pytest.fixture
async def mock_client():
    # Force the offline mock client so the endpoint test never touches network or keychain.
    app.dependency_overrides[get_settings] = lambda: Settings(
        novelai=NovelAISettings(mock=True), _env_file=None
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def test_generate_mock_returns_images(mock_client):
    resp = await mock_client.post("/api/generate", json={"prompt": "1girl", "width": 128, "height": 128})
    assert resp.status_code == 200
    body = resp.json()
    assert body["mock"] is True
    assert body["count"] == 1
    assert body["images"][0]  # base64 payload present


async def test_generate_rejects_empty_prompt(mock_client):
    resp = await mock_client.post("/api/generate", json={"prompt": ""})
    assert resp.status_code == 422  # pydantic min_length validation


@pytest.mark.parametrize("exc_path, expected", [
    ("app.novelai.errors.NovelAINoAnlasError", 402),   # out of Anlas → surfaced as 402, not a generic 500
    ("app.novelai.errors.NovelAIRateLimitError", 429),  # rate-limited → 429
    ("app.novelai.errors.NovelAIError", 502),           # no mapped status → generic 502
])
async def test_generate_surfaces_upstream_status(monkeypatch, exc_path, expected):
    import importlib
    mod_name, cls_name = exc_path.rsplit(".", 1)
    exc_cls = getattr(importlib.import_module(mod_name), cls_name)

    class _StubClient:
        is_mock = False
        async def generate(self, params):
            raise exc_cls("upstream failed")

    from app.routers import generate as gen
    monkeypatch.setattr(gen, "get_client", lambda settings: _StubClient())
    app.dependency_overrides[get_settings] = lambda: Settings(novelai=NovelAISettings(mock=True), _env_file=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/generate", json={"prompt": "1girl"})
    app.dependency_overrides.clear()
    assert resp.status_code == expected


async def test_download_writes_files(tmp_path):
    # state_dir pins app settings to tmp (no real settings.json), so download_dir falls back to the env value.
    app.dependency_overrides[get_settings] = lambda: Settings(
        download_dir=str(tmp_path), vault=VaultSettings(state_dir=str(tmp_path / "state")), _env_file=None
    )
    png_b64 = base64.b64encode(solid_png(8, 8)).decode("ascii")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/download", json={"images": [png_b64, png_b64]})
    app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["count"] == 2
    assert len(list(tmp_path.glob("*.png"))) == 2
