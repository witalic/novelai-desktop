"""Prompt tokenization endpoint — the token-usage indicator ("X of 512"). Fully local (no NovelAI call,
no keychain, no network); resolves model → tokenizer via the catalog and counts positive + negative in
one round trip. Behind the standard ``/api`` guard like every route, but safe offline / in tests."""
import logging

from fastapi import APIRouter
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.novelai.augment import augment
from app.novelai.catalog import ModelSpec, get_catalog
from app.novelai.tokenizer import count_tokens

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tokenize", tags=["tokenize"])


class TokenizeRequest(BaseModel):
    model: str
    positive: str = ""
    negative: str = ""
    quality_toggle: bool = False  # quality tags are appended to the positive → count against its budget
    uc_preset: int = 3  # the undesired-content preset is prepended to the negative (3 = None)


class TokenizeResponse(BaseModel):
    positive: int
    negative: int
    tokenizer: str  # echoed for transparency (which tokenizer produced the counts)


def _spec_for(model: str) -> ModelSpec:
    cat = get_catalog()
    # Unknown model (e.g. an old preset for a since-removed model) → the default model's spec.
    return next((m for m in cat.models if m.id == model),
                next(m for m in cat.models if m.id == cat.default_model))


@router.post("", response_model=TokenizeResponse)
async def tokenize(req: TokenizeRequest) -> TokenizeResponse:
    tk = _spec_for(req.model).tokenizer
    # Count exactly the text generation sends — the same augmentation build_body applies (quality tags,
    # UC preset), so the indicator matches the web UI. CPU-bound → off the event loop (see account router).
    text = augment(req.model, req.positive, req.negative, quality=req.quality_toggle, uc_preset=req.uc_preset)
    pos = await run_in_threadpool(count_tokens, text.positive, tk)
    neg = await run_in_threadpool(count_tokens, text.negative, tk)
    return TokenizeResponse(positive=pos, negative=neg, tokenizer=tk)
