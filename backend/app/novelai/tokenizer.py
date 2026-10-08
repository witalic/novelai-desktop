"""Local prompt tokenization for the token-usage indicator ("X of 512"). Fully local: no NovelAI call,
no keychain, no network — the prompt never leaves the sidecar, so tests stay offline and there is no
Anlas cost or API load.

v4/v4.5 use a T5 tokenizer; we count against a bundled T5 SentencePiece model (``assets/t5_spiece.model``,
see ``assets/README.md``). NovelAI does not publish their exact vocab, so this is the standard t5-v1_1
tokenizer — token counts match the web UI closely for English tag prompts. Two calibration knobs if it
drifts: swap the bundled model, or flip ``_INCLUDE_EOS``.

v5 uses Qwen 3.5's byte-level BPE. We bundle the exact vocabulary file the web client loads
(``assets/qwen35_tokenizer.def``) and port its encoder, so counts match the web UI token-for-token. CLIP (v3)
is not wired (v3 is absent from the catalog): it, like any unknown tokenizer name, falls back to a coarse
heuristic rather than raising.
"""
import json
import logging
import re
import sys
import unicodedata
import zlib
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

# Frozen (PyInstaller) builds extract bundled data under sys._MEIPASS, not next to this .py — resolve the
# asset from there when frozen so the packaged app finds the tokenizer model (the .spec bundles it here).
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _ASSETS = Path(sys._MEIPASS) / "app" / "novelai" / "assets"
else:
    _ASSETS = Path(__file__).resolve().parent / "assets"
_T5_MODEL = _ASSETS / "t5_spiece.model"
_INCLUDE_EOS = True  # T5 appends </s>; NovelAI's 512 budget counts it. Flip if the web UI disagrees.
_QWEN_DEF = _ASSETS / "qwen35_tokenizer.def"  # raw-deflate JSON: {config, specialTokens, vocab, merges}

# NovelAI emphasis syntax is control, not content — count only the text (matches the web UI):
#   * numeric weights ``1.3::content::`` (optionally signed) — strip the ``N::`` opener and every ``::``
#   * v3-style ``{strengthen}`` / ``[weaken]`` — strip the braces/brackets (nesting included)
# Without this the literal markers inflate the count massively (a weighted prompt read ~60% high).
_EMPHASIS = re.compile(r"(-?\d+(?:\.\d+)?)?::")


def _strip_emphasis(text: str) -> str:
    text = _EMPHASIS.sub("", text)
    text = re.sub(r"[{}\[\]]", "", text)
    return re.sub(r"\s+", " ", text).strip()


@lru_cache(maxsize=1)
def _t5():  # noqa: ANN202 — sentencepiece has no stable public type
    import sentencepiece as spm  # local import: only pulled in when a count is first requested

    return spm.SentencePieceProcessor(model_file=str(_T5_MODEL))


def _byte_chars() -> list[str]:
    """GPT-2's reversible byte → printable-char map; byte-level BPE vocabularies are written in it."""
    printable = [*range(ord("!"), ord("~") + 1), *range(ord("¡"), ord("¬") + 1), *range(ord("®"), ord("ÿ") + 1)]
    shifted = (b for b in range(256) if b not in printable)
    mapping = {b: chr(b) for b in printable} | {b: chr(256 + i) for i, b in enumerate(shifted)}
    return [mapping[b] for b in range(256)]


@lru_cache(maxsize=1)
def _qwen() -> Callable[[str], int]:
    """A token counter porting the web client's BPE encoder: NFC → special tokens whole → regex pre-split →
    byte-level BPE (lowest-rank pair first). Only counts are needed: every merge result is in the vocab, so
    the vocab itself isn't kept."""
    import regex  # local import: \p{L}-style classes the stdlib re lacks; only needed once v5 is counted

    cfg = json.loads(zlib.decompress(_QWEN_DEF.read_bytes(), -15))
    ranks = {(sys.intern(a), sys.intern(b)): i for i, (a, b) in enumerate(cfg["merges"])}
    split = regex.compile(cfg["config"]["splitRegex"])
    specials = regex.compile("|".join(map(regex.escape, sorted(cfg["specialTokens"], key=len, reverse=True))))
    normalization = cfg["config"].get("normalization")
    del cfg  # the 248k-entry vocab is the bulk of the file — drop it before counting starts
    byte_chars = _byte_chars()

    @lru_cache(maxsize=1 << 16)
    def word_tokens(word: str) -> int:
        parts = [byte_chars[b] for b in word.encode("utf-8")]
        while len(parts) > 1:
            pair = min(zip(parts, parts[1:]), key=lambda p: ranks.get(p, len(ranks)))
            if pair not in ranks:
                break
            merged, i = [], 0
            while i < len(parts):
                if i < len(parts) - 1 and (parts[i], parts[i + 1]) == pair:
                    merged.append(parts[i] + parts[i + 1])
                    i += 2
                else:
                    merged.append(parts[i])
                    i += 1
            parts = merged
        return len(parts)

    def plain(text: str) -> int:
        return sum(word_tokens(m.group(0)) for m in split.finditer(text))

    def count(text: str) -> int:
        if normalization:
            text = unicodedata.normalize(normalization, text)
        n, pos = 0, 0
        for m in specials.finditer(text):  # a special token (<|endoftext|>, …) is one token, never split
            n += plain(text[pos:m.start()]) + 1
            pos = m.end()
        return n + plain(text[pos:])

    return count


def _heuristic(text: str) -> int:
    """Coarse fallback (~4 chars/token) when a real tokenizer isn't available — never 0 for non-empty."""
    return (len(text) + 3) // 4


def count_tokens(text: str, tokenizer: str) -> int:
    """Token count for ``text`` under the named tokenizer ('t5' / 'qwen'). Empty text → 0; an unknown
    tokenizer or a load failure degrades to a heuristic so the indicator never crashes."""
    if not text.strip():
        return 0
    if tokenizer == "qwen":
        # The web UI counts v5 prompts as typed — weight markers included, no EOS — so we do too.
        try:
            return _qwen()(text)
        except (ImportError, OSError, ValueError, KeyError, zlib.error) as exc:  # missing/corrupt → degrade
            log.warning("Qwen tokenizer unavailable, falling back to heuristic: %s", exc)
            return _heuristic(text)
    text = _strip_emphasis(text)  # T5: count prompt content, not NovelAI's weight markers
    if not text:
        return 0
    if tokenizer == "t5":
        try:
            n = len(_t5().encode(text))
            return n + 1 if _INCLUDE_EOS else n
        except (ImportError, OSError, RuntimeError) as exc:  # lib/asset missing → degrade, don't 500
            log.warning("T5 tokenizer unavailable, falling back to heuristic: %s", exc)
    return _heuristic(text)
