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


class LogSettings(BaseModel):
    level: str = "INFO"


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


@lru_cache
def get_settings() -> Settings:
    return Settings()
