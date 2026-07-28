"""Tiny in-process, per-user sliding-window rate caps for flood-prone write
endpoints (capture, ingest). Not a security control — a DoS-annoyance guard
for a single-process trusted-LAN deployment. Deliberately process-local:
restarting resets it, multi-worker deployments each get their own window."""

import time
from collections import defaultdict, deque
from threading import Lock

_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_lock = Lock()

WINDOW_SECONDS = 60.0
LIMITS = {
    "capture": 30,
    "ingest": 6,
    "keys_request": 3,
    "feedback": 12,
    "delete": 20,
    "absence": 10,
    "ritual": 4,
    "memory": 10,
    "chat": 20,
    "write": 30,  # generic create-endpoint cap — content rows per person
    "artifact": 4,  # digest/readout/handoff each write a file per call
}
MAX_KEYS = 1024  # X-User is client-supplied — bound the key space


def check(surface: str, user: str) -> None:
    """Raise ValueError (→ 400) when user exceeded the per-minute cap."""
    limit = LIMITS.get(surface)
    if limit is None:
        return
    now = time.monotonic()
    key = (surface, user)
    with _lock:
        # shed drained windows so rotated X-User names can't grow the dict
        for k in [k for k, w in _hits.items() if k != key and w and now - w[-1] > WINDOW_SECONDS]:
            del _hits[k]
        if key not in _hits and len(_hits) >= MAX_KEYS:
            raise ValueError("slow down — too many distinct senders")
        window = _hits[key]
        while window and now - window[0] > WINDOW_SECONDS:
            window.popleft()
        if len(window) >= limit:
            raise ValueError(f"slow down — {surface} is capped at {limit}/minute per person")
        window.append(now)


def reset() -> None:
    with _lock:
        _hits.clear()
