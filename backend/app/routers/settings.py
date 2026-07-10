"""App settings endpoints: persisted UI/behaviour prefs (theme, accent, autosave, download folder).

Secrets (the NovelAI token) are handled separately and stored in the keychain, never here.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException

from app import keychain
from app.settings import Settings, get_settings
from app.vault import manager
from app.vault.models import AppSettings, PatchSettings, SetToken, TokenStatus

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=AppSettings)
def get_app_settings(settings: Settings = Depends(get_settings)) -> AppSettings:
    return manager.get_settings(settings)


@router.patch("", response_model=AppSettings)
def patch_app_settings(body: PatchSettings, settings: Settings = Depends(get_settings)) -> AppSettings:
    return manager.patch_settings(settings, body)


# ---- NovelAI token (kept in the OS keychain; the value is never returned or logged) ----
@router.get("/novelai-token", response_model=TokenStatus)
def token_status() -> TokenStatus:
    return TokenStatus(set=keychain.has_novelai_token())


@router.put("/novelai-token", response_model=TokenStatus)
def set_token(body: SetToken) -> TokenStatus:
    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="Token is required.")
    keychain.set_novelai_token(token)
    return TokenStatus(set=True)


@router.delete("/novelai-token", response_model=TokenStatus)
def clear_token() -> TokenStatus:
    keychain.delete_novelai_token()
    return TokenStatus(set=False)
