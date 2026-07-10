"""On-disk (de)serialization: atomic ``work.json``, image bytes, sidecars, Pillow thumbnail.

Images are written BEFORE the manifest so a saved ``work.json`` never references a missing file
(``rules/backend.md``: no dangling refs).
"""
import base64
import json
from pathlib import Path

from app.vault import layout, migrate
from app.vault.models import WorkDoc


def read_work(work_dir: Path) -> WorkDoc:
    # All reads go through migrate.load_doc — on-disk files stay at their written schema_version
    # until the next save (lazy migration).
    return migrate.load_doc((work_dir / "work.json").read_text("utf-8"))


def write_work(work_dir: Path, doc: WorkDoc) -> None:
    images_dir = work_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    for im in doc.images:
        if im.image_b64:
            (images_dir / f"{im.id}.png").write_bytes(base64.b64decode(im.image_b64, validate=True))
            im.file = f"images/{im.id}.png"
            im.image_b64 = None
        sidecar = {
            "snapshot_id": im.snapshot_id, "role": im.role, "created_at": im.created_at, "tags": im.tags,
            "description": im.description, "group": im.group, "favorite": im.favorite, "source": im.source,
        }
        layout.atomic_write_text(images_dir / f"{im.id}.json", json.dumps(sidecar, ensure_ascii=False))
    # Draft-stack images live alongside gallery images (served by the same /images/{id} route).
    for st in doc.stack:
        if st.image_b64:
            (images_dir / f"{st.id}.png").write_bytes(base64.b64decode(st.image_b64, validate=True))
            st.file = f"images/{st.id}.png"
            st.image_b64 = None
    _gc_orphans(work_dir, {im.id for im in doc.images} | {st.id for st in doc.stack})
    layout.atomic_write_text(work_dir / "work.json", doc.model_dump_json())


def _gc_orphans(work_dir: Path, keep: set[str]) -> None:
    """Drop image files, sidecars, and thumbnails for images no longer in the work (e.g. removed on the
    canvas) so a human-readable vault doesn't accumulate orphans on every re-save."""
    images_dir = work_dir / "images"
    if images_dir.is_dir():
        for f in images_dir.iterdir():
            if f.stem not in keep:  # both <id>.png and <id>.json share the stem
                f.unlink(missing_ok=True)
    thumbs = work_dir / ".thumbs"
    if thumbs.is_dir():
        for f in thumbs.iterdir():
            if f.name.split("@", 1)[0] not in keep:  # <id>@<width>.png
                f.unlink(missing_ok=True)


def write_thumbnail(work_dir: Path, doc: WorkDoc) -> None:
    if not doc.preview_image_id:
        return
    src = work_dir / "images" / f"{doc.preview_image_id}.png"
    if not src.is_file():
        return
    from PIL import Image as PILImage

    with PILImage.open(src) as img:  # context-managed so the source handle is released (Windows: unblocks move/delete)
        img.thumbnail((512, 512))
        img.convert("RGB").save(work_dir / "preview.png")
