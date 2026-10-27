"""Edits of a stored work made on the backend, not by the copy the app holds open: load → change → save on the loaded
copy as the lock base → log the operations (``worklog``). A copy of that work open in the app — unsaved edits and all —
catches up by replaying them (``frontend/src/vault/agentOps.ts``) instead of meeting a conflict.
"""
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

from app.settings import Settings
from app.vault import service, worklog
from app.vault.models import WorkDoc


def _dump(value: object) -> object:
    return value.model_dump() if isinstance(value, BaseModel) else value


def title_of(doc: WorkDoc) -> str:
    """A work's title as messages quote it."""
    return f"“{doc.title or 'Untitled'}”"


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"  # the app's own id shape (frontend vault/ids.ts)


def edit_work(settings: Settings, work_id: str, change: Callable[[WorkDoc], list[dict]],
              adopt: dict[str, Path] | None = None) -> tuple[WorkDoc, list[dict]]:
    """Apply ``change`` (it returns the operations it made — the open app replays them) and save, copying in the
    ``adopt`` files (``store.write_work``). When the app saves the work in between (409), load it again and redo the
    change on the fresh copy."""
    with worklog.edit_lock:
        for _ in range(3):
            doc = service.load_work(settings, work_id)
            prev = doc.updated_at
            ops = change(doc)
            if not ops:
                return doc, []
            try:
                saved = service.save_work(settings, doc, adopt=adopt)  # a 404 if deleted meanwhile (the loaded base)
            except HTTPException as exc:
                if exc.status_code != 409 or service.load_work(settings, work_id).updated_at == prev:
                    raise  # not a concurrent edit (e.g. a vault move in progress)
                continue
            # Dumped after the save, which stamps what the change left empty (a new combination's times).
            ops = json.loads(json.dumps(ops, default=_dump))
            worklog.record_work_edit(work_id, prev, saved["updated_at"], ops)
            return doc, ops
    raise HTTPException(status_code=409, detail="The work kept changing while this edit was being saved — try again.")


def purge_images(doc: WorkDoc, ids: set[str]) -> None:
    """Delete images from every structure that names them — the images list, the canvas image nodes, every
    gallery grid album — and move the preview on. Mirrors ``frontend/src/vault/workOps.ts`` ``purgeImages``."""
    doc.images = [im for im in doc.images if im.id not in ids]
    nodes = doc.canvas.get("nodes")
    if isinstance(nodes, list):
        doc.canvas["nodes"] = nodes = [n for n in nodes if not (isinstance(n, dict) and n.get("type") == "image"
                                                                 and n.get("id") in ids)]
        for n in nodes:
            data = n.get("data") if isinstance(n, dict) else None
            if isinstance(data, dict) and n.get("type") == "zone" and data.get("role") == "gallery":
                for block in data.get("blocks") or []:
                    if isinstance(block, dict) and isinstance(block.get("imageIds"), list):
                        block["imageIds"] = [i for i in block["imageIds"] if i not in ids]
    if doc.preview_image_id in ids:
        doc.preview_image_id = next((im.id for im in doc.images if im.role == "gallery"), None)


@dataclass(frozen=True)
class Transfer:
    """What a move or copy did: ``ids`` maps each image to its id in ``dst``. A move whose removal from ``src``
    failed landed as a copy — ``not_removed`` says why."""

    src: WorkDoc
    dst: WorkDoc
    ids: dict[str, str]
    copied: bool
    not_removed: str | None = None


def transfer_images(settings: Settings, src_id: str, dst_id: str, image_ids: list[str], *, copy: bool) -> Transfer:
    """Gallery images of one work into another's Quick access, each with its recipe and metadata, as it was — under
    new ids (ids are the vault's: one image, one id). The files are copied in as the target saves; a move then removes
    them from the source."""
    image_ids = list(dict.fromkeys(image_ids))
    if not image_ids:
        raise HTTPException(status_code=400, detail="Name at least one image.")
    if src_id == dst_id:
        raise HTTPException(status_code=400, detail="The images are in that work already.")
    src = service.load_work(settings, src_id)
    in_gallery = {im.id: im for im in src.images if im.role == "gallery"}
    if missing := next((i for i in image_ids if i not in in_gallery), None):
        raise HTTPException(status_code=404, detail=f"No gallery image “{missing}” in {title_of(src)}.")
    service.load_work(settings, dst_id)  # it exists — before any file is touched
    snaps = {s.id: s for s in src.snapshots}
    new_ids = {i: new_id("img") for i in image_ids}
    new_snaps = {s: new_id("snap") for s in {in_gallery[i].snapshot_id for i in image_ids} if s in snaps}
    adopt = {new_ids[i]: service.image_path(settings, src_id, i) for i in image_ids}

    def add(doc: WorkDoc) -> list[dict]:
        made = [in_gallery[i].model_copy(update={
            "id": new_ids[i], "snapshot_id": new_snaps.get(in_gallery[i].snapshot_id or ""), "file": ""})
            for i in image_ids]
        recipes = [snaps[s].model_copy(update={"id": new}) for s, new in new_snaps.items()]
        doc.snapshots += recipes
        doc.images += made
        doc.preview_image_id = doc.preview_image_id or made[0].id
        return [{"op": "images_added", "images": made, "snapshots": recipes}]

    try:
        dst, _ = edit_work(settings, dst_id, add, adopt)
    except FileNotFoundError as exc:  # one left the source since (a save without it there, its deletion)
        what = "copied" if copy else "moved"
        raise HTTPException(status_code=409, detail=f"An image left {title_of(src)} while it was being {what} — "
                                                    "nothing changed, try again.") from exc
    if copy:
        return Transfer(src, dst, new_ids, copied=True)

    def remove(doc: WorkDoc) -> list[dict]:
        gone = [i for i in image_ids if any(im.id == i and im.role == "gallery" for im in doc.images)]
        purge_images(doc, set(gone))
        return [{"op": "images_deleted", "ids": gone}] if gone else []

    try:
        edit_work(settings, src_id, remove)
    except (HTTPException, OSError) as exc:  # the copy has landed: report it as one, never lose track of it
        reason = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return Transfer(src, dst, new_ids, copied=True,
                        not_removed=f"Removing them from {title_of(src)} failed: {reason}")
    return Transfer(src, dst, new_ids, copied=False)
