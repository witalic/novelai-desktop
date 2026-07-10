"""The streaming endpoint emits SSE events ending in a final image — verified offline via the mock."""
import json

import httpx
import pytest

from app.main import app
from app.settings import NovelAISettings, Settings, get_settings


@pytest.fixture
async def mock_client():
    app.dependency_overrides[get_settings] = lambda: Settings(novelai=NovelAISettings(mock=True), _env_file=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def test_stream_emits_intermediates_then_final(mock_client):
    resp = await mock_client.post("/api/generate/stream", json={"prompt": "1girl"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = [json.loads(line[5:].strip()) for line in resp.text.split("\n") if line.startswith("data:")]
    kinds = [e["type"] for e in events]
    assert "intermediate" in kinds
    assert kinds[-1] == "final"
    assert events[-1]["image"]  # base64 payload present


async def test_multi_sample_stream_stamps_per_sample_seeds(mock_client):
    """M3 offline proof: NovelAI derives sample k as seed+k — the stream router stamps that on
    every final, and the mock mirrors the real per-sample event shape so this path stays covered."""
    resp = await mock_client.post("/api/generate/stream", json={"prompt": "1girl", "seed": 100, "n_samples": 2})
    assert resp.status_code == 200
    events = [json.loads(line[5:].strip()) for line in resp.text.split("\n") if line.startswith("data:")]
    finals = [e for e in events if e["type"] == "final"]
    assert len(finals) == 2                            # one final per sample
    assert [f["seed"] for f in finals] == [100, 101]   # sample k = seed + k
    assert all(f["mock"] for f in finals)
    assert {e["samp"] for e in events if e["type"] == "intermediate"} == {0, 1}  # previews are per-sample too
