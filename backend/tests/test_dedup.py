"""The backend "unique tags" rule matches the station's (frontend/src/canvas/dedup.test.ts — the same cases)."""
import pytest

from app.novelai.dedup import dedupe_prompt


@pytest.mark.parametrize(("prompt", "expected"), [
    ("1girl, long hair, blue eyes", "1girl, long hair, blue eyes"),  # no duplicates: unchanged
    ("long hair, blue eyes, long hair, long hair", "1.5::long hair::, blue eyes"),  # summed 3.0, capped
    ("Long Hair, long hair", "1.5::Long Hair::"),  # case-insensitive, first display form kept
    ("1.3::long hair::, long hair", "1.5::long hair::"),  # weighted + plain, capped
    ("2::detailed eyes::, 1girl", "2::detailed eyes::, 1girl"),  # a single weighted tag is never capped
    ("1.3::a, b::, c", "1.3::a, b::, c"),  # a weighted group is atomic
    ("", ""),
    ("  ,  , ", ""),
    ("0.6::smile::, 0.6::smile::", "1.2::smile::"),  # merged below the cap keeps its sum
])
def test_matches_the_station(prompt, expected):
    assert dedupe_prompt(prompt) == expected


# Where Python's own semantics would differ from the station's JavaScript (expected values are what dedup.ts gives).
@pytest.mark.parametrize(("prompt", "expected"), [
    ("1.125::a::", "1.13::a::"),  # Math.round: halves go up (Python's round() would give 1.12)
    ("0.625::a::, 0.5::a::", "1.13::a::"),
    ("-0::a::", "0::a::"),  # never "-0"
    ("1234567::a::", "1234567::a::"),  # no exponent form
    ("１::a::", "１::a::"),  # a fullwidth digit is no weight to JS
    ("﻿a, a", "1.5::a::"),  # JS trim() strips a BOM
    ("1.5::a\rb::, 1.5::a\rb::", "1.5::1.5::a\rb::::"),  # JS . stops at \r: the group isn't one
])
def test_follows_javascript_where_python_differs(prompt, expected):
    assert dedupe_prompt(prompt) == expected
