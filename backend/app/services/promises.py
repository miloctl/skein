"""The promise ledger: promises made to people outside the team.
The exec readout reads from here — a promise that isn't recorded is a
promise the team can't keep on purpose."""

from datetime import UTC, datetime, timedelta

from .. import db
from . import scope
from .search import index_record

STATUSES = ("open", "kept", "missed", "withdrawn")
# 'external': promises to people outside the team (exec readout material).
# 'team': the manager's own promises TO the team — visible so they get kept.
AUDIENCES = ("external", "team")
# who owes whom. `to_whom` is the OTHER PARTY either way (migration 007).
DIRECTIONS = ("given", "received")


def add_promise(
    promise: str,
    to_whom: str = "",
    due_date: str = "",
    engagement_id: int = 0,
    audience: str = "external",
    direction: str = "given",
    *,
    actor: str = "system",
    origin: str = "human",
    visibility: str = scope.WORKSPACE,
    crew_id: int = 0,
) -> dict:
    db.validate_date("due_date", due_date, allow_clear=False)
    if not promise.strip():
        raise ValueError("the promise text is required")
    if audience not in AUDIENCES:
        raise ValueError(f"audience must be one of {AUDIENCES}")
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}")
    efrag, ep = scope.visible_filter(scope.Viewer.for_actor(actor), "engagements")
    if engagement_id and not db.query_one(
        f"SELECT id FROM engagements WHERE id = ? AND {efrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (engagement_id, *ep),
    ):
        raise ValueError(scope.missing_text("engagements", engagement_id))
    ts = db.now()
    with db.transaction():
        tier, crew = scope.resolve_write(visibility, crew_id, actor=actor)
        # NO assert_readable_by on to_whom: unlike an assignee or an owner,
        # a promise recipient is deliberately not a roster name. The default
        # audience is `external` and to_whom holds "the board" or a customer,
        # so checking it against crew membership refuses the ordinary case.
        cid = db.execute(
            "INSERT INTO promises (promise, to_whom, engagement_id, due_date, audience,"
            " direction, origin, created_by, created_at, updated_at, visibility, crew_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                promise,
                to_whom,
                engagement_id or None,
                due_date or None,
                audience,
                direction,
                origin,
                actor,
                ts,
                ts,
                tier,
                crew,
            ),
        )
        # the literal in each branch, not a variable: the activity registry is
        # checked against the actions the code actually logs by reading for
        # `log_activity(actor, "…"` (tests/test_activity_feed.py), and an
        # indirection there hides BOTH verbs from that scan.
        detail = scope.detail(tier, f"#{cid}", promise[:80])
        if direction == "received":
            db.log_activity(actor, "await_promise", detail)
        else:
            db.log_activity(actor, "add_promise", detail)
        index_record("promise", cid, promise[:120], f"{promise} {to_whom}")
    return {"id": cid, "promise": promise, "status": "open", "direction": direction}


def update_promise(
    promise_id: int, status: str, *, actor: str = "system", origin: str = "human"
) -> dict:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    row = db.query_one("SELECT * FROM promises WHERE id = ?", (promise_id,))
    if not row:
        raise scope.missing("promises", promise_id)
    scope.assert_editable("promises", row, actor, verb="settle")
    if row["status"] != "open":
        raise ValueError(f"promise #{promise_id} already {row['status']}")
    db.execute(
        "UPDATE promises SET status = ?, updated_at = ? WHERE id = ?",
        (status, db.now(), promise_id),
    )
    db.log_activity(actor, "update_promise", f"#{promise_id} {status}")
    return {"id": promise_id, "status": status}


def edit_promise(
    promise_id: int,
    promise: str = "",
    due_date: str = "",
    to_whom: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    """Correct the wording/date of an OPEN promise — old→new logged; settled
    promises stay as history."""
    db.validate_date("due_date", due_date)
    row = db.query_one("SELECT * FROM promises WHERE id = ?", (promise_id,))
    if not row:
        raise scope.missing("promises", promise_id)
    scope.assert_editable("promises", row, actor, verb="edit")
    if row["status"] != "open":
        raise ValueError(f"promise #{promise_id} is {row['status']} — history stays put")
    fields = {
        k: v for k, v in [("promise", promise), ("due_date", due_date), ("to_whom", to_whom)] if v
    }
    if not fields:
        raise ValueError("nothing to update")
    if fields.get("due_date") == "-":
        fields["due_date"] = None  # type: ignore[assignment]
    if fields.get("to_whom") == "-":
        fields["to_whom"] = ""
    sets = ", ".join(f"{k} = ?" for k in fields)
    # A new due date renegotiates the promise, so the chase starts over. Kept
    # here rather than in the chaser: without it a promise moved out by a week
    # carries its old nudge_count, and the first chase after the NEW date
    # passes lands on ESCALATE_AFTER_CYCLES and goes straight to the team.
    reset = ", nudge_count = 0, last_nudged_at = NULL" if "due_date" in fields else ""
    db.execute(
        f"UPDATE promises SET {sets}{reset}, updated_at = ? WHERE id = ?",  # noqa: S608 — keys hardcoded
        (*fields.values(), db.now(), promise_id),
    )
    if promise and promise != row["promise"]:
        # both strings are the promise's own text, so a scoped rewording logs
        # the identifier only (services/scope.py::detail)
        db.log_activity(
            actor,
            "edit_promise",
            scope.detail(row["visibility"], f"#{promise_id}", f"'{row['promise']}' -> '{promise}'"),
        )
    else:
        db.log_activity(actor, "edit_promise", f"#{promise_id} {' '.join(fields)}")
    return {"id": promise_id, "updated": list(fields)}


def list_promises(
    status: str = "",
    audience: str = "",
    viewer: scope.Viewer = scope.NOBODY,
    direction: str = "",
) -> list[dict]:
    frag, vp = scope.visible_filter(viewer, "promises")
    where, params = [frag], list(vp)
    if status:
        where.append("status = ?")
        params.append(status)
    if audience:
        where.append("audience = ?")
        params.append(audience)
    if direction:
        where.append("direction = ?")
        params.append(direction)
    return db.query(
        f"SELECT * FROM promises WHERE {' AND '.join(where)}"  # noqa: S608 — clauses hardcoded, and scope.visible_filter emits only bound marks
        " ORDER BY status != 'open', due_date IS NULL, due_date, id DESC LIMIT 100",
        tuple(params),
    )


# The chaser's cadence. A received promise that is overdue gets one nudge per
# CYCLE, not per hourly run — the job fires every hour and a nudge every hour
# is a nudge nobody reads. Two silent cycles is the point where the person
# waiting has chased twice and nothing moved, which is when it stops being
# their problem alone.
NUDGE_CYCLE_HOURS = 24
ESCALATE_AFTER_CYCLES = 2


def _is_agent(name: str) -> bool:
    row = db.query_one("SELECT kind FROM users WHERE name = ?", (name,))
    return bool(row and row["kind"] == "agent")


def chase_received(*, actor: str = "scheduler") -> dict:
    """Nudge the person waiting on an overdue received promise, and escalate
    to the team when two cycles have passed with no settlement.

    The escalation is the whole point. A promise made TO the team goes quiet
    in exactly one way — nobody chases it — and the person waiting is usually
    the person least able to escalate it. After two silent cycles the fact
    becomes team-visible whether or not they raise it.
    """
    from .notifications import notify

    now = datetime.now(UTC)
    today = db.today().isoformat()
    nudged, escalated = [], []
    # No viewer filter, deliberately: a job has no viewer, and this reads
    # every tier so a crew-scoped promise is still chased for the person who
    # recorded it. What LEAVES is the guard — see the escalation below.
    rows = db.query(
        "SELECT * FROM promises WHERE direction = 'received' AND status = 'open'"
        " AND due_date IS NOT NULL AND due_date < ?",
        (today,),
    )
    for row in rows:
        last = row["last_nudged_at"]
        if last:
            seen = datetime.fromisoformat(last)
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=UTC)
            if now - seen < timedelta(hours=NUDGE_CYCLE_HOURS):
                continue  # already chased this cycle
        cycles = int(row["nudge_count"] or 0) + 1
        who = row["to_whom"] or "the other party"
        # The recorder is the person waiting, and the only one who can chase
        # it. An agent identity cannot: it reads no notifications and cannot
        # send an email, so the nudge would land nowhere (the same reason
        # rituals.py::week_open routes an agent-recorded promise to the team).
        target = row["created_by"] or "team"
        if _is_agent(target):
            target = "team"
        notify(
            target,
            f"Still open with {who}: “{row['promise'][:80]}” was due {row['due_date']}.",
            tier="digest",
            link="/planning",
        )
        nudged.append(row["id"])
        # The team-wide escalation NAMES NOBODY. `to_whom` is free text and
        # nothing stops it being a teammate, so quoting it here would publish
        # a named person's missed past commitment to every viewer — the exact
        # thing services/forge.py refuses when it declines to name a pusher.
        # The tier check stands beside it: a crew or private promise must not
        # reach the whole roster at all, in any wording.
        #
        # Once, not daily. `==`, not `>=`: the recorder keeps being nudged,
        # but a team-wide message repeating the same promise every 24 hours
        # forever is how a digest gets muted.
        if cycles == ESCALATE_AFTER_CYCLES and row["visibility"] == scope.WORKSPACE:
            notify(
                "team",
                f"A promise made to the team is overdue and unanswered:"
                f" “{row['promise'][:80]}”, due {row['due_date']}."
                " Whoever recorded it has chased it twice.",
                tier="digest",
                link="/planning",
            )
            escalated.append(row["id"])
        db.execute(
            "UPDATE promises SET last_nudged_at = ?, nudge_count = ? WHERE id = ?",
            (db.now(), cycles, row["id"]),
        )
    return {"nudged": len(nudged), "escalated": len(escalated), "ids": nudged}
