"""Adoption telemetry: is the TOOL being used, by whom, through which surface.
Team-scoped by design — this measures the platform's reach, not people's
output. One row per (day, user, surface); counts only, no content."""

import contextlib
import time
from datetime import datetime, timedelta, timezone
from threading import Lock

from .. import db

SURFACES = ("web", "cli", "chat", "slack", "mcp", "webhook", "api")

# Buffered, not written per call: every authenticated request lands here, and
# a per-call upsert takes SQLite's single write lock on the hot path. The buffer
# drains on the first record_use after FLUSH_SECONDS, when adoption() reads,
# and at app shutdown — on an idle process the tail sits buffered until one
# of those. Counts buffered at a crash, and a batch whose write fails, are
# lost — accepted, these rows are reach counters, not an audit record.
# Process-local like ratelimit; conftest zeroes FLUSH_SECONDS and clears the
# buffer between tests.
FLUSH_SECONDS = 30.0
_pending: dict[tuple[str, str, str], int] = {}
_pending_lock = Lock()
_last_flush = 0.0


def _write(batch: dict[tuple[str, str, str], int]) -> None:
    # suppression per row, not around the loop: one failing upsert must not
    # drop the rest of the batch with it
    for (day, user, surface), n in batch.items():
        with contextlib.suppress(Exception):
            db.execute(
                "INSERT INTO tool_usage (day, user, surface, actions) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (day, user, surface)"
                " DO UPDATE SET actions = actions + excluded.actions",
                (day, user, surface, n),
            )


def record_use(user: str, surface: str) -> None:
    """Fire-and-forget count; must never break the request it rides on."""
    global _last_flush
    if not user or user == "anonymous":
        return
    surface = surface if surface in SURFACES else "api"
    day = datetime.now(timezone.utc).date().isoformat()
    with _pending_lock:
        key = (day, user, surface)
        _pending[key] = _pending.get(key, 0) + 1
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
        _last_flush = 0.0


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
    from the DX panel: >50% of actions should originate outside the web UI."""
    flush()  # buffered counts belong in the numbers this reports
    weeks = max(1, min(int(weeks), 520))
    cutoff = (datetime.now(timezone.utc).date() - timedelta(weeks=weeks)).isoformat()
    week_ago = (datetime.now(timezone.utc).date() - timedelta(days=7)).isoformat()
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
        "SELECT surface, COUNT(DISTINCT user) AS users, SUM(actions) AS actions"
        " FROM tool_usage WHERE day >= ? GROUP BY surface ORDER BY actions DESC",
        (cutoff,),
    )
    weekly_active = db.query_row(
        "SELECT COUNT(DISTINCT t.user) AS n FROM tool_usage t"
        " JOIN users u ON u.name = t.user AND u.active = 1 WHERE t.day >= ?",
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


def snapshot_forecasts() -> dict:
    """Daily: record today's slip forecasts so calibration can be measured
    against actuals later. Idempotent per (day, milestone)."""
    from .portfolio import slip_forecast

    day = datetime.now(timezone.utc).date().isoformat()
    n = 0
    for f in slip_forecast()["forecasts"]:
        try:
            db.execute(
                "INSERT OR IGNORE INTO forecast_snapshots"
                " (day, milestone_id, due_date, forecast_date, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (day, f["milestone_id"], f["due_date"], f["forecast_date"], db.now()),
            )
            n += 1
        except Exception:
            pass
    return {"snapshotted": n}
