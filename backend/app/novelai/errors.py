"""Typed NovelAI errors, mapped from HTTP status (``rules/novelai-api.md``: never swallow a non-200)."""


class NovelAIError(Exception):
    """Base error. ``http_status`` is the upstream status a router can surface (None => generic)."""

    http_status: int | None = None


class NovelAIAuthError(NovelAIError):
    http_status = 401


class NovelAINoAnlasError(NovelAIError):
    http_status = 402


class NovelAIRateLimitError(NovelAIError):
    http_status = 429


class NovelAIBadRequestError(NovelAIError):
    http_status = 400


_BY_STATUS: dict[int, type[NovelAIError]] = {
    400: NovelAIBadRequestError,
    401: NovelAIAuthError,
    402: NovelAINoAnlasError,
    429: NovelAIRateLimitError,
}


def map_response_error(status: int, text: str) -> NovelAIError:
    cls = _BY_STATUS.get(status, NovelAIError)
    return cls(f"NovelAI returned {status}: {text[:300]}")


def describe(exc: Exception) -> str:
    """An error as the user reads it. A network failure (httpx) is named by its type — a timeout or a dropped
    connection often carries no text at all, which left "Could not read the NovelAI subscription:" with nothing after."""
    text = str(exc)
    if isinstance(exc, NovelAIError):
        return text
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__
