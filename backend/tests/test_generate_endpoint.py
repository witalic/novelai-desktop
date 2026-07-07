import httpx
import pytest

from app.main import app
from app.settings import NovelAISettings, Settings, get_settings


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
