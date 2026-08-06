"""Tiny in-process, per-user sliding-window rate caps for flood-prone write
endpoints (capture, ingest). Not a security control — a DoS-annoyance guard
for a single-process trusted-network deployment. Deliberately process-local:
restarting resets it, multi-worker deployments each get their own window."""

import math
import time
from collections import defaultdict, deque
from threading import Lock

from . import config

_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
_lock = Lock()


class RateLimited(ValueError):
    """A refused call, carrying when a retry can succeed.

    Subclasses ValueError so the agent gate (tools/_gate.py) and the mock
    agent keep catching it as the plain-string refusal they hand to the
    model. main.py maps it to 429 with a Retry-After header — as a bare
    ValueError it answered 400, which made a refused-for-now request
    wire-identical to a malformed one, so no client could back off.
    """

    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.retry_after = retry_after


def client_addr(request) -> str:
    """The caller's address, for the per-address caps (signin, forge_addr).

    X-Forwarded-For is read only to the depth the operator declared in
    SKEIN_TRUST_PROXY_HOPS. Each trusted proxy APPENDS the peer address it
    saw, so the rightmost N entries are ours and entry -N is the client as
    the outermost trusted proxy saw it; everything left of that is
    caller-typed text. At 0 hops the header is ignored — trusting it on a
    direct connection lets any caller pick their own bucket key, which
    unmakes the cap."""
    hops = config.TRUST_PROXY_HOPS
    if hops > 0:
        raw = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return request.client.host if request.client else "unknown"


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
    # every delivery, counted by ADDRESS before the signature is checked. The
    # `forge` bucket above cannot cover an unsigned caller — it is keyed on a
    # name we only trust after verifying — so without this a caller holding no
    # credential buys an HMAC over the whole body at line rate.
    "forge_addr": 600,
}
MAX_KEYS = 1024  # X-User is client-supplied — bound the key space
# What the cap counts, per surface. A signed-out caller has no name, so the
# signin cap counts addresses — and the refusal must not claim otherwise.
# Behind a reverse proxy, SKEIN_TRUST_PROXY_HOPS is what makes an address
# mean a caller: at the default 0 every browser shares the proxy's address,
# and so one signin bucket for the whole deployment.
PER = {
    "signin": "per address",
    "forge": "for the whole integration",
    "forge_addr": "per address",
}


def check(surface: str, user: str, cost: int = 1) -> None:
    """Raise RateLimited (→ 429 + Retry-After, main.py) when user exceeded
    the per-minute cap.

    cost > 1 charges one request for several slots, for a surface where ONE
    call does the work of many. A flock turn is one agent loop per member plus
    the merge (routes/chat.py), so charging it as a single chat turn let one
    message buy several turns of model spend inside the same cap. THIS CALL is
    all-or-nothing: an over-budget one takes no slots. That is not a claim
    about a whole request — the flock path already spent one slot at the top of
    the route before it knew the flock's size, and that slot stays spent.
    """
    limit = LIMITS.get(surface)
    if limit is None:
        return
    # a caller computing cost from data (a member count) must never reach zero
    # by arithmetic accident and turn the cap into a no-op
    cost = max(1, cost)
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
        if len(window) + cost > limit:
            # _hits is a defaultdict, so the line above MATERIALIZED this key.
            # An empty window is unreachable for cost=1 (len 0 never exceeds a
            # limit of 1 or more) but reachable the moment cost > limit, and it
            # is permanent: the shed loop skips empty windows, it counts toward
            # MAX_KEYS, and the eviction below reads _hits[k][-1] on every
            # surface's key — which is an IndexError on an empty one, surfacing
            # as a 500 somewhere unrelated.
            if not window:
                del _hits[key]
            scope = PER.get(surface, "per person")
            if cost > limit:
                # unreachable today (the flock cost caps at MAX_MEMBERS + 1,
                # below every limit) — but window[need - 1] below indexes past
                # the deque the moment that stops being true, and a full
                # window of waiting never makes room for this request
                raise RateLimited(
                    f"One request cannot use {cost} {surface} slots — the limit"
                    f" is {limit} per minute {scope}. Send a smaller request.",
                    retry_after=int(WINDOW_SECONDS),
                )
            # window[need - 1] is the timestamp whose expiry first makes room
            # for `cost` slots, so the wait is exact, not a window-sized guess
            need = len(window) + cost - limit
            wait = max(1, math.ceil(WINDOW_SECONDS - (now - window[need - 1])))
            unit = "second" if wait == 1 else "seconds"
            uses = f" This request uses {cost} of them." if cost > 1 else ""
            raise RateLimited(
                f"The limit for {surface} is {limit} per minute {scope}.{uses}"
                f" Wait {wait} {unit}, then send the request again.",
                retry_after=wait,
            )
        window.extend([now] * cost)


def reset() -> None:
    with _lock:
        _hits.clear()
