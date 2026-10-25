"""The server's Anlas estimate matches the UI's (frontend/src/presets/cost.test.ts — the same cases)."""
from app.novelai.cost import anlas_cost


def test_a_full_size_28_step_image_is_about_20_anlas():
    assert anlas_cost(1024, 1024, 28, 1, opus=False) == 20


def test_opus_gets_the_first_sample_free_within_its_limits():
    assert anlas_cost(1024, 1024, 28, 1, opus=True) == 0
    assert anlas_cost(1024, 1024, 28, 3, opus=True) == 40  # three, one free → 2 × 20
    assert anlas_cost(1024, 1024, 50, 1, opus=True) > 0
    assert anlas_cost(1088, 1920, 28, 1, opus=True) > 0


def test_it_scales_with_samples_and_has_floors():
    assert anlas_cost(1024, 1024, 28, 2, opus=False) == 40
    assert anlas_cost(64, 64, 1, 1, opus=False) == 2
