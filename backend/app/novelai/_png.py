"""Stdlib-only PNG helpers — Pillow is deferred until the vault needs metadata parsing.

``solid_png`` builds a valid solid-colour PNG (for the mock client); ``unzip_pngs`` extracts the
PNG entries from NovelAI's ZIP response.
"""
import io
import re
import struct
import zipfile
import zlib

_SIG = b"\x89PNG\r\n\x1a\n"


def solid_png(width: int, height: int, rgb: tuple[int, int, int] = (44, 44, 52)) -> bytes:
    """Encode a solid-colour RGB PNG of the given size."""

    def _chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit, colour type 2 (RGB)
    row = bytes(rgb) * width
    raw = bytearray()
    for _ in range(height):
        raw.append(0)  # filter type 0 (None) per scanline
        raw.extend(row)
    idat = zlib.compress(bytes(raw), 6)
    return _SIG + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")


def _sample_index(name: str) -> tuple[int, str]:
    m = re.search(r"(\d+)", name)  # numeric order so image_10 sorts after image_2, not before
    return (int(m.group(1)) if m else 0, name)


def unzip_pngs(data: bytes) -> list[bytes]:
    """Return the PNG entries of a NovelAI ZIP response, ordered by sample index (image_0, image_1, ...)."""
    out: list[bytes] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in sorted(zf.namelist(), key=_sample_index):
            if name.lower().endswith(".png"):
                out.append(zf.read(name))
    if not out:
        raise ValueError("no PNG entries in NovelAI ZIP response")
    return out
