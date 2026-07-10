"""Application settings, loaded from environment (prefix ``NAI_``) and an optional ``backend/.env``.

Secrets are never defined here — they live in the OS keychain (see ``rules/security.md``).
"""
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class ApiSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(default=8787, ge=1, le=65535)
    # Per-launch shared secret set by the Electron shell (env NAI_API__AUTH_TOKEN). When present, /api calls
    # must carry it in the `nai_auth` cookie, which the shell sets SameSite=Strict + HttpOnly: the browser
    # sends it on every same-origin request (fetch AND <img>/subresource loads) but never cross-site, so
    # CSRF / DNS-rebinding against the loopback API is closed without touching the frontend. Empty in dev.
    auth_token: str = ""


class LogSettings(BaseModel):
    level: str = "INFO"


class NovelAISettings(BaseModel):
    base_url: str = "https://image.novelai.net"
    model: str = "nai-diffusion-4-5-full"
    timeout_s: float = 120.0
    # Force the offline mock client (no network, no Anlas). When False and no token is in the
    # keychain, the client also falls back to mock — see app.novelai.get_client.
    mock: bool = False


class VaultSettings(BaseModel):
    # Explicit override (env NAI_VAULT__DIR); otherwise the active vault from app settings.
    dir: str | None = None
    # Where persisted app settings live (OS config dir by default; tests override to a tmp dir).
    state_dir: str | None = None
    # Base for the auto-created first-run vault (defaults to ~/Documents/novelai-vault; tests override).
    default_dir: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NAI_",
        env_nested_delimiter="__",
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api: ApiSettings = ApiSettings()
    log: LogSettings = LogSettings()
    novelai: NovelAISettings = NovelAISettings()
    vault: VaultSettings = VaultSettings()
    # Where "Download" writes images (the OS Downloads folder by default).
    download_dir: str = Field(default_factory=lambda: str(Path.home() / "Downloads"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
