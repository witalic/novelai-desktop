"""What agents change, told to the app as it happens (``rules/security.md``: nothing happens silently).

Every agent write publishes a **notice** to the open app over SSE (``GET /api/agent/events``): a toast says what
happened, and the Library, the Works list and an open work refresh at once. An open work catches up on an agent's edit
through the work log (``app.vault.worklog``), which keeps the operations every backend edit applied.

In memory for the app's lifetime, bounded. Agent tools run in worker threads; subscribers live on the event loop.
"""
import asyncio
import contextlib
import threading
from collections.abc import Iterator

_QUEUE = 100  # notices held for one slow subscriber; past that it misses some (the work log still has the edits)

_lock = threading.Lock()
_subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()


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
