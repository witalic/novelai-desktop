"""Offline mock client — same interface as ``NovelAIClient``, no network, no Anlas spend.
Lets the UI and tests run without a token (``rules/novelai-api.md``)."""
import logging

from app.novelai._png import solid_png
from app.novelai.models import GenerateParams

log = logging.getLogger(__name__)


class MockNovelAIClient:
    is_mock = True

    async def generate(self, params: GenerateParams) -> list[bytes]:
        log.info("MOCK generate %dx%d n=%d (no network, no Anlas)", params.width, params.height, params.n_samples)
        # Tint each sample differently so multiple mock outputs are visually distinguishable.
        return [
            solid_png(params.width, params.height, (44, (44 + i * 40) % 216, 52))
            for i in range(params.n_samples)
        ]
