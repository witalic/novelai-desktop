"""Quality tags + UC presets applied client-side, mirroring NovelAI's web client (offline, pure)."""
from app.novelai.augment import _PRESETS, augment
from app.novelai.catalog import get_catalog

V45, V45C = "nai-diffusion-4-5-full", "nai-diffusion-4-5-curated"
Q = "very aesthetic, masterpiece, no text"


def test_every_catalog_model_has_its_presets():
    # Drift guard: a model added to the catalog without its preset strings would silently send bare prompts.
    for m in get_catalog().models:
        assert m.id in _PRESETS, f"{m.id} has no quality/UC presets"
        for uc in m.uc_presets:
            assert uc == 3 or _PRESETS[m.id].uc.get(uc), f"{m.id} offers ucPreset {uc} without its text"


def test_quality_tags_are_appended_per_model():
    assert augment(V45, "1girl, solo", "", quality=True, uc_preset=3).positive == f"1girl, solo, {Q}"
    assert augment(V45C, "1girl", "", quality=True, uc_preset=3).positive == f"1girl, {Q}, -0.8::feet::, rating:general"


def test_quality_off_or_blank_prompt_is_left_alone():
    off = augment(V45, "1girl", "", quality=False, uc_preset=3)
    assert off.positive == "1girl" and off.quality_hint == 0
    blank = augment(V45, "  ", "", quality=True, uc_preset=3)
    assert blank.positive == "  " and blank.quality_hint == 0


def test_quality_tags_go_before_an_in_image_text_block():
    # Everything after "Text:" is drawn as text — quality tags must never land there.
    assert augment(V45, "1girl, sign, Text: Hello", "", quality=True, uc_preset=3).positive == \
        f"1girl, sign, {Q}, Text: Hello"
    assert augment(V45, "text: Hi", "", quality=True, uc_preset=3).positive == f"{Q}, text: Hi"
    # A weighted "text" tag (1.2::text::) is emphasis syntax, not a Text block.
    assert augment(V45, "1.2::text::, 1girl", "", quality=True, uc_preset=3).positive == f"1.2::text::, 1girl, {Q}"


def test_uc_preset_is_prepended_with_nsfw_on_full_models():
    heavy = augment(V45, "1girl", "bad hands", quality=False, uc_preset=4)
    assert heavy.negative.startswith("nsfw, lowres, artistic error, film grain,")
    assert heavy.negative.endswith("negative space, blank page, bad hands")
    assert heavy.uc_hint == 2
    # An empty user negative becomes exactly the preset (plus nsfw on Full).
    assert augment(V45, "1girl", "", quality=False, uc_preset=4).negative == "nsfw, " + _PRESETS[V45].uc[4]


def test_nsfw_is_skipped_when_the_prompt_asks_for_it_and_on_curated():
    assert not augment(V45, "1girl, NSFW", "", quality=False, uc_preset=4).negative.startswith("nsfw")
    assert augment(V45C, "1girl", "", quality=False, uc_preset=4).negative == _PRESETS[V45C].uc[4]


def test_uc_none_leaves_the_negative_untouched():
    none = augment(V45, "1girl", "bad hands", quality=False, uc_preset=3)
    assert none.negative == "bad hands" and none.uc_hint == 0


def test_unoffered_preset_is_clamped_and_unknown_model_sent_as_typed():
    clamped = augment(V45C, "x", "bad", quality=False, uc_preset=7)  # no Furry Focus on V4.5 Curated
    assert clamped.negative == "bad" and clamped.uc_hint == 0
    unknown = augment("nai-diffusion-9", "1girl", "bad", quality=True, uc_preset=4)
    assert unknown == ("1girl", "bad", 0, 0)
