"""Team pulse: healthy game mechanics, team-scoped only. Deterministic SQL —
no leaderboards, no individual scores. Seasons are 6-week buckets so nothing
accrues forever."""

from datetime import date, datetime, timedelta, timezone

from .. import db

SEASON_EPOCH = date(2026, 1, 5)  # a Monday; seasons are 6-week buckets from here
SEASON_DAYS = 42


def _today() -> date:
    return datetime.now(timezone.utc).date()


def season() -> dict:
    days = (_today() - SEASON_EPOCH).days
    n = days // SEASON_DAYS
    start = SEASON_EPOCH + timedelta(days=n * SEASON_DAYS)
    end = start + timedelta(days=SEASON_DAYS - 1)
    return {
        "label": f"{start.year}·S{n + 1}",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days_left": (end - _today()).days,
    }


def standup_chain() -> dict:
    """Consecutive weekdays where every participating human posted a standup.
    One shared number — the team holds the chain together.

    Roster = active humans who posted at least one standup in the last 30 days.
    This keeps 'anonymous' rows, typo'd X-User names, and service accounts from
    zeroing the chain forever — you join the chain by playing."""
    cutoff = (_today() - timedelta(days=30)).isoformat()
    humans = [
        u["name"]
        for u in db.query(
            "SELECT u.name FROM users u WHERE u.kind = 'human' AND u.active = 1"
            " AND u.name != 'anonymous'"
            " AND EXISTS (SELECT 1 FROM standups s WHERE s.author = u.name"
            "             AND s.created_at >= ?)",
            (cutoff,),
        )
    ]
    if not humans:
        return {"chain": 0, "humans": 0}
    # One range scan, never a per-day substr() query: substr(created_at)
    # defeats every index, so a per-day probe full-scans standups once per
    # loop step — up to 90 scans per call. 130 days covers the 90 loop steps
    # plus the weekend rewind below; the range predicate rides
    # idx_standups_created.
    lookback = (_today() - timedelta(days=130)).isoformat()
    by_day: dict[str, set[str]] = {}
    for r in db.query(
        "SELECT substr(created_at, 1, 10) AS day, author FROM standups"
        " WHERE created_at >= ? GROUP BY 1, 2",
        (lookback,),
    ):
        by_day.setdefault(r["day"], set()).add(r["author"])
    chain = 0
    day = _today()
    if day.weekday() >= 5:
        day -= timedelta(days=day.weekday() - 4)
    for _ in range(90):
        if day.weekday() < 5:
            authors = by_day.get(day.isoformat(), set())
            if not set(humans) <= authors:
                # today doesn't break the chain until it's over
                if day == _today():
                    day -= timedelta(days=1)
                    continue
                break
            chain += 1
        day -= timedelta(days=1)
    return {"chain": chain, "humans": len(humans)}


def blocker_speedrun() -> list[dict]:
    """Average + best clear time per impact tier, this season. Raising blockers
    is scoring, not failing — only clear times are shown, never who."""
    start = season()["start"]
    rows = db.query(
        "SELECT impact,"
        " COUNT(*) AS cleared,"
        " ROUND(AVG((julianday(resolved_at) - julianday(created_at)) * 24), 1) AS avg_hours,"
        " ROUND(MIN((julianday(resolved_at) - julianday(created_at)) * 24), 1) AS best_hours"
        " FROM blockers WHERE status = 'resolved' AND resolved_at >= ?"
        " GROUP BY impact"
        " ORDER BY CASE impact WHEN 'critical' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END",
        (start,),
    )
    return rows


def pulse() -> dict:
    s = season()
    open_blockers = db.query_one("SELECT COUNT(*) AS n FROM blockers WHERE status != 'resolved'")
    spotted = db.query_one(
        "SELECT COUNT(*) AS n FROM blockers WHERE created_at >= ?", (s["start"],)
    )
    lessons = db.query_one("SELECT COUNT(*) AS n FROM lessons WHERE created_at >= ?", (s["start"],))
    shipped = db.query_one(
        "SELECT COUNT(*) AS n FROM engagements WHERE status = 'closed' AND closed_at >= ?",
        (s["start"],),
    )
    return {
        "season": s,
        "standup_chain": standup_chain(),
        "blocker_speedrun": blocker_speedrun(),
        "season_totals": {
            "blockers_spotted": spotted["n"] if spotted else 0,
            "blockers_open": open_blockers["n"] if open_blockers else 0,
            "lessons_recorded": lessons["n"] if lessons else 0,
            "engagements_shipped": shipped["n"] if shipped else 0,
        },
    }
