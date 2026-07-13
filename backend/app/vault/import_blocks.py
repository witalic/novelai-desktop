"""Bulk import of library blocks.

Two steps mirror the import window (design/library-import-mockup.html):
  * ``parse`` — read block JSON from raw texts (dropped/browsed files), a base64 zip (unzipped in
    memory), or a folder path (globbed). Normalise each into a candidate and flag the ones that
    duplicate an existing block (same polarity + prompt text).
  * ``save`` — commit a caller-selected set (new blocks with fresh ids, or a chosen existing id to
    replace in place).

Fully local: reads a user-picked path / an uploaded zip's bytes, no network. Zip contents are read
from the archive, never extracted to disk (no zip-slip). Guards cap file count / sizes.
"""
import base64
import binascii
import io
import json
import logging
import os
import zipfile
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

from app.vault import layout, service
from app.vault.models import BlockDoc

log = logging.getLogger(__name__)

_POLARITIES = {"positive", "negative"}
_MAX_FILES = 2000               # sanity cap on json files scanned per import
_MAX_ZIP_BYTES = 50 * 1024 * 1024      # compressed zip payload
_MAX_MEMBER_BYTES = 5 * 1024 * 1024    # per-file uncompressed guard (a block json is tiny)
_MAX_TOTAL_BYTES = 100 * 1024 * 1024   # cumulative uncompressed guard (zip-bomb: 50 MB zip -> ~10 GB)
_MAX_SCAN = 50_000                     # directory entries examined before a folder import bails (walk-DoS)


class ImportCandidate(BaseModel):
    name: str
    text: str
    polarity: str
    category: str
    tags: list[str]
    # Set when this duplicates a block already in the library (same polarity + normalised text).
    existing_id: str | None = None
    existing_name: str | None = None


class ImportSkip(BaseModel):
    source: str
    error: str


class ImportParseRequest(BaseModel):
    texts: list[str] = []       # raw JSON strings read in the renderer (dropped/browsed .json files)
    zip_b64: str | None = None  # a .zip of json files, base64-encoded
    path: str | None = None     # a folder (or single .json) the user picked via the OS dialog


class ImportParseResult(BaseModel):
    candidates: list[ImportCandidate]
    skipped: list[ImportSkip]


class ImportSaveRequest(BaseModel):
    blocks: list[BlockDoc]      # ids already resolved by the caller: fresh for new, existing for replace


class ImportSaveResult(BaseModel):
    saved: int
    errors: list[ImportSkip] = []


def _norm(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def _existing_index(settings) -> dict[str, tuple[str, str]]:  # noqa: ANN001
    """``"<polarity>|<normalised text>" -> (id, name)`` over every block on disk (files are truth)."""
    root = service.blocks_root(settings)
    idx: dict[str, tuple[str, str]] = {}
    if not root.is_dir():
        return idx
    for bj in root.glob("**/*.json"):
        try:
            d = json.loads(bj.read_text("utf-8"))
        except (OSError, ValueError):
            continue
        bid = d.get("id")
        text = str(d.get("text") or "")
        if not bid or not text.strip():
            continue
        idx.setdefault(f"{d.get('polarity', 'positive')}|{_norm(text)}", (bid, str(d.get("name") or "")))
    return idx


def _to_candidate(raw: object, source: str, existing: dict[str, tuple[str, str]]) -> ImportCandidate | ImportSkip:
    if not isinstance(raw, dict):
        return ImportSkip(source=source, error="not a JSON object")
    text = str(raw.get("text") or "").strip()
    if not text:
        return ImportSkip(source=source, error="missing 'text'")
    polarity = raw.get("polarity") if raw.get("polarity") in _POLARITIES else "positive"
    category = layout.slugify(str(raw.get("category") or "")) or "custom"
    name = str(raw.get("name") or "").strip()
    tags = [t.strip() for t in (raw.get("tags") or []) if isinstance(t, str) and t.strip()]
    ex = existing.get(f"{polarity}|{_norm(text)}")
    return ImportCandidate(
        name=name, text=text, polarity=polarity, category=category, tags=tags,
        existing_id=ex[0] if ex else None, existing_name=ex[1] if ex else None,
    )


def _add_text(source: str, txt: str, raws: list[tuple[str, object]], skips: list[ImportSkip]) -> None:
    try:
        data = json.loads(txt)
    except ValueError as exc:
        skips.append(ImportSkip(source=source, error=f"invalid JSON: {exc}"))
        return
    items = data if isinstance(data, list) else [data]
    for j, item in enumerate(items):
        raws.append((source if len(items) == 1 else f"{source}[{j}]", item))


def _collect(req: ImportParseRequest) -> tuple[list[tuple[str, object]], list[ImportSkip]]:
    raws: list[tuple[str, object]] = []
    skips: list[ImportSkip] = []

    for i, txt in enumerate(req.texts):
        _add_text(f"item {i + 1}", txt, raws, skips)

    if req.zip_b64:
        try:
            payload = base64.b64decode(req.zip_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Malformed zip data.") from exc
        if len(payload) > _MAX_ZIP_BYTES:
            raise HTTPException(status_code=400, detail="Zip is too large.")
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                members = [m for m in zf.infolist()
                           if m.filename.lower().endswith(".json") and not m.is_dir()]
                total = 0
                for m in members[:_MAX_FILES]:
                    if m.file_size > _MAX_MEMBER_BYTES:
                        skips.append(ImportSkip(source=m.filename, error="file too large"))
                        continue
                    total += m.file_size  # trust the declared size we already guarded per-member
                    if total > _MAX_TOTAL_BYTES:  # zip-bomb: stop before inflating gigabytes into memory
                        skips.append(ImportSkip(source=m.filename, error="import size limit reached"))
                        break
                    try:
                        _add_text(m.filename, zf.read(m).decode("utf-8"), raws, skips)
                    except (OSError, UnicodeDecodeError) as exc:
                        skips.append(ImportSkip(source=m.filename, error=str(exc)))
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=400, detail="Not a valid zip archive.") from exc

    if req.path:
        p = Path(req.path)
        if p.is_file() and p.suffix.lower() == ".json":
            files = [p]
        elif p.is_dir():
            # Bounded walk: cap both the matches (_MAX_FILES) AND the entries examined (_MAX_SCAN) so a
            # huge tree (e.g. a drive root) can't tie up the worker for minutes enumerating everything.
            files, scanned = [], 0
            for root, _dirs, names in os.walk(p):
                for name in names:
                    scanned += 1
                    if name.lower().endswith(".json"):
                        files.append(Path(root) / name)
                if len(files) >= _MAX_FILES or scanned >= _MAX_SCAN:
                    break
            files = sorted(files)[:_MAX_FILES]
        else:
            raise HTTPException(status_code=400, detail="Path is not a folder or a .json file.")
        for f in files:
            try:
                _add_text(f.name, f.read_text("utf-8"), raws, skips)
            except (OSError, UnicodeDecodeError) as exc:
                skips.append(ImportSkip(source=str(f), error=str(exc)))

    return raws, skips


def parse(settings, req: ImportParseRequest) -> ImportParseResult:  # noqa: ANN001
    raws, skips = _collect(req)
    existing = _existing_index(settings)
    candidates: list[ImportCandidate] = []
    for source, raw in raws:
        result = _to_candidate(raw, source, existing)
        (candidates if isinstance(result, ImportCandidate) else skips).append(result)
    return ImportParseResult(candidates=candidates, skipped=skips)


def save(settings, req: ImportSaveRequest) -> ImportSaveResult:  # noqa: ANN001
    """Upsert each block (caller set the ids). One failure is recorded, not fatal — the rest still land."""
    saved = 0
    errors: list[ImportSkip] = []
    for block in req.blocks:
        try:
            service.save_block(settings, block)
            saved += 1
        except HTTPException as exc:
            errors.append(ImportSkip(source=block.id, error=str(exc.detail)))
    return ImportSaveResult(saved=saved, errors=errors)
