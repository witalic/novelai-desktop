"""Model catalog endpoint — static NovelAI model/sampler/resolution facts (``novelai/catalog.py``).

Pure static data: no NovelAI call, no keychain. Behind the standard ``/api`` guard like every other
route, but safe offline / in tests / in mock. The frontend fetches it once and caches it.
"""
from fastapi import APIRouter

from app.novelai.catalog import Catalog, get_catalog

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("", response_model=Catalog)
async def catalog() -> Catalog:
    return get_catalog()
