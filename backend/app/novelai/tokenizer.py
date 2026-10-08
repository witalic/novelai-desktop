"""Local prompt tokenization for the token-usage indicator ("X of 512"). Fully local: no NovelAI call,
no keychain, no network — the prompt never leaves the sidecar, so tests stay offline and there is no
Anlas cost or API load.

v4/v4.5 use a T5 tokenizer; we count against a bundled T5 SentencePiece model (``assets/t5_spiece.model``,
see ``assets/README.md``). NovelAI does not publish their exact vocab, so this is the standard t5-v1_1
tokenizer — token counts match the web UI closely for English tag prompts. Two calibration knobs if it
drifts: swap the bundled model, or flip ``_INCLUDE_EOS``. v5's Qwen tokenizer is not bundled yet and CLIP (v3)
is not wired (v3 is absent from the catalog): those, like any unknown tokenizer name, fall back to a coarse
heuristic rather than raising.
"""
import logging
import re
import sys
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


def _heuristic(text: str) -> int:
    """Coarse fallback (~4 chars/token) when a real tokenizer isn't available — never 0 for non-empty."""
    return (len(text) + 3) // 4


def count_tokens(text: str, tokenizer: str) -> int:
    """Token count for ``text`` under the named tokenizer ('t5'). Empty text → 0; any other tokenizer
    ('qwen' for now) or a load failure degrades to a heuristic so the indicator never crashes."""
    if not text.strip():
        return 0
    text = _strip_emphasis(text)  # count prompt content, not NovelAI's weight markers
    if not text:
        return 0
    if tokenizer == "t5":
        try:
            n = len(_t5().encode(text))
            return n + 1 if _INCLUDE_EOS else n
        except (ImportError, OSError, RuntimeError) as exc:  # lib/asset missing → degrade, don't 500
            log.warning("T5 tokenizer unavailable, falling back to heuristic: %s", exc)
    return _heuristic(text)
