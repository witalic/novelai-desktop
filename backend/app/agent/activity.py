"""What agents did, for Settings → Agent activity (transparency, ``rules/security.md``: what an agent reads goes
on to its model provider, so reads are shown too), and which agents are connected. In memory for the app's
lifetime, the newest ``MAX_ENTRIES`` rows. Repeated reads of the same kind within a minute fold into one row with
a count, so a browsing agent doesn't drown the log; refusals and failures always get their own row.

Every row carries a ``seq`` that rises with each new row or fold, so the app can tell what arrived since the
user last looked."""
import threading
import time
from collections import deque
from datetime import datetime
from typing import Literal

Outcome = Literal["ok", "denied", "error"]
Kind = Literal["read", "write", "generate"]  # what the tool does — the Agent activity filters

MAX_ENTRIES = 200
_FOLD_S = 60  # reads of the same kind this close together share a row
_CONNECTED_WINDOW_S = 15 * 60  # an agent counts as connected if it talked to us within this window

_lock = threading.Lock()  # tools run in worker threads
_entries: deque[dict] = deque(maxlen=MAX_ENTRIES)
_sessions: dict[str, float] = {}  # MCP session id → monotonic time last seen
_seq = 0


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _next_seq() -> int:
    global _seq
    _seq += 1
    return _seq


def record(action: str, detail: str, outcome: Outcome, *, kind: Kind, cost: int | None = None) -> None:
    """One row; ``cost`` is the Anlas a generation spent (0: free) — None for everything else."""
    with _lock:
        _entries.appendleft({"seq": _next_seq(), "at": _now(), "kind": kind, "action": action, "detail": detail,
                             "outcome": outcome, "count": 1, "cost": cost, "_t": time.monotonic()})


def record_read(action: str, detail: str) -> None:
    """A read; folds into the newest row when that is the same kind of read from the last minute."""
    with _lock:
        top = _entries[0] if _entries else None
        if (top and top["kind"] == "read" and top["outcome"] == "ok" and top["action"] == action
                and time.monotonic() - top["_t"] < _FOLD_S):
            top.update(seq=_next_seq(), at=_now(), detail=detail, count=top["count"] + 1, _t=time.monotonic())
            return
        _entries.appendleft({"seq": _next_seq(), "at": _now(), "kind": "read", "action": action, "detail": detail,
                             "outcome": "ok", "count": 1, "cost": None, "_t": time.monotonic()})


def recent() -> list[dict]:
    """Newest first, without internal fields."""
    with _lock:
        return [{k: v for k, v in e.items() if not k.startswith("_")} for e in _entries]


def clear() -> None:
    """The user's Clear — ``seq`` keeps counting, so nothing old looks new afterwards."""
    with _lock:
        _entries.clear()


def touch_session(session_id: str) -> None:
    with _lock:
        _sessions[session_id] = time.monotonic()


def end_session(session_id: str) -> None:
    with _lock:
        _sessions.pop(session_id, None)


def connected() -> int:
    cutoff = time.monotonic() - _CONNECTED_WINDOW_S
    with _lock:
        for sid in [s for s, seen in _sessions.items() if seen < cutoff]:
            del _sessions[sid]
        return len(_sessions)


def forget_sessions() -> None:
    """A new token or a stopped listener disconnects everyone."""
    with _lock:
        _sessions.clear()
