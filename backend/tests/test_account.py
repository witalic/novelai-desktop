"""Account subscription: parsing + the endpoint (mock client, offline)."""
import httpx
import pytest

from app.main import app
from app.novelai.client import parse_subscription
from app.settings import NovelAISettings, Settings, get_settings


def test_parse_subscription_sums_anlas_and_maps_tier():
    parsed = parse_subscription({
        "tier": 3, "active": True,
        "trainingStepsLeft": {"fixedTrainingStepsLeft": 1000, "purchasedTrainingSteps": 5821},
    })
    assert parsed == {"tier": 3, "tier_name": "Opus", "active": True, "anlas": 6821}


def test_parse_subscription_tolerates_missing_fields():
    assert parse_subscription({}) == {"tier": 0, "tier_name": "Paper", "active": False, "anlas": 0}


@pytest.fixture
async def mock_client():
    app.dependency_overrides[get_settings] = lambda: Settings(novelai=NovelAISettings(mock=True), _env_file=None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def test_subscription_endpoint_returns_offline_stand_in(mock_client):
    body = (await mock_client.get("/api/account/subscription")).json()
    assert body["tier"] == 3 and body["tier_name"] == "Opus" and body["active"] is True
    assert body["anlas"] > 0
