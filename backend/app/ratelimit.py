"""Tiny in-process, per-user sliding-window rate caps for flood-prone write
endpoints (capture, ingest). Not a security control — a DoS-annoyance guard
for a single-process trusted-network deployment. Deliberately process-local:
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
    "verify": 2,  # full-chain walk over an unpruned table — the priciest read
    # the one surface an UNAUTHENTICATED caller can use to make us call out to
    # the identity provider. Generous for a person signing in, useless as an
    # amplifier. Keyed by client address, since a signed-out caller has no name.
    "signin": 10,
    # the forge webhook, metered as ONE integration rather than per pusher: a
    # signed caller must never be able to spend a named teammate's write
    # budget, and a busy monorepo pushes more than a person types.
    "forge": 120,
}
MAX_KEYS = 1024  # X-User is client-supplied — bound the key space
# What the cap counts, per surface. A signed-out caller has no name, so the
# signin cap counts addresses — and the refusal must not claim otherwise.
# Behind a reverse proxy that does not pass the caller's address through,
# every browser shares one address, and so one signin bucket.
PER = {"signin": "per address", "forge": "for the whole integration"}


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
            # evict the longest-idle window instead of refusing — a name
            # flood must not lock out the next REAL teammate's first write
            oldest = min((k for k in _hits if k != key), key=lambda k: _hits[k][-1], default=None)
            if oldest is not None:
                del _hits[oldest]
        window = _hits[key]
        while window and now - window[0] > WINDOW_SECONDS:
            window.popleft()
        if len(window) >= limit:
            scope = PER.get(surface, "per person")
            raise ValueError(f"slow down — {surface} is capped at {limit}/minute {scope}")
        window.append(now)


def reset() -> None:
    with _lock:
        _hits.clear()
