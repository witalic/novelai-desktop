"""OS keychain access for secrets (``rules/security.md``): the single chokepoint for reading/writing
the NovelAI token. Secret values are never logged. Store the token out-of-band with the keyring CLI:

    .venv\\Scripts\\keyring.exe set novelai-desktop novelai-token
"""
import logging

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

log = logging.getLogger(__name__)

SERVICE = "novelai-desktop"
_NOVELAI_TOKEN = "novelai-token"


def get_novelai_token() -> str | None:
    """Return the stored NovelAI persistent token, or None if unset / no keychain backend."""
    try:
        return keyring.get_password(SERVICE, _NOVELAI_TOKEN)
    except KeyringError as exc:  # missing/headless backend — never crash the app
        log.warning("Keychain read failed: %s", exc)
        return None


def has_novelai_token() -> bool:
    """Whether a token is stored — checked without ever returning the value to callers."""
    return bool(get_novelai_token())


def set_novelai_token(token: str) -> None:
    """Store the NovelAI persistent token. The value is never logged."""
    keyring.set_password(SERVICE, _NOVELAI_TOKEN, token)


def delete_novelai_token() -> None:
    """Remove the stored token (no-op if none / no backend)."""
    try:
        keyring.delete_password(SERVICE, _NOVELAI_TOKEN)
    except (PasswordDeleteError, KeyringError) as exc:
        log.warning("Keychain delete skipped: %s", exc)
