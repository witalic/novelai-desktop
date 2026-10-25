"""Agent generations (the MCP ``generate_images`` tool): whether the agent may spend what one costs, and pacing
them — one at a time, a pause between, a growing back-off after NovelAI says "too many requests"
(``rules/novelai-api.md``: never hammer the API; respect Anlas).

Anlas rules (owner decision): a free generation (Opus: ≤ 1024×1024 px, ≤ 28 steps, the first image) is always
allowed while "Generate images" is on. A paid one needs paid generations switched on in Settings, the call itself
asking to spend (``paid: true`` — the user agreed to it), and to fit both the per-request cap and what is left of
the daily budget. The cost is the estimate (``novelai.cost``), checked before anything is sent; whether the account
has Opus's free generations is read from NovelAI right before each generation, never from a cache.
"""
import contextlib
import math
import threading
import time
from collections.abc import Iterator

import anyio

from app.agent import store
from app.agent.config import AgentConfig
from app.agent.errors import Refused
from app.novelai import MockNovelAIClient, NovelAIClient

_GAP_S = 3.0  # pause between two agent generations
_BACKOFF_START_S = 30.0  # after a 429 — doubled on each one in a row
_BACKOFF_MAX_S = 300.0
_ESTIMATE_TTL_S = 300.0  # how long a subscription read serves cost estimates (never a generation)

_lock = threading.Lock()  # one agent generation at a time — a second call is refused, never queued
_not_before = 0.0  # monotonic time the next agent generation may go out
_backoff = 0.0
_tier: tuple[float, bool] | None = None  # (read at, has Opus's free generations), for estimates only

Client = NovelAIClient | MockNovelAIClient


@contextlib.contextmanager
def slot() -> Iterator[None]:
    """Hold the one agent-generation slot (in a worker thread) once the pause after the previous one is over."""
    if not _lock.acquire(blocking=False):
        raise Refused("Another agent generation is running — try again in a few seconds.", "another one was running")
    try:
        wait = _not_before - time.monotonic()
        if wait > _GAP_S:  # backing off after a 429 — say so rather than hold the call for minutes
            raise Refused(f"NovelAI asked to slow down — try again in {math.ceil(wait)} s.", "backing off after a 429")
        if wait > 0:
            time.sleep(wait)
        yield
    finally:
        _lock.release()


def finished(*, rate_limited: bool = False) -> None:
    """A generation request went out: the next one waits the usual pause — or backs off after a 429."""
    global _not_before, _backoff
    _backoff = min(_backoff * 2 or _BACKOFF_START_S, _BACKOFF_MAX_S) if rate_limited else 0.0
    _not_before = time.monotonic() + max(_GAP_S, _backoff)


def has_opus(client: Client, *, for_estimate: bool = False) -> bool:
    """Whether the account gets Opus's free generations (an active tier 3). Read from NovelAI each time, except that
    an estimate may reuse a read from the last few minutes. Called from a worker thread; the client is async."""
    global _tier
    now = time.monotonic()
    if for_estimate and _tier is not None and now - _tier[0] < _ESTIMATE_TTL_S:
        return _tier[1]
    sub = anyio.from_thread.run(client.get_subscription)
    _tier = (now, bool(sub.get("active")) and int(sub.get("tier") or 0) >= 3)
    return _tier[1]


def refusal(cost: int, asked_to_pay: bool, cfg: AgentConfig, opus: bool) -> tuple[str, str] | None:
    """Why a generation costing ``cost`` Anlas (> 0) may not run — (what the agent reads, what the user's feed
    shows) — or None when it may."""
    s = cfg.spending
    if not asked_to_pay:
        free = ("Free ones are Opus generations up to 1024×1024 px, 28 steps, one image" if opus
                else "This account has no free generations")
        return (f"This generation costs about {cost} Anlas. {free}. Ask the user first — only if they agree to spend "
                "Anlas on it, call again with paid: true.", f"◆ {cost} — paid not requested")
    if not s.paid:
        return ("Paid generations are off in Settings → Agent access (Anlas for agent generations); only the user "
                "can turn them on.", f"◆ {cost} — paid generations are off")
    if cost > s.max_per_request:
        return (f"About {cost} Anlas is over the per-generation limit the user set (◆ {s.max_per_request}).",
                f"◆ {cost} — over the ◆ {s.max_per_request} limit")
    left = max(s.daily_budget - store.spent_today(cfg), 0)
    if cost > left:
        return (f"About {cost} Anlas is over what is left of today's budget (◆ {left} of {s.daily_budget}).",
                f"◆ {cost} — over today's budget (◆ {left} left)")
    return None
