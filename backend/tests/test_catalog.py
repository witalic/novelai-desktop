"""Model catalog: shape invariants + the endpoint + a drift guard against the built-in presets."""
import httpx
import pytest

from app.main import app
from app.novelai.catalog import get_catalog
from app.vault.presets import _BUILTINS


def test_every_model_has_a_tokenizer_matching_its_family():
    for m in get_catalog().models:
        assert m.tokenizer == {"v3": "clip", "v4": "t5", "v5": "qwen"}[m.family]
        assert m.token_limit > 0 and m.negative_token_limit > 0


def test_v3_is_not_offered_yet():
    # build_body only emits the v4 shape (which v5 shares), so v3 stays out until its branch lands.
    assert all(m.family != "v3" for m in get_catalog().models)


def test_model_samplers_reference_known_ids_and_exclude_smea():
    cat = get_catalog()
    known = {s.id for s in cat.samplers}
    for m in cat.models:
        assert m.samplers, f"{m.id} offers no samplers"
        assert set(m.samplers) <= known, f"{m.id} references an unknown sampler"
        assert not any("smea" in s for s in m.samplers), "v4 models must not offer SMEA"


def test_default_model_is_listed():
    cat = get_catalog()
    assert cat.default_model in {m.id for m in cat.models}


def test_builtin_presets_do_not_drift_from_the_catalog():
    """Every built-in preset's (model, sampler, size) must exist in the catalog — so adding a built-in
    that references an unlisted model/sampler/size fails here instead of silently at runtime."""
    cat = get_catalog()
    model_ids = {m.id: m for m in cat.models}
    sizes = {(r.width, r.height) for r in cat.resolutions}
    for doc in _BUILTINS:
        p = doc.params
        assert p.model in model_ids, f"{doc.id}: model {p.model} not in catalog"
        assert p.sampler in model_ids[p.model].samplers, f"{doc.id}: sampler {p.sampler} unavailable for {p.model}"
        assert (p.width, p.height) in sizes, f"{doc.id}: size {p.width}x{p.height} not a catalog resolution"


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_catalog_endpoint_is_offline_and_complete(client):
    body = (await client.get("/api/catalog")).json()
    assert body["default_model"] == "nai-diffusion-5-full"
    assert {"nai-diffusion-5-full", "nai-diffusion-5-curated"} <= {m["id"] for m in body["models"]}
    assert body["dim_limits"]["step"] == 64
    assert any(m["tokenizer"] == "t5" for m in body["models"])
