"""What agents change, told to the app as it happens (``rules/security.md``: nothing happens silently).

- **Notices** — every agent write publishes one to the open app over SSE (``GET /api/agent/events``): a toast
  says what happened, and the Library, the Works list and an open work refresh at once.
- **The work log** — each agent edit of a work records the operations it applied and the ``updated_at`` it
  replaced. The app's open copy of that work (unsaved canvas edits and all) catches up by replaying them
  (``GET /api/agent/works/{id}/changes?since=<its base>``) instead of reloading on a 409 and losing its edits.
  Only an unbroken chain of agent edits from that base to the stored copy can be replayed; a save from anywhere
  else in between is a real conflict.

In memory for the app's lifetime, bounded. Agent tools run in worker threads; subscribers live on the event loop.
"""
import asyncio
import contextlib
import threading
from collections import OrderedDict, deque
from collections.abc import Iterator

_EDITS_PER_WORK = 50  # an open copy catches up on every notice — it never falls this far behind
_WORKS = 20  # works whose edits are remembered, most recently edited first
_QUEUE = 100  # notices held for one slow subscriber; past that it misses some (the work log still has the edits)

# Held across an agent's whole edit of a work — load, save, log — so agent edits never overlap, and a reader of
# the log (GET …/changes) never sees the saved copy before the edit that made it is logged.
edit_lock = threading.Lock()

_lock = threading.Lock()
_subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
_log: OrderedDict[str, deque[dict]] = OrderedDict()


def publish(notice: dict) -> None:
    with _lock:
        subs = list(_subscribers)
    for loop, queue in subs:
        with contextlib.suppress(RuntimeError):  # that loop is gone (shutting down)
            loop.call_soon_threadsafe(_offer, queue, notice)


def _offer(queue: asyncio.Queue, notice: dict) -> None:
    with contextlib.suppress(asyncio.QueueFull):
        queue.put_nowait(notice)


@contextlib.contextmanager
def subscribe() -> Iterator[asyncio.Queue]:
    entry = (asyncio.get_running_loop(), asyncio.Queue(maxsize=_QUEUE))
    with _lock:
        _subscribers.add(entry)
    try:
        yield entry[1]
    finally:
        with _lock:
            _subscribers.discard(entry)


def record_work_edit(work_id: str, prev: str, at: str, ops: list[dict]) -> None:
    """An agent saved ``work_id``: the copy stamped ``prev`` became ``at`` through ``ops``."""
    with _lock:
        _log.setdefault(work_id, deque(maxlen=_EDITS_PER_WORK)).append({"prev": prev, "at": at, "ops": ops})
        _log.move_to_end(work_id)
        while len(_log) > _WORKS:
            _log.popitem(last=False)  # an open copy of a forgotten work just reloads on its next 409


def edits_since(work_id: str, since: str, stored: str) -> list[dict] | None:
    """The operations that turn the copy saved at ``since`` into the stored one (stamped ``stored``), or None
    when that isn't a chain of agent edits alone."""
    if since == stored:
        return []
    with _lock:
        entries = list(_log.get(work_id, ()))
    ops: list[dict] = []
    cur = since
    for e in entries:  # in save order, so one pass follows the chain
        if e["prev"] == cur:
            ops += e["ops"]
            cur = e["at"]
    return ops if cur == stored else None
