"""OS keychain access for secrets (``rules/security.md``): the single chokepoint for reading/writing
the NovelAI token. Secret values are never logged. Store the token out-of-band with the keyring CLI:

    .venv\\Scripts\\keyring.exe set novelai-desktop novelai-token
"""
import logging

import keyring
from keyring.errors import KeyringError

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


def set_novelai_token(token: str) -> None:
    """Store the NovelAI persistent token. The value is never logged."""
    keyring.set_password(SERVICE, _NOVELAI_TOKEN, token)
