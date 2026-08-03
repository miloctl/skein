"""Adoption telemetry: is the TOOL being used, by whom, through which surface.
Team-scoped by design — this measures the platform's reach, not people's
output. One row per (day, user, surface); counts only, no content."""

import contextlib
from datetime import datetime, timedelta, timezone

from .. import db

SURFACES = ("web", "cli", "chat", "slack", "mcp", "webhook", "api")


def record_use(user: str, surface: str) -> None:
    """Fire-and-forget upsert; must never break the request it rides on."""
    if not user or user == "anonymous":
        return
    surface = surface if surface in SURFACES else "api"
    day = datetime.now(timezone.utc).date().isoformat()
    with contextlib.suppress(Exception):
        db.execute(
            "INSERT INTO tool_usage (day, user, surface, actions) VALUES (?, ?, ?, 1)"
            " ON CONFLICT (day, user, surface) DO UPDATE SET actions = actions + 1",
            (day, user, surface),
        )


def adoption(weeks: int = 4) -> dict:
    """Who touched the platform, how recently, through what. The success bar
    from the DX panel: >50% of actions should originate outside the web UI."""
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
