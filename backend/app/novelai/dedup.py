"""The station's "unique tags" option, for prompts assembled on the backend (agent generations): merge repeated
tags, summing their weights. Mirrors ``frontend/src/canvas/dedup.ts`` down to JavaScript's own semantics — its
whitespace, its ASCII-only digits, its line terminators, its rounding — so the same composition sends the same
prompt from either side (``tests/test_dedup.py`` runs the frontend's cases and the edges where Python differs).

NovelAI treats repetition as emphasis (the weights stack), so duplicates are merged rather than dropped. A plain
tag weighs 1.0; ``1.3::tag::`` weighs 1.3. A tag seen once keeps its weight; a merged one is capped, so incidental
duplication across blocks can't turn into extreme emphasis. Weights are written to two decimals. Order is first
occurrence.
"""
import math
import re

MAX_WEIGHT = 1.5  # ceiling for a MERGED tag

# What JS String.prototype.trim() strips (WhiteSpace + LineTerminator) — not what str.strip() strips.
_JS_SPACE = " \t\n\v\f\r               " \
            "  　﻿"
# A weighted group ``N::content::`` is atomic (its content may hold commas); the rest splits on commas. ASCII
# digits and no line terminator inside, as JS's \d and . have it.
_WEIGHTED = re.compile(r"(-?[0-9]+(?:\.[0-9]+)?)::([^\n\r  ]*?)::")


def _parse(text: str) -> list[tuple[str, float]]:
    tags: list[tuple[str, float]] = []

    def plain(s: str) -> None:
        tags.extend((t, 1.0) for part in s.split(",") if (t := part.strip(_JS_SPACE)))

    last = 0
    for m in _WEIGHTED.finditer(text):
        plain(text[last:m.start()])
        if display := m.group(2).strip(_JS_SPACE):
            tags.append((display, float(m.group(1))))
        last = m.end()
    plain(text[last:])
    return tags


def _fmt(weight: float) -> str:
    # JS String(Math.round(w * 100) / 100): halves round up, and the shortest form ("1.5", "2", never "-0").
    v = math.floor(weight * 100 + 0.5) / 100
    return "0" if v == 0 else repr(v).removesuffix(".0")


def dedupe_prompt(text: str, cap: float = MAX_WEIGHT) -> str:
    merged: dict[str, tuple[str, float, int]] = {}  # lower-cased tag → (display, summed weight, count), first-seen order
    for display, weight in _parse(text):
        key = display.lower()
        if key in merged:
            first, total, count = merged[key]
            merged[key] = (first, total + weight, count + 1)
        else:
            merged[key] = (display, weight, 1)
    out = []
    for display, weight, count in merged.values():
        w = weight if count == 1 else min(cap, weight)  # a tag seen once is never capped
        out.append(display if abs(w - 1) < 1e-9 else f"{_fmt(w)}::{display}::")
    return ", ".join(out)
