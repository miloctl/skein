"""Availability ledger: PTO / on-call / focus windows. A person away for
half the week is a capacity swing the staffing math must see — capacity,
conflicts, the weekly draft, and what-if staffing all consult this table."""

import re
from datetime import date, timedelta

from .. import db

KINDS = ("pto", "oncall", "focus")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def add_absence(
    person: str,
    starts_on: str,
    ends_on: str,
    kind: str = "pto",
    note: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    from .users import resolve_teammate

    person = resolve_teammate(person, actor, "person", allow_team=False)
    if not person:
        raise ValueError("person is required")
    if len(person) > 60:
        raise ValueError("person must be under 60 characters")
    if len(note) > 200:
        raise ValueError("keep the note under 200 characters")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    for label, value in (("starts_on", starts_on), ("ends_on", ends_on)):
        if not _DATE.match(value or ""):
            raise ValueError(f"{label} must be YYYY-MM-DD")
        date.fromisoformat(value)  # rejects 2026-02-31
    if ends_on < starts_on:
        raise ValueError("ends_on must not be before starts_on")
    # an open-ended window would zero someone out of planning forever
    if (date.fromisoformat(ends_on) - date.fromisoformat(starts_on)).days > 180:
        raise ValueError("windows are capped at 180 days — enter long leave in chunks")
    aid = db.execute(
        "INSERT INTO absences (person, kind, starts_on, ends_on, note, origin,"
        " created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (person, kind, starts_on, ends_on, note, origin, actor, db.now()),
    )
    db.log_activity(actor, "add_absence", f"#{aid} {person} {kind} {starts_on}..{ends_on}")
    return {"id": aid, "person": person, "kind": kind}


def delete_absence(absence_id: int, *, actor: str = "system") -> dict:
    row = db.query_one("SELECT * FROM absences WHERE id = ?", (absence_id,))
    if not row:
        raise db.NotFound(f"no absence #{absence_id}")
    db.execute("DELETE FROM absences WHERE id = ?", (absence_id,))
    db.log_activity(
        actor,
        "delete_absence",
        f"#{absence_id} {row['person']} {row['kind']} {row['starts_on']}..{row['ends_on']}",
    )
    return {
        "id": absence_id,
        "deleted": True,
        # echo what was destroyed — a CLI caller with a transposed digit
        # must see whose window just vanished
        "person": row["person"],
        "kind": row["kind"],
        "starts_on": row["starts_on"],
        "ends_on": row["ends_on"],
    }


def list_absences(person: str = "", from_date: str = "") -> list[dict]:
    """Upcoming-and-current by default — history stays queryable via from_date."""
    cutoff = from_date or db.now()[:10]
    if person:
        return db.query(
            "SELECT * FROM absences WHERE person = ? AND ends_on >= ? ORDER BY starts_on",
            (person, cutoff),
        )
    return db.query(
        "SELECT * FROM absences WHERE ends_on >= ? ORDER BY starts_on, person", (cutoff,)
    )


def away_today(kind: str = "pto") -> dict[str, str]:
    """{person: kind} for everyone with an absence window covering today.
    Only 'pto' zeroes capacity; oncall/focus are advisory context."""
    today = db.now()[:10]
    rows = db.query(
        "SELECT person, kind FROM absences WHERE starts_on <= ? AND ends_on >= ?",
        (today, today),
    )
    out: dict[str, str] = {}
    for r in rows:
        # pto wins over advisory kinds when windows overlap
        if r["person"] not in out or (r["kind"] == kind and out[r["person"]] != kind):
            out[r["person"]] = r["kind"]
    return out


def weekday_overlap(person: str, week_monday: date) -> int:
    """Weekdays (Mon-Fri) of the given week covered by any pto absence."""
    week_days = [week_monday + timedelta(days=i) for i in range(5)]
    rows = db.query(
        "SELECT starts_on, ends_on FROM absences WHERE person = ? AND kind = 'pto'"
        " AND starts_on <= ? AND ends_on >= ?",
        (person, week_days[-1].isoformat(), week_days[0].isoformat()),
    )
    covered = set()
    for r in rows:
        for d in week_days:
            if r["starts_on"] <= d.isoformat() <= r["ends_on"]:
                covered.add(d)
    return len(covered)
