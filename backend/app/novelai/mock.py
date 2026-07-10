"""Offline mock client — same interface as ``NovelAIClient``, no network, no Anlas spend.
Lets the UI and tests run without a token (``rules/novelai-api.md``)."""
import asyncio
import base64
import logging
from collections.abc import AsyncIterator

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

    async def generate_stream(self, params: GenerateParams) -> AsyncIterator[dict]:
        """Mirror the real client's event shape (client.py): per-sample intermediates then a final,
        each carrying ``samp`` — the stream router derives sample k's seed as ``seed + samp``, and
        that path must be exercisable offline (M3)."""
        log.info("MOCK stream %dx%d n=%d (no network, no Anlas)", params.width, params.height, params.n_samples)
        for samp in range(params.n_samples):
            for i in range(3):  # a few previews per sample — bounded, no tight loop
                await asyncio.sleep(0.15)
                png = solid_png(params.width, params.height, (44, (44 + i * 50) % 216, 52))
                yield {"type": "intermediate", "samp": samp, "step": i, "mime": "image/png", "image": base64.b64encode(png).decode("ascii")}
            png = solid_png(params.width, params.height, (44, (130 + samp * 40) % 216, 52))
            yield {"type": "final", "samp": samp, "mime": "image/png", "image": base64.b64encode(png).decode("ascii")}
