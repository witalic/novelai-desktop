"""The work log: each backend edit of a work (``workedit``) records the operations it applied and the ``updated_at`` it
replaced. The app's open copy of that work (unsaved canvas edits and all) catches up by replaying them
(``GET /api/agent/works/{id}/changes?since=<its base>``) instead of reloading on a 409 and losing its edits. Only an
unbroken chain of logged edits from that base to the stored copy can be replayed; a save from anywhere else in between
is a real conflict.

In memory for the app's lifetime, bounded. Edits run in worker threads.
"""
import threading
from collections import OrderedDict, deque

_EDITS_PER_WORK = 50  # an open copy catches up on every notice — it never falls this far behind
_WORKS = 20  # works whose edits are remembered, most recently edited first

# Held across a whole edit of a work — load, save, log — so backend edits never overlap, and a reader of the log
# (GET …/changes) never sees the saved copy before the edit that made it is logged.
edit_lock = threading.Lock()

_lock = threading.Lock()
_log: OrderedDict[str, deque[dict]] = OrderedDict()


def record_work_edit(work_id: str, prev: str, at: str, ops: list[dict]) -> None:
    """``work_id`` was saved: the copy stamped ``prev`` became ``at`` through ``ops``."""
    with _lock:
        _log.setdefault(work_id, deque(maxlen=_EDITS_PER_WORK)).append({"prev": prev, "at": at, "ops": ops})
        _log.move_to_end(work_id)
        while len(_log) > _WORKS:
            _log.popitem(last=False)  # an open copy of a forgotten work just reloads on its next 409


def edits_since(work_id: str, since: str, stored: str) -> list[dict] | None:
    """The operations that turn the copy saved at ``since`` into the stored one (stamped ``stored``), or None
    when that isn't a chain of logged edits alone."""
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
