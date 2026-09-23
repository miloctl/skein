"""Availability ledger: PTO / on-call / focus windows. A person away for
half the week is a capacity swing the staffing math must see — capacity,
conflicts, the weekly draft, and what-if staffing all consult this table."""

import re
from datetime import date, timedelta

from .. import db
from . import scope

KINDS = ("pto", "oncall", "focus")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# A window counts for the team (capacity, planning, the weekly draft,
# staffing what-ifs) only when the person away shared at least its dates: a
# tier wider than private, or a private row with dates_shared. Every one of
# those readers takes this fragment, so an "only me" window moves nobody's
# plan. The kind and the note stay the row's tier's to show.
TEAM_SEES_DATES = "(visibility <> 'private' OR dates_shared)"


def add_absence(
    person: str,
    starts_on: str,
    ends_on: str,
    kind: str = "pto",
    note: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
    visibility: str | None = None,
    crew_id: int = 0,
    dates_shared: bool = False,
    requester: str = "",
) -> dict:
    """`requester` is the person an agent filed this for. The review sets it
    from the proposal's requested_by, never from the payload, and it lets that
    person's own window be private.

    No `visibility` means the narrowest tier the filer can pick: only the
    person away for their own window, everyone on the roster for a
    teammate's (a private window about somebody else is refused below)."""
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
    if visibility is None:
        visibility = scope.PRIVATE if person == (requester or actor) else scope.WORKSPACE
    with db.transaction():
        tier, crew = scope.resolve_write(visibility, crew_id, actor=actor)
        # CLASSIFIED keys absences on `person`, not on the filer. Without this,
        # a filer could scope a colleague's window to a tier where NOBODY can
        # read it — not the filer (wrong author column) and not the subject —
        # while it still moves that person's capacity.
        scope.assert_readable_by(tier, crew, person, label="person", author=requester or actor)
        aid = db.execute(
            "INSERT INTO absences (person, kind, starts_on, ends_on, note, origin,"
            " created_by, created_at, visibility, crew_id, dates_shared)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " RETURNING id",
            (
                person,
                kind,
                starts_on,
                ends_on,
                note,
                origin,
                actor,
                db.now(),
                tier,
                crew,
                bool(dates_shared) or tier != scope.PRIVATE,
            ),
        )
        db.log_activity(
            actor,
            "add_absence",
            scope.detail(tier, f"#{aid}", f"{person} {kind} {starts_on}..{ends_on}"),
        )
    return {"id": aid, "person": person, "kind": kind}


def share_absence(absence_id: int, team_sees: str, *, actor: str) -> dict:
    """Widen one window: "dates" lets the team plan around it, "details" makes
    it visible to everyone on the roster. Only the person away can, and never
    narrower: capacity and planning already read a shared window."""
    if team_sees not in ("dates", "details"):
        raise ValueError('team_sees must be "dates" or "details"')
    with db.transaction():
        row = db.query_one(
            "SELECT person, visibility, dates_shared FROM absences WHERE id = ? FOR UPDATE",
            (absence_id,),
        )
        if not row or row["person"] != actor:
            raise scope.missing("absences", absence_id)
        if row["visibility"] == scope.WORKSPACE or (team_sees == "dates" and row["dates_shared"]):
            raise ValueError("The team already sees this.")
        if team_sees == "dates":
            db.execute("UPDATE absences SET dates_shared = TRUE WHERE id = ?", (absence_id,))
        else:
            db.execute(
                "UPDATE absences SET visibility = ?, crew_id = NULL, dates_shared = TRUE"
                " WHERE id = ?",
                (scope.WORKSPACE, absence_id),
            )
        db.log_activity(actor, "share_absence", f"#{absence_id} {team_sees}")
    return {"id": absence_id, "team_sees": team_sees}


def delete_absence(absence_id: int, *, actor: str = "system") -> dict:
    row = db.query_one("SELECT * FROM absences WHERE id = ?", (absence_id,))
    if not row:
        raise scope.missing("absences", absence_id)
    scope.assert_editable("absences", row, actor, verb="delete")
    db.execute("DELETE FROM absences WHERE id = ?", (absence_id,))
    # the id only: who was away, why and when is gone with the row
    db.log_activity(actor, "delete_absence", f"#{absence_id}")
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


def list_absences(
    person: str = "", from_date: str = "", viewer: scope.Viewer = scope.NOBODY
) -> list[dict]:
    """Upcoming-and-current by default — history stays queryable via from_date."""
    cutoff = from_date or db.today().isoformat()  # vs ends_on, a date column
    frag, vp = scope.visible_filter(viewer, "absences")
    if person:
        return db.query(
            f"SELECT * FROM absences WHERE person = ? AND ends_on >= ? AND {frag}"  # noqa: S608 — scope.visible_filter emits only bound marks
            " ORDER BY starts_on",
            (person, cutoff, *vp),
        )
    return db.query(
        f"SELECT * FROM absences WHERE ends_on >= ? AND {frag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " ORDER BY starts_on, person",
        (cutoff, *vp),
    )


def away_today(kind: str = "pto") -> dict[str, str]:
    """{person: kind} for everyone with an absence window covering today.
    Only 'pto' zeroes capacity; oncall/focus are advisory context."""
    # TEAM_SEES_DATES, not the viewer's tier filter, and the same for
    # weekday_overlap. These two feed capacity and the weekly draft, and a
    # planner that cannot see a window the person away shared staffs them
    # anyway. An "only me" window is left out on purpose: that person chose
    # to have no team effect.
    #
    # The KIND is a different question and it IS hidden. engagements.capacity
    # puts this value on /api/capacity for the whole roster, so a private
    # `focus` or `oncall` window announced itself by name. Scoped rows report
    # "away": unavailability is the honest core, the reason is not. The
    # precedence below compares against the REAL kind on the left, so a
    # private PTO day still outranks an advisory one and the capacity math
    # does not move. The right-hand comparand is the MASKED value already
    # stored — correct in every ordering, but a new sentinel that collides
    # with a real kind would break it.
    today = db.today().isoformat()  # vs starts_on/ends_on, date columns
    rows = db.query(
        "SELECT person, kind, visibility FROM absences"  # noqa: S608 — TEAM_SEES_DATES is a module constant
        f" WHERE starts_on <= ? AND ends_on >= ? AND {TEAM_SEES_DATES}",
        (today, today),
    )
    out: dict[str, str] = {}
    for r in rows:
        shown = r["kind"] if r["visibility"] == scope.WORKSPACE else "away"
        # pto wins over advisory kinds when windows overlap
        if r["person"] not in out or (r["kind"] == kind and out[r["person"]] != kind):
            out[r["person"]] = shown
    return out


def weekday_overlap(person: str, week_monday: date) -> int:
    """Weekdays (Mon-Fri) of the given week covered by any pto absence."""
    week_days = [week_monday + timedelta(days=i) for i in range(5)]
    rows = db.query(
        "SELECT starts_on, ends_on FROM absences WHERE person = ? AND kind = 'pto'"  # noqa: S608 — TEAM_SEES_DATES is a module constant
        f" AND starts_on <= ? AND ends_on >= ? AND {TEAM_SEES_DATES}",
        (person, week_days[-1].isoformat(), week_days[0].isoformat()),
    )
    covered = set()
    for r in rows:
        for d in week_days:
            if r["starts_on"] <= d.isoformat() <= r["ends_on"]:
                covered.add(d)
    return len(covered)
