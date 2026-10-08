"""Prompt tokenization: the count function + the endpoint (offline, no network)."""
import httpx
import pytest

from app.main import app
from app.novelai import tokenizer
from app.novelai.tokenizer import count_tokens


def test_empty_text_is_zero():
    assert count_tokens("", "t5") == 0
    assert count_tokens("   ", "t5") == 0


def test_t5_counts_real_tokens():
    # A multi-word prompt tokenizes to several T5 pieces (well above a 1-per-word floor).
    n = count_tokens("1girl, masterpiece, best quality, detailed background", "t5")
    assert n > 6


def test_novelai_weight_syntax_is_not_counted():
    # A weighted tag counts as just its text — the 1.3:: / :: markers are control syntax, not content.
    weighted = count_tokens("1.3::detailed eyes::", "t5")
    plain = count_tokens("detailed eyes", "t5")
    assert weighted == plain
    # And well below the naive count of the literal string with its markers.
    assert weighted < count_tokens("1.3 colon colon detailed eyes colon colon", "t5")


def test_brace_and_bracket_emphasis_is_not_counted():
    # v3-style {strengthen} / [weaken] emphasis is control syntax too — count only the text.
    assert count_tokens("{worst quality}", "t5") == count_tokens("worst quality", "t5")
    assert count_tokens("[[horror (theme)]]", "t5") == count_tokens("horror (theme)", "t5")


def test_unknown_tokenizer_falls_back_to_heuristic():
    # No 'clip' asset wired yet — must degrade to a count, never raise.
    assert count_tokens("a fairly long prompt string here", "clip") > 0


def test_loader_needs_no_network(monkeypatch):
    # Force the tokenizer load to fail (as if offline / lib missing) and prove we still get a count.
    def _boom():
        raise OSError("no model")

    monkeypatch.setattr(tokenizer, "_t5", _boom)  # monkeypatch restores the real loader after the test
    assert count_tokens("hello world", "t5") > 0


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_tokenize_endpoint_counts_positive_and_negative(client):
    body = (await client.post("/api/tokenize", json={
        "model": "nai-diffusion-4-5-full", "positive": "1girl, masterpiece", "negative": "",
    })).json()
    assert body["positive"] > 0
    assert body["negative"] == 0
    assert body["tokenizer"] == "t5"


async def test_quality_toggle_counts_the_appended_quality_tags(client):
    body = {"model": "nai-diffusion-4-5-full", "positive": "1girl, solo", "negative": ""}
    off = (await client.post("/api/tokenize", json={**body, "quality_toggle": False})).json()
    on = (await client.post("/api/tokenize", json={**body, "quality_toggle": True})).json()
    assert on["positive"] == off["positive"] + 8  # v4.5 Full quality tags (web-calibrated)
    assert on["negative"] == off["negative"]      # quality tags don't touch the negative
    # Curated's quality preset is longer (adds -0.8::feet::, rating:general) — counted as sent, not a constant.
    curated = (await client.post("/api/tokenize", json={**body, "model": "nai-diffusion-4-5-curated", "quality_toggle": True})).json()
    assert curated["positive"] > on["positive"]


async def test_matches_novelai_web_ui_reference_counts(client):
    # Calibrated against the NovelAI web UI (qualityToggle ON): the count must land exactly on these.
    cases = [
        (69, "1.3::babydoll::, 1.3::hot pink babydoll::, 1.3::sheer babydoll::, 1.3::see-through babydoll::, "
             "1.3::lace babydoll::, 1.2::matching panties::, 1.2::pink lace panties::, 1.2::garter belt::, "
             "1.2::white thigh highs::, 1.2::satin ribbon::, 1.2::bow accents::"),
        (34, "babydoll, hot pink babydoll, sheer babydoll, see-through babydoll"),
        (26, "hu tao (genshin impact), 1girl, solo"),
    ]
    for expected, positive in cases:
        body = (await client.post("/api/tokenize", json={
            "model": "nai-diffusion-4-5-full", "positive": positive, "negative": "", "quality_toggle": True,
        })).json()
        assert body["positive"] == expected, f"{positive!r} → {body['positive']} (want {expected})"


async def test_uc_preset_matches_novelai_web_ui_reference_counts(client):
    # ucPreset prepends an undesired-content negative; with an EMPTY user negative the count is exactly
    # the preset's text (verified against the web UI). None(3), Heavy(4), Light(5), Human(6), Furry(7).
    for uc, expected in [(3, 0), (4, 77), (5, 55), (6, 91), (7, 74)]:
        body = (await client.post("/api/tokenize", json={
            "model": "nai-diffusion-4-5-full", "positive": "", "negative": "", "uc_preset": uc,
        })).json()
        assert body["negative"] == expected, f"uc {uc} → {body['negative']} (want {expected})"


async def test_uc_preset_prepends_before_the_user_negative(client):
    # The preset text is prepended, so a user negative adds on top of the preset's count.
    base = (await client.post("/api/tokenize", json={
        "model": "nai-diffusion-4-5-full", "negative": "", "uc_preset": 4})).json()["negative"]
    withneg = (await client.post("/api/tokenize", json={
        "model": "nai-diffusion-4-5-full", "negative": "extra tag, another", "uc_preset": 4})).json()["negative"]
    assert withneg > base


async def test_tokenize_unknown_model_uses_default_tokenizer(client):
    body = (await client.post("/api/tokenize", json={
        "model": "nai-diffusion-legacy-9", "positive": "test", "negative": "bad",
    })).json()
    assert body["tokenizer"] == "t5"  # resolved via the catalog's default model
    assert body["positive"] > 0 and body["negative"] > 0
