"""On-disk (de)serialization: atomic ``work.json``, image bytes, sidecars, Pillow thumbnail.

Images are written BEFORE the manifest so a saved ``work.json`` never references a missing file
(``rules/backend.md``: no dangling refs).
"""
import base64
import json
import os
from pathlib import Path

from app.vault.models import WorkDoc


def read_work(work_dir: Path) -> WorkDoc:
    return WorkDoc.model_validate_json((work_dir / "work.json").read_text("utf-8"))


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, "utf-8")
    os.replace(tmp, path)


def write_work(work_dir: Path, doc: WorkDoc) -> None:
    images_dir = work_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    for im in doc.images:
        if im.image_b64:
            (images_dir / f"{im.id}.png").write_bytes(base64.b64decode(im.image_b64))
            im.file = f"images/{im.id}.png"
            im.image_b64 = None
        sidecar = {
            "snapshot_id": im.snapshot_id, "params": im.params, "tags": im.tags,
            "description": im.description, "group": im.group, "favorite": im.favorite, "source": im.source,
        }
        (images_dir / f"{im.id}.json").write_text(json.dumps(sidecar, ensure_ascii=False), "utf-8")
    _atomic_write_text(work_dir / "work.json", doc.model_dump_json())


def write_thumbnail(work_dir: Path, doc: WorkDoc) -> None:
    if not doc.preview_image_id:
        return
    src = work_dir / "images" / f"{doc.preview_image_id}.png"
    if not src.is_file():
        return
    from PIL import Image as PILImage

    img = PILImage.open(src)
    img.thumbnail((512, 512))
    img.convert("RGB").save(work_dir / "preview.png")
