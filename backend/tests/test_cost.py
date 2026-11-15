"""The server's Anlas estimate matches the UI's (frontend/src/presets/cost.test.ts — the same cases) and what NovelAI
charged: the V5 and V4.5 figures were read off the account's balance around real generations (2026-11-15)."""
from app.novelai.cost import anlas_cost

V5, V45 = "nai-diffusion-5-full", "nai-diffusion-4-5-full"


def test_v5_costs_half_again_the_base_that_v4_5_pays():
    assert anlas_cost(V45, 832, 1216, 28, 1, opus=False) == 20
    assert anlas_cost(V5, 832, 1216, 28, 1, opus=False) == 30
    assert anlas_cost(V5, 1088, 1920, 28, 1, opus=False) == 60
    assert anlas_cost("nai-diffusion-5-curated", 832, 1216, 28, 1, opus=False) == 30


def test_opus_gets_one_sample_free_within_its_limits():
    assert anlas_cost(V5, 1024, 1024, 28, 1, opus=True) == 0
    assert anlas_cost(V5, 1024, 1024, 28, 3, opus=True) == 60  # three, one free → 2 × 30
    assert anlas_cost(V5, 1024, 1024, 50, 1, opus=True) > 0
    assert anlas_cost(V5, 1088, 1920, 28, 1, opus=True) == 60


def test_a_used_up_v5_usage_limit_ends_the_free_v5_sample_only():
    assert anlas_cost(V5, 1024, 1024, 28, 1, opus=True, v5_limit_reached=True) == 30
    assert anlas_cost(V45, 1024, 1024, 28, 1, opus=True, v5_limit_reached=True) == 0


def test_novelai_refuses_an_image_over_140_anlas():
    assert anlas_cost(V5, 2048, 1536, 50, 1, opus=False) is None  # 150 a piece
    assert anlas_cost(V45, 2048, 1536, 50, 1, opus=False) == 100


def test_it_scales_with_samples_and_has_a_floor():
    assert anlas_cost(V45, 1024, 1024, 28, 2, opus=False) == 40
    assert anlas_cost(V5, 64, 64, 1, 1, opus=False) == 2
