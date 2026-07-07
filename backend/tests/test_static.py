"""While frontend/dist is absent (skeleton state), /app/ serves the placeholder."""
import httpx
import pytest

from app.main import _DIST, create_app


@pytest.fixture
async def fresh_client():
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.skipif(_DIST.is_dir(), reason="frontend is built; StaticFiles serves it instead of the placeholder")
async def test_app_placeholder(fresh_client):
    resp = await fresh_client.get("/app/")
    assert resp.status_code == 200
    assert "not built yet" in resp.text.lower()
