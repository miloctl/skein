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
LIMITS = {"capture": 30, "ingest": 6}


def check(surface: str, user: str) -> None:
    """Raise ValueError (→ 400) when user exceeded the per-minute cap."""
    limit = LIMITS.get(surface)
    if limit is None:
        return
    now = time.monotonic()
    key = (surface, user)
    with _lock:
        window = _hits[key]
        while window and now - window[0] > WINDOW_SECONDS:
            window.popleft()
        if len(window) >= limit:
            raise ValueError(f"slow down — {surface} is capped at {limit}/minute per person")
        window.append(now)


def reset() -> None:
    with _lock:
        _hits.clear()
