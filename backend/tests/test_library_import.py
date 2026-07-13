"""Library import: parse (texts / zip / folder), duplicate detection, and bulk save (new + replace)."""
import base64
import io
import json
import zipfile

import httpx
import pytest

from app.main import app
from app.settings import Settings, VaultSettings, get_settings


@pytest.fixture
async def client(tmp_path):
    vault = tmp_path / "vault"
    app.dependency_overrides[get_settings] = lambda: Settings(
        vault=VaultSettings(dir=str(vault), state_dir=str(tmp_path / "state")), _env_file=None
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac, vault, tmp_path
    app.dependency_overrides.clear()


def _blk(**over):
    return {"category": "style", "name": "Nixeu", "text": "nixeu style, painterly",
            "polarity": "positive", "tags": ["style"], **over}


async def test_parse_single_and_list_json_texts(client):
    ac, _, _ = client
    body = {"texts": [json.dumps(_blk()), json.dumps([_blk(name="A", text="a, b"), _blk(name="B", text="c, d")])]}
    res = (await ac.post("/api/vault/library/import/parse", json=body)).json()
    assert len(res["candidates"]) == 3
    assert res["skipped"] == []
    assert res["candidates"][0]["category"] == "style" and res["candidates"][0]["polarity"] == "positive"


async def test_parse_skips_missing_text_and_bad_json(client):
    ac, _, _ = client
    body = {"texts": [json.dumps(_blk(text="   ")), "{not json}"]}
    res = (await ac.post("/api/vault/library/import/parse", json=body)).json()
    assert res["candidates"] == []
    assert len(res["skipped"]) == 2


async def test_parse_from_zip(client):
    ac, _, _ = client
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.json", json.dumps(_blk(text="from zip a")))
        zf.writestr("nested/b.json", json.dumps([_blk(text="from zip b"), _blk(text="from zip c")]))
        zf.writestr("readme.txt", "ignored")  # non-json ignored
    body = {"zip_b64": base64.b64encode(buf.getvalue()).decode("ascii")}
    res = (await ac.post("/api/vault/library/import/parse", json=body)).json()
    assert len(res["candidates"]) == 3


async def test_parse_from_folder_path(client):
    ac, _, tmp_path = client
    src = tmp_path / "import_src"
    (src / "sub").mkdir(parents=True)
    (src / "one.json").write_text(json.dumps(_blk(text="folder one")), "utf-8")
    (src / "sub" / "two.json").write_text(json.dumps(_blk(text="folder two")), "utf-8")
    res = (await ac.post("/api/vault/library/import/parse", json={"path": str(src)})).json()
    assert len(res["candidates"]) == 2


async def test_parse_nonexistent_path_is_400(client):
    ac, _, tmp_path = client
    r = await ac.post("/api/vault/library/import/parse", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 400


async def test_parse_malformed_zip_is_400(client):
    ac, _, _ = client
    bad = base64.b64encode(b"definitely not a zip archive").decode("ascii")
    r = await ac.post("/api/vault/library/import/parse", json={"zip_b64": bad})
    assert r.status_code == 400


async def test_parse_flags_duplicate_of_existing_block(client):
    ac, _, _ = client
    # An existing library block.
    await ac.post("/api/vault/library/blocks", json={
        "id": "existing1", "category": "style", "name": "Existing", "text": "long hair, blue eyes",
        "polarity": "positive", "tags": []})
    # A candidate with the SAME polarity + text (whitespace/case differ) → flagged as a duplicate.
    res = (await ac.post("/api/vault/library/import/parse", json={
        "texts": [json.dumps(_blk(text="Long Hair,  blue eyes"))]})).json()
    c = res["candidates"][0]
    assert c["existing_id"] == "existing1" and c["existing_name"] == "Existing"
    # A different text is NOT flagged.
    res2 = (await ac.post("/api/vault/library/import/parse", json={
        "texts": [json.dumps(_blk(text="short hair"))]})).json()
    assert res2["candidates"][0]["existing_id"] is None


async def test_bulk_save_new_and_replace(client):
    ac, vault, _ = client
    # Seed an existing block to replace.
    await ac.post("/api/vault/library/blocks", json={
        "id": "keep1", "category": "style", "name": "Old", "text": "old text", "polarity": "positive", "tags": []})
    blocks = [
        {"id": "new1", "category": "character", "name": "New A", "text": "new a", "polarity": "positive", "tags": ["x"]},
        {"id": "keep1", "category": "style", "name": "New Name", "text": "replaced text", "polarity": "positive", "tags": []},
    ]
    res = (await ac.post("/api/vault/library/import", json={"blocks": blocks})).json()
    assert res["saved"] == 2 and res["errors"] == []
    # New one landed; the replaced one overwrote in place (same id, new content).
    page = (await ac.get("/api/vault/library/blocks")).json()
    by_id = {b["id"]: b for b in page["items"]}
    assert by_id["new1"]["text"] == "new a"
    assert by_id["keep1"]["name"] == "New Name" and by_id["keep1"]["text"] == "replaced text"


async def test_bulk_save_records_invalid_without_aborting(client):
    ac, _, _ = client
    blocks = [
        {"id": "ok1", "category": "custom", "name": "", "text": "fine", "polarity": "positive", "tags": []},
        {"id": "bad id!", "category": "custom", "name": "", "text": "bad", "polarity": "positive", "tags": []},
    ]
    res = (await ac.post("/api/vault/library/import", json={"blocks": blocks})).json()
    assert res["saved"] == 1 and len(res["errors"]) == 1
