"""Adoption telemetry: is the TOOL being used, by whom, through which surface.
Team-scoped by design — this measures the platform's reach, not people's
output. One row per (day, user, surface); counts only, no content."""

import contextlib
import time
from datetime import timedelta
from threading import Lock

from .. import db

SURFACES = ("web", "cli", "chat", "slack", "mcp", "webhook", "api")

# Buffered, not written per call: every authenticated request lands here, and
# a per-call upsert costs a round trip on the hot path. The buffer
# drains on the first record_use after FLUSH_SECONDS, when adoption() reads,
# and at app shutdown — on an idle process the tail sits buffered until one
# of those. Counts buffered at a crash, and a batch whose write fails, are
# lost — accepted, these rows are reach counters, not an audit record.
# Process-local like ratelimit; conftest zeroes FLUSH_SECONDS and clears the
# buffer between tests.
FLUSH_SECONDS = 30.0
_pending: dict[tuple[str, str, str], int] = {}
_pending_lock = Lock()
# -inf, never 0.0: time.monotonic()'s reference point is arbitrary (boot on
# Linux), so 0.0 reads as "flushed at boot" — on a host up for less than
# FLUSH_SECONDS the first record_use buffers instead of flushing, which is
# what test_record_use_buffers_between_flushes pins. -inf means "never
# flushed" at any uptime and for any FLUSH_SECONDS.
_last_flush = float("-inf")


def _write(batch: dict[tuple[str, str, str], int]) -> None:
    # suppression per row, not around the loop: one failing upsert must not
    # drop the rest of the batch with it.
    #
    # db.savepoint() is what makes that suppression SAFE. This runs inside the
    # request's transaction (extensions/fastapi.py wraps mutating routes), and
    # in PostgreSQL a failed statement aborts the whole transaction — so a
    # swallowed error here left every later statement in the request failing
    # with InFailedSqlTransaction, and telemetry took down the write it was
    # only supposed to count. Rolling back to the savepoint discards the failed
    # statement and nothing else.
    for (day, user, surface), n in batch.items():
        with contextlib.suppress(Exception), db.savepoint():
            db.execute(
                # tool_usage.actions, not a bare `actions`: inside DO UPDATE
                # the bare name is ambiguous between the target row and
                # `excluded`, and PostgreSQL refuses it.
                'INSERT INTO tool_usage (day, "user", surface, actions) VALUES (?, ?, ?, ?)'
                ' ON CONFLICT (day, "user", surface)'
                " DO UPDATE SET actions = tool_usage.actions + excluded.actions",
                (day, user, surface, n),
            )


def record_use(user: str, surface: str, *, counts: bool = True) -> None:
    """Fire-and-forget count. Must never break the request it rides on.

    counts=False records that the person was HERE without adding to the
    action tally: the row is created or touched at +0, so
    weekly_active_users still sees them and by_surface does not. The web UI
    fans one page out into eight or more reads while a CLI invocation is one
    request, so counting reads made non_web_share — the >50% success bar in
    adoption() below — a measure of how many cards a page renders. Every
    read resolves a caller now (tests/test_route_identity.py), which took
    that from a lean to a landslide.
    """
    global _last_flush
    if not user or user == "anonymous":
        return
    surface = surface if surface in SURFACES else "api"
    day = db.today().isoformat()
    with _pending_lock:
        key = (day, user, surface)
        _pending[key] = _pending.get(key, 0) + (1 if counts else 0)
        if time.monotonic() - _last_flush < FLUSH_SECONDS:
            return
        batch = dict(_pending)
        _pending.clear()
        _last_flush = time.monotonic()
    _write(batch)


def reset() -> None:
    """Drop buffered counts and re-arm the flush clock (conftest — a count
    buffered against one test's database must not land in the next test's)."""
    global _last_flush
    with _pending_lock:
        _pending.clear()
        _last_flush = float("-inf")


def flush() -> None:
    """Write buffered counts now (adoption() reads, app shutdown)."""
    global _last_flush
    with _pending_lock:
        if not _pending:
            return
        batch = dict(_pending)
        _pending.clear()
        _last_flush = time.monotonic()
    _write(batch)


def adoption(weeks: int = 4) -> dict:
    """Who touched the platform, how recently, through what. The success bar
    (docs/INSIGHTS.md): >50% of actions originate outside the web UI."""
    flush()  # buffered counts belong in the numbers this reports
    weeks = max(1, min(int(weeks), 520))
    cutoff = (db.today() - timedelta(weeks=weeks)).isoformat()
    week_ago = (db.today() - timedelta(days=7)).isoformat()
    humans = db.query(
        "SELECT name FROM users WHERE kind = 'human' AND active = 1 AND name != 'anonymous'"
    )
    # NO per-person list here. A per-person action tally over a past window is
    # the leaderboard input the anti-surveillance rule refuses (docs/INSIGHTS.md:
    # person-level data plans the future, only team aggregates judge the past),
    # and this payload is served raw at GET /api/adoption. weekly_active_users
    # below is the team COUNT, computed on its own — the reach number without
    # the names.
    by_surface = db.query(
        'SELECT surface, COUNT(DISTINCT "user") AS users, SUM(actions) AS actions'
        " FROM tool_usage WHERE day >= ? GROUP BY surface ORDER BY actions DESC",
        (cutoff,),
    )
    weekly_active = db.query_row(
        'SELECT COUNT(DISTINCT t."user") AS n FROM tool_usage t'
        ' JOIN users u ON u.name = t."user" AND u.active = 1 WHERE t.day >= ?',
        (week_ago,),
    )
    capture_total = db.query_row(
        "SELECT COUNT(*) AS n FROM activity WHERE action = 'capture' AND created_at >= ?", (cutoff,)
    )
    # non-web = everything that isn't the web UI — keyed API automation
    # (git hooks, scripts, webhooks) counts toward the automation bar
    non_web = db.query_row(
        "SELECT SUM(actions) AS n FROM tool_usage WHERE day >= ? AND surface != 'web'", (cutoff,)
    )
    total = db.query_row("SELECT SUM(actions) AS n FROM tool_usage WHERE day >= ?", (cutoff,))
    return {
        "window_weeks": weeks,
        "team_humans": len(humans),
        "weekly_active_users": weekly_active["n"],
        "by_surface": by_surface,
        "non_web_share": round((non_web["n"] or 0) / total["n"], 2) if total["n"] else None,
        "captures_in_window": capture_total["n"],
    }


def snapshot_health() -> dict:
    """Daily: record each engagement's R/Y/G so the readout can say which way
    it is MOVING. Idempotent per (day, engagement) through the unique index.

    Shares this module with snapshot_forecasts for the same reason: both are
    "write today's derived state down so tomorrow can compare", and both are
    read by services/insights.py and services/readout.py rather than here."""
    from .portfolio import engagement_health

    day = db.today().isoformat()
    n = 0
    # name_assignees=False: nothing here stores a receipt, but the flag keeps
    # this caller honest if that ever changes — a snapshot is history, and
    # history is the direction the anti-surveillance rule refuses
    for h in engagement_health(name_assignees=False):
        db.execute(
            "INSERT INTO health_snapshots"
            " (day, engagement_id, health, status, created_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT DO NOTHING",
            (day, h["id"], h["health"], h.get("status", ""), db.now()),
        )
        n += 1
    return {"snapshotted": n}


def snapshot_forecasts() -> dict:
    """Daily: record today's slip forecasts so calibration can be measured
    against actuals later. Idempotent per (day, milestone)."""
    from .portfolio import slip_forecast

    day = db.today().isoformat()
    n = 0
    for f in slip_forecast()["forecasts"]:
        try:
            db.execute(
                "INSERT INTO forecast_snapshots"
                " (day, milestone_id, due_date, forecast_date, created_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT DO NOTHING",
                (day, f["milestone_id"], f["due_date"], f["forecast_date"], db.now()),
            )
            n += 1
        except Exception:
            pass
    return {"snapshotted": n}
