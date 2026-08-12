"""The promise ledger: promises made to people outside the team.
The exec readout reads from here — a promise that isn't recorded is a
promise the team can't keep on purpose."""

from datetime import UTC, datetime, timedelta

from .. import db
from . import scope, wording
from .search import index_record
from .users import is_agent

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
    # a CHANGED date, not a present one. An edit form that round-trips the
    # current values reset the chase every save, so the team-wide escalation
    # the feature exists for could be deferred forever, and the cleared
    # last_nudged_at fired an extra nudge inside the same 24-hour cycle.
    renegotiated = "due_date" in fields and fields["due_date"] != row["due_date"]
    reset = ", nudge_count = 0, last_nudged_at = NULL" if renegotiated else ""
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


def _names_a_teammate(text: str) -> bool:
    """Return true when free text contains any current or former teammate."""
    from .users import fold, names_someone

    return names_someone(text, {fold(u["name"]) for u in db.query("SELECT name FROM users")})


def chase_received(*, actor: str = "scheduler") -> dict:
    """Chase overdue promises and create their notices in one transaction."""
    with db.transaction():
        return _chase_received_locked(actor=actor)


def _chase_received_locked(*, actor: str) -> dict:
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
    nudged, escalated, unroutable = [], [], []
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
        # The recorder is the person waiting, and the only one who can chase
        # it. An agent identity cannot: it reads no notifications and cannot
        # send an email, so the nudge would land nowhere (the same reason
        # rituals.py::week_open routes an agent-recorded promise to the team).
        target = row["created_by"] or "team"
        # users.is_agent, not a local SELECT on `name`: that column is BINARY
        # collation, so an exact match let "Scout" past the check and the
        # nudge was addressed to an identity that reads no notifications —
        # landing nowhere, silently, forever. routes/deps.py documents the
        # same defect.
        if is_agent(target):
            target = "team"
        # "team" is EVERY viewer (notifications.py, `user IN (?, 'team')`), so
        # that fallback turns a personal nudge into a broadcast. A scoped row's
        # body must not take it: an agent-authored PRIVATE promise reached the
        # whole roster through this line. No team-safe wording exists for a
        # body nobody else may read, so the row is skipped and counted rather
        # than the job going quiet about it.
        if target == "team" and row["visibility"] != scope.WORKSPACE:
            unroutable.append(row["id"])
            continue
        notify(
            target,
            lambda source: (
                f"Still open with {source['to_whom'] or 'the other party'}:"
                f" “{source['promise'][:80]}” was due {source['due_date']}."
            ),
            tier="digest",
            link="/planning",
            source_entity="promise",
            source_id=int(row["id"]),
        )
        nudged.append(row["id"])
        # The team escalation names only its promise source. It quotes the
        # body only when the body and party fields name no teammate.
        #
        # Once, not daily. `==`, not `>=`: the recorder keeps being nudged,
        # but a team-wide message repeating the same promise every 24 hours
        # forever is how a digest gets muted.
        if cycles == ESCALATE_AFTER_CYCLES and row["visibility"] == scope.WORKSPACE:
            notify(
                "team",
                lambda source: (
                    "A promise made to the team is overdue and unanswered"
                    + (
                        f" “{source['promise'][:80]}”"
                        if not _names_a_teammate(
                            f"{source['promise'][:80]} {source['to_whom'] or ''}"
                        )
                        else ". Read it on Work → Plan the week"
                    )
                    + f", due {source['due_date']}. Skein sent"
                    f" {wording.count(ESCALATE_AFTER_CYCLES, 'reminder')}"
                    " to whoever recorded it."
                ),
                tier="digest",
                link="/planning",
                source_entity="promise",
                source_id=int(row["id"]),
            )
            escalated.append(row["id"])
        db.execute(
            "UPDATE promises SET last_nudged_at = ?, nudge_count = ? WHERE id = ?",
            (db.now(), cycles, row["id"]),
        )
    return {
        "nudged": len(nudged),
        "escalated": len(escalated),
        # a scoped row whose only routable target was "team" — reported, not
        # dropped silently, or the job's own log would read as "nothing due"
        "unroutable": len(unroutable),
        "ids": nudged,
    }
