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
