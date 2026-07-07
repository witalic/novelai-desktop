"""NovelAI image generation — thin client over the unofficial API (``rules/novelai-api.md``)."""
from app.keychain import get_novelai_token
from app.novelai.client import NovelAIClient
from app.novelai.mock import MockNovelAIClient
from app.novelai.models import GenerateParams
from app.settings import Settings

__all__ = ["GenerateParams", "NovelAIClient", "MockNovelAIClient", "get_client"]


def get_client(settings: Settings) -> NovelAIClient | MockNovelAIClient:
    """Pick the real client when a token is available, else the offline mock (no network, no Anlas)."""
    if settings.novelai.mock:
        return MockNovelAIClient()
    token = get_novelai_token()
    if not token:
        return MockNovelAIClient()
    return NovelAIClient(
        token,
        base_url=settings.novelai.base_url,
        timeout_s=settings.novelai.timeout_s,
    )
