"""Prompt tokenization endpoint — the token-usage indicator ("X of 512"). Fully local (no NovelAI call,
no keychain, no network); resolves model → tokenizer via the catalog and counts positive + negative in
one round trip. Behind the standard ``/api`` guard like every route, but safe offline / in tests."""
import logging

from fastapi import APIRouter
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.novelai.augment import quality_tokens, uc_negative
from app.novelai.catalog import ModelSpec, get_catalog
from app.novelai.tokenizer import count_tokens

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tokenize", tags=["tokenize"])


class TokenizeRequest(BaseModel):
    model: str
    positive: str = ""
    negative: str = ""
    quality_toggle: bool = False  # qualityToggle prepends quality tags → count against the positive budget
    uc_preset: int = 3  # ucPreset prepends an undesired-content negative (3 = None); counts on the negative


class TokenizeResponse(BaseModel):
    positive: int
    negative: int
    tokenizer: str  # echoed for transparency (which tokenizer produced the counts)


def _spec_for(model: str) -> ModelSpec:
    cat = get_catalog()
    # Unknown model (e.g. an old preset for a since-removed model) → the default model's spec.
    return next((m for m in cat.models if m.id == model),
                next(m for m in cat.models if m.id == cat.default_model))


def _join(prefix: str, text: str) -> str:
    prefix, text = prefix.strip(), text.strip()
    return f"{prefix}, {text}" if prefix and text else (prefix or text)


@router.post("", response_model=TokenizeResponse)
async def tokenize(req: TokenizeRequest) -> TokenizeResponse:
    spec = _spec_for(req.model)
    tk = spec.tokenizer
    # The web UI counts what NovelAI actually sends: quality tags prepended to the positive, the ucPreset
    # undesired-content prepended to the negative. Prepend then count (one EOS), so an empty user negative
    # with a ucPreset still counts the preset's text. CPU-bound → off the event loop (see account router).
    pos = await run_in_threadpool(count_tokens, req.positive, tk)
    if req.quality_toggle and req.positive.strip():
        pos += quality_tokens(req.model)
    full_negative = _join(uc_negative(req.model, req.uc_preset), req.negative)
    neg = await run_in_threadpool(count_tokens, full_negative, tk)
    return TokenizeResponse(positive=pos, negative=neg, tokenizer=tk)
