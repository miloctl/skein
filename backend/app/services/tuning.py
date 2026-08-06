"""Operator-tunable runtime numbers, held in app_settings.

Same contract as services/settings.py, generalized to a registry: env stays
the DEFAULT so a fresh deployment behaves per its .env, a stored value
overrides it, and clearing returns to the env rather than to a hardcoded
guess. The difference is that these are numbers with bounds, so every write
is range-checked and some pairs are checked against each other.

READ THROUGH, never cache. effective() hits SQLite on every call — a
sub-millisecond indexed lookup by primary key. A module-level cache would be
faster and wrong: the moment this app runs more than one worker, each process
would hold its own copy and admins would see a change that only some requests
obey. routes/chat.py::_inflight is the existing example of state that is
correct only in one process, and it is the thing blocking a second worker.

WHAT IS DELIBERATELY ABSENT, so a later reader does not read it as an
oversight: SKEIN_AUTH_MODE, SKEIN_TRUST_PROXY_HOPS, SKEIN_ADMINS, and every
model-provider and credential setting. Letting a web surface change how
identity is resolved, or who counts as an administrator, is privilege
escalation with extra steps — an admin who can lower the bar can let
themselves through it. Those stay env-only, set by whoever runs the server.
"""

from collections.abc import Callable
from dataclasses import dataclass

from .. import config, db

PREFIX = "tuning_"


@dataclass(frozen=True)
class Tunable:
    """One knob. `floor`/`ceiling` are INCLUSIVE and always finite: an
    unbounded knob is one an admin can use to take the deployment down, and
    the floor matters as much as the ceiling (a 0 rate limit refuses
    everyone, a 1-thread pool deadlocks the first tool that waits on the
    pool). `live` is False when the value is read once at startup — the UI
    must say so rather than imply a change took effect."""

    name: str
    label: str
    # a CALLABLE, never a value: config is reloaded by the suite and by
    # test_model_providers.py, so a default captured at import time would go
    # stale against the module it is supposed to mirror
    default: Callable[[], float]
    floor: int
    ceiling: int
    unit: str
    live: bool
    detail: str


# The registry. `default` is a callable so it reads the CURRENT config value:
# config is re-imported by tests and reloaded in test_model_providers.py, and
# a value captured at import time would go stale against it.
TUNABLES: tuple[Tunable, ...] = (
    Tunable(
        "chat_limit",
        "Chat messages",
        lambda: _limit("chat"),
        1,
        500,
        "per person per minute",
        True,
        "One flock turn spends one slot per member, so a 3-member flock costs 3.",
    ),
    Tunable(
        "write_limit",
        "Writes",
        lambda: _limit("write"),
        1,
        1000,
        "per person per minute",
        True,
        "Covers REST creates and agent tool writes. An agent turn usually writes 1 to 3 times.",
    ),
    Tunable(
        "capture_limit",
        "Captures",
        lambda: _limit("capture"),
        1,
        1000,
        "per person per minute",
        True,
        "Quick capture from the palette, chat, Slack and MCP.",
    ),
    Tunable(
        "member_timeout_s",
        "Flock member deadline",
        lambda: _member_timeout(),
        10,
        900,
        "seconds",
        True,
        "How long one flock member, and the merge after it, can take."
        " If you raise this deadline, raise the model socket timeout first.",
    ),
    Tunable(
        "read_timeout_s",
        "Model socket timeout",
        lambda: _read_timeout(),
        10,
        3600,
        "seconds",
        True,
        "Ends a provider connection that goes silent. This timeout must stay"
        " greater than the flock member deadline, so the deadline is the one"
        " that fires.",
    ),
    Tunable(
        "thread_pool",
        "Request thread pool",
        lambda: config.THREAD_POOL,
        2,
        256,
        "threads",
        False,
        "Serves every REST handler. A smaller pool is faster until requests"
        " queue on work that blocks.",
    ),
    Tunable(
        "tool_threads",
        "Agent tool thread pool",
        lambda: config.TOOL_THREADS,
        2,
        256,
        "threads",
        False,
        "Serves agent tool calls.",
    ),
)

BY_NAME = {t.name: t for t in TUNABLES}


def _limit(surface: str) -> int:
    from .. import ratelimit

    return ratelimit.LIMITS[surface]


def _member_timeout() -> float:
    from ..routes import chat

    return chat.MEMBER_TIMEOUT_S


def _read_timeout() -> float:
    from ..agents import team_agent

    return team_agent.READ_TIMEOUT_S


def default_of(name: str) -> int:
    """The env/code default, read fresh and floored to a whole unit."""
    return int(BY_NAME[name].default())


def _overrides() -> dict[str, int]:
    # A RANGE, not LIKE: `_` is LIKE's single-character wildcard, so
    # `key LIKE 'tuning_%'` also matched `tuningXchat_limit` — and the slice
    # below then read it as the knob `chat_limit`, letting a foreign key in
    # this shared table drive a live rate limit. The range is also an index
    # scan on the primary key instead of the full table scan LIKE forced,
    # which matters because this runs per rate-limit check.
    rows = db.query(
        "SELECT key, value FROM app_settings WHERE key >= ? AND key < ?",
        (PREFIX, PREFIX[:-1] + chr(ord(PREFIX[-1]) + 1)),
    )
    out: dict[str, int] = {}
    for r in rows:
        name = r["key"][len(PREFIX) :]
        if name not in BY_NAME:
            continue  # a knob retired between releases: ignore, never guess
        try:
            out[name] = int(r["value"])
        except (TypeError, ValueError):
            continue
    return out


def override_of(name: str) -> int | None:
    """The stored override, or None when the knob is at its default.

    This, not effective(), is what a hot call site asks: it answers "did an
    administrator change this" without computing the default, so the code
    constant stays the single source of the default — which is also what
    keeps monkeypatching that constant working in the suite.
    """
    knob = BY_NAME.get(name)
    if knob is None:
        return None
    got = _overrides().get(name)
    if got is None or not (knob.floor <= got <= knob.ceiling):
        return None
    return got


def effective(name: str) -> int:
    """The value a call site must use. Read through on every call — see the
    module docstring on why this is not cached."""
    got = _overrides().get(name)
    if got is None:
        return default_of(name)
    knob = BY_NAME[name]
    # A stored value outside the current bounds is IGNORED rather than
    # clamped: bounds move between releases, and silently running at a
    # number nobody chose is how an operator ends up debugging a setting the
    # UI says is in effect. Falling back to the default is visible in list().
    return got if knob.floor <= got <= knob.ceiling else default_of(name)


def _check_pairs(name: str, value: int, current: dict[str, int]) -> None:
    """Cross-knob invariants, refused at write time. The suite pins
    READ_TIMEOUT_S > MEMBER_TIMEOUT_S (tests/test_model_providers.py); without
    this an admin inverts it from a form and every cold model load starts
    dying as a failed member instead of finishing."""
    proposed = {**current, name: value}
    if proposed["read_timeout_s"] <= proposed["member_timeout_s"]:
        # names the STORED numbers, never the submitted one: a refusal must
        # not echo the rejected value back (CLAUDE.md)
        raise ValueError(
            "The model socket timeout must be greater than the flock member"
            f" deadline. The socket timeout is {current['read_timeout_s']} seconds"
            f" and the member deadline is {current['member_timeout_s']} seconds."
            " Raise the socket timeout, or lower the member deadline first."
        )


def list_tunables() -> list[dict]:
    """Every knob with its effective value, its default, and its bounds."""
    overrides = _overrides()
    out = []
    for knob in TUNABLES:
        stored = overrides.get(knob.name)
        out.append(
            {
                "name": knob.name,
                "label": knob.label,
                "value": effective(knob.name),
                "default": default_of(knob.name),
                "override": stored,
                "floor": knob.floor,
                "ceiling": knob.ceiling,
                "unit": knob.unit,
                "live": knob.live,
                "detail": knob.detail,
                # an override the bounds no longer admit is reported, not
                # hidden: effective() is already ignoring it
                "ignored": stored is not None and not (knob.floor <= stored <= knob.ceiling),
            }
        )
    return out


def set_tunable(name: str, value: int | None, *, actor: str) -> dict:
    """None clears the override and returns the knob to its env default."""
    knob = BY_NAME.get(name)
    if knob is None:
        # never echo the rejected name back — it is caller-supplied
        raise ValueError(f"unknown setting — expected one of: {', '.join(sorted(BY_NAME))}")
    if value is None:
        # CHECKED like any other write. Clearing moves the value just as a set
        # does — to the default — so an unchecked clear was a way around the
        # pair rule: raise the socket timeout, raise the deadline under it,
        # then clear the socket timeout and the pair lands inverted.
        _check_pairs(name, default_of(name), {t.name: effective(t.name) for t in TUNABLES})
        db.execute("DELETE FROM app_settings WHERE key = ?", (f"{PREFIX}{name}",))
        db.log_activity(actor, "set_tuning", f"{knob.label} returned to the server default")
        return {"name": name, "value": effective(name), "override": None}
    value = int(value)
    if not (knob.floor <= value <= knob.ceiling):
        raise ValueError(
            f"{knob.label} must be between {knob.floor} and {knob.ceiling}"
            f" {knob.unit}. Send a number in that range."
        )
    current = {t.name: effective(t.name) for t in TUNABLES}
    _check_pairs(name, value, current)
    db.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at",
        (f"{PREFIX}{name}", str(value), db.now()),
    )
    db.log_activity(actor, "set_tuning", f"{knob.label} set to {value} {knob.unit}")
    return {"name": name, "value": effective(name), "override": value}
