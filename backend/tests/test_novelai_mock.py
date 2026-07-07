import struct

from app.novelai._png import solid_png
from app.novelai.mock import MockNovelAIClient
from app.novelai.models import GenerateParams

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


async def test_mock_returns_valid_pngs():
    client = MockNovelAIClient()
    imgs = await client.generate(GenerateParams(prompt="x", width=128, height=192, n_samples=2))
    assert len(imgs) == 2
    for img in imgs:
        assert img[:8] == _PNG_SIG


def test_solid_png_has_requested_dimensions():
    png = solid_png(64, 96)
    assert png[:8] == _PNG_SIG
    width, height = struct.unpack(">II", png[16:24])  # IHDR width/height
    assert (width, height) == (64, 96)
