"""Routines: recurring work written once (docs/intent/routines.md).

A routine is a template row. Each time it is due, fire_due creates an
ordinary task under the owner's name, and with an agent it delegates the task
with the owner as sponsor, so the wake queue starts one bounded turn. The
firing is a human delegation by standing consent (origin 'human'), so the
review gate, the authority matrix and delegate_task do not change.

Only the owner edits, resumes or deletes a routine: a firing writes under the
owner's name, so an edit by anyone else puts that name on words the owner did
not write. Any reader can pause one, because a pause writes nothing under
anyone's name and resume undoes it.
"""

import logging
from datetime import UTC, date, datetime, time, timedelta

from .. import config, db
from . import scope, users

log = logging.getLogger("skein.routines")

AUTO_PAUSE_AFTER = 3
# two tick periods (jobs.py runs the tick every 5 minutes): a firing later
# than this after its time is recorded `late`
ON_TIME = timedelta(minutes=10)
MAX_ACTIVE_PER_OWNER = 25
DUE_BATCH = 200
# " (YYYY-MM-DD)" is appended to each task title, and the task title cap must
# still hold (work.TITLE_LEN)
_DATE_SUFFIX = 13
_FIELDS = (
    "title",
    "description",
    "priority",
    "assignee",
    "agent",
    "acceptance_criteria",
    "weekdays",
    "at_time",
    "every_weeks",
    "starts_on",
    "due_days",
)
_SCHEDULE = ("weekdays", "at_time", "every_weeks", "starts_on")


def next_occurrence(r: dict, after: datetime) -> datetime:
    """The first scheduled moment strictly after `after`, in UTC.

    The schedule is local: days, a wall time and a week parity counted from
    the Monday of the week of starts_on, in config.TZ. A wall time in a DST
    gap resolves with fold 0, so 02:30 on a spring-forward day runs at 03:30.
    In an overlap it is the first 01:30."""
    days = {int(d) for d in str(r["weekdays"]).split(",")}
    hour, minute = (int(part) for part in str(r["at_time"]).split(":"))
    start = date.fromisoformat(str(r["starts_on"]))
    anchor = start - timedelta(days=start.weekday())
    every = int(r["every_weeks"])
    day = max(after.astimezone(config.TZ).date(), start)
    # one full cycle plus the day `after` falls on is enough to meet a kept day
    for _ in range(7 * every + 8):
        if day.isoweekday() in days and ((day - anchor).days // 7) % every == 0:
            moment = datetime.combine(day, time(hour, minute), tzinfo=config.TZ).astimezone(UTC)
            if moment > after:
                return moment
        day += timedelta(days=1)
    raise ValueError("the schedule has no day to run on")


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def _moment(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _identity_locks(*names: str) -> None:
    """Identity locks before any routine row, in one order for every path.

    users.rename_user and users.set_active take LOCK_IDENTITY before the rows
    they rewrite. A path here that held the routine row first and then waited
    on an identity would close a deadlock cycle with a rename updating
    routines.created_by or routines.agent."""
    for identity in sorted({users.fold(n) for n in names if n}):
        db.name_lock(db.LOCK_IDENTITY, identity)


def _weekdays(value: object) -> str:
    raw = value if isinstance(value, list | tuple) else str(value or "").split(",")
    try:
        days = sorted({int(str(d).strip()) for d in raw if str(d).strip()})
    except ValueError:
        raise ValueError("weekdays must be numbers from 1 (Monday) to 7 (Sunday)") from None
    if not days or any(d < 1 or d > 7 for d in days):
        raise ValueError("Pick at least one day. Days are 1 (Monday) to 7 (Sunday).")
    return ",".join(str(d) for d in days)


def _validated(fields: dict, *, owner: str, tier: str, crew_id: int | None) -> dict:
    """The checks create_task and delegate_task run, done when the routine is
    saved: a firing must not fail on a value the form accepted."""
    from .work import DESCRIPTION_LEN, PRIORITIES, TITLE_LEN

    out = dict(fields)
    out["title"] = str(out.get("title") or "").strip()
    if not out["title"]:
        raise ValueError("A routine needs a title.")
    if len(out["title"]) > TITLE_LEN - _DATE_SUFFIX:
        raise ValueError(
            f"The title is longer than {TITLE_LEN - _DATE_SUFFIX} characters."
            " Skein adds the date to each task title. Write a shorter title."
        )
    out["description"] = str(out.get("description") or "")
    if len(out["description"]) > DESCRIPTION_LEN:
        raise ValueError(f"The description is longer than {DESCRIPTION_LEN} characters.")
    out["priority"] = str(out.get("priority") or "medium")
    if out["priority"] not in PRIORITIES:
        raise ValueError(f"priority must be one of {', '.join(PRIORITIES)}")
    out["weekdays"] = _weekdays(out.get("weekdays"))
    at_time = str(out.get("at_time") or "")
    try:
        parsed = time.fromisoformat(at_time)
    except ValueError:
        raise ValueError("at_time must be a time shaped HH:MM") from None
    if len(at_time) != 5:
        raise ValueError("at_time must be a time shaped HH:MM")
    out["at_time"] = parsed.strftime("%H:%M")
    every = out.get("every_weeks") or 1
    if not isinstance(every, int) or isinstance(every, bool) or not 1 <= every <= 4:
        raise ValueError("every_weeks must be a whole number from 1 to 4")
    out["every_weeks"] = every
    starts_on = str(out.get("starts_on") or db.today().isoformat())
    try:
        date.fromisoformat(starts_on)
    except ValueError:
        raise ValueError("starts_on must be a date shaped YYYY-MM-DD") from None
    out["starts_on"] = starts_on
    due_days = out.get("due_days")
    if due_days is not None and (
        not isinstance(due_days, int) or isinstance(due_days, bool) or not 0 <= due_days <= 27
    ):
        raise ValueError("due_days must be a whole number from 0 to 27, or empty")
    out["due_days"] = due_days
    out["assignee"] = str(out.get("assignee") or "").strip()
    out["agent"] = str(out.get("agent") or "").strip()
    out["acceptance_criteria"] = str(out.get("acceptance_criteria") or "").strip()
    if out["agent"] and out["assignee"]:
        raise ValueError(
            "Pick an assignee or an agent, not both. The agent is the assignee of a delegated task."
        )
    if out["agent"]:
        if tier == scope.PRIVATE:
            raise ValueError(
                "A private routine cannot delegate, because a private task has one reader."
                " Pick a crew, or make the routine visible to everyone on the roster."
            )
        row = db.query_one(
            "SELECT name, kind, active, identity_owner FROM users WHERE name = ?",
            (out["agent"],),
        )
        if (
            not row
            or row["kind"] != "agent"
            or not row["active"]
            or not users.is_delegatable_agent_identity(
                str(row["name"]), str(row["identity_owner"] or "")
            )
        ):
            raise ValueError("That agent cannot take routine work. Pick an agent from the roster.")
        if len(out["acceptance_criteria"]) > 1000:
            raise ValueError(
                "acceptance_criteria is longer than 1000 characters."
                " Write a shorter definition of done."
            )
    else:
        out["acceptance_criteria"] = ""
    if out["assignee"]:
        scope.assert_readable_by(tier, crew_id, out["assignee"], label="assignee", author=owner)
    return {k: out[k] for k in _FIELDS}


def _readable(routine_id: int, actor: str, *, hold: bool = False) -> dict:
    """The row, or the absent row's 404 for a caller who cannot read it."""
    frag, vp = scope.visible_filter(scope.Viewer.for_actor(actor), "routines")
    row = db.query_one(
        f"SELECT * FROM routines WHERE id = ? AND {frag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        + (" FOR UPDATE" if hold else ""),
        (routine_id, *vp),
    )
    if not row:
        raise scope.missing("routines", routine_id)
    return row


def _names(routine_id: int) -> tuple[str, str]:
    """The owner and agent names, read before any lock so their identity
    locks can be taken first (_identity_locks). The row is read again under
    the locks, and a caller that finds a name changed meanwhile acts on
    nothing."""
    row = db.query_one("SELECT created_by, agent FROM routines WHERE id = ?", (routine_id,))
    return (str(row["created_by"]), str(row["agent"])) if row else ("", "")


def _owner_only(row: dict, actor: str) -> None:
    if row["created_by"] != actor:
        raise PermissionError(
            f"Only {row['created_by']} can change routine #{row['id']}."
            f" Pause it, or ask {row['created_by']}."
        )


def _active_count(owner: str) -> int:
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM routines WHERE created_by = ? AND status = 'active'",
        (owner,),
    )
    return int(row["n"]) if row else 0


def _refuse_over_cap(owner: str) -> None:
    if _active_count(owner) >= MAX_ACTIVE_PER_OWNER:
        raise ValueError(
            f"You have {MAX_ACTIVE_PER_OWNER} active routines, the limit."
            " Pause or delete one first."
        )


def create_routine(fields: dict, *, actor: str) -> dict:
    visibility = str(fields.get("visibility") or scope.WORKSPACE)
    with db.transaction():
        # the owner lock guards the active count below: two creates both
        # read 24 and both insert without it
        _identity_locks(actor, str(fields.get("agent") or ""))
        tier, cid = scope.resolve_write(visibility, int(fields.get("crew_id") or 0), actor=actor)
        clean = _validated(fields, owner=actor, tier=tier, crew_id=cid)
        _refuse_over_cap(actor)
        now = datetime.now(UTC)
        next_at = _stamp(next_occurrence(clean, now))
        ts = db.now()
        rid = db.execute(
            "INSERT INTO routines (title, description, priority, assignee, agent,"
            " acceptance_criteria, weekdays, at_time, every_weeks, starts_on, due_days,"
            " next_at, created_by, created_at, updated_at, visibility, crew_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (*(clean[k] for k in _FIELDS), next_at, actor, ts, ts, tier, cid),
        )
        db.log_activity(actor, "create_routine", scope.detail(tier, f"#{rid}", clean["title"]))
    return {"id": rid, "title": clean["title"], "status": "active", "next_at": next_at}


def update_routine(routine_id: int, fields: dict, *, actor: str) -> dict:
    with db.transaction():
        _identity_locks(actor, str(fields.get("agent") or ""), _names(routine_id)[1])
        row = _readable(routine_id, actor, hold=True)
        _owner_only(row, actor)
        merged = {k: row[k] for k in _FIELDS}
        merged.update({k: v for k, v in fields.items() if k in _FIELDS})
        clean = _validated(merged, owner=actor, tier=row["visibility"], crew_id=row["crew_id"])
        changed = [k for k in _FIELDS if clean[k] != row[k]]
        if not changed:
            raise ValueError("nothing to update")
        next_at = row["next_at"]
        # a schedule edit counts from now: an old time never fires because
        # someone changed the day
        if row["status"] == "active" and any(k in _SCHEDULE for k in changed):
            next_at = _stamp(next_occurrence(clean, datetime.now(UTC)))
        sets = ", ".join(f"{k} = ?" for k in changed)
        db.execute(
            f"UPDATE routines SET {sets}, next_at = ?, updated_at = ? WHERE id = ?",  # noqa: S608 — keys from _FIELDS
            (*(clean[k] for k in changed), next_at, db.now(), routine_id),
        )
        # the id and the field names, never the text (collab.update_note)
        db.log_activity(actor, "update_routine", f"#{routine_id} {', '.join(changed)}")
    return {"id": routine_id, "changed": changed, "next_at": next_at}


def _pause(routine_id: int, reason: str, *, by: str = "") -> None:
    db.execute(
        "UPDATE routines SET status = 'paused', paused_reason = ?, paused_by = ?,"
        " next_at = NULL, updated_at = ? WHERE id = ?",
        (reason, by, db.now(), routine_id),
    )


def _tell_owner(row: dict, message: str) -> None:
    """A notice to the owner only. The owner can always read their own
    routine, so the text may name it at any tier."""
    from .notifications import notify

    notify(str(row["created_by"]), message, tier="digest", link="/planning#planning-routines")


def _named(row: dict) -> str:
    return f"routine #{row['id']} '{row['title']}'"


def pause_routine(routine_id: int, *, actor: str) -> dict:
    with db.transaction():
        row = _readable(routine_id, actor, hold=True)
        # a second pause keeps the first reason: overwriting nobody_finished
        # with by_person hides why the routine stopped
        if row["status"] == "paused":
            return {"id": routine_id, "status": "paused"}
        _pause(routine_id, "by_person", by=actor)
        db.log_activity(actor, "pause_routine", f"#{routine_id}")
        if actor != row["created_by"]:
            _tell_owner(row, f"{actor} paused your {_named(row)}.")
    return {"id": routine_id, "status": "paused"}


def _owner_in_crew(row: dict) -> bool:
    """Whether the owner can still write at the routine's tier. Only a crew
    tier can stop them: the membership that let them save it can end."""
    if row["visibility"] != scope.CREW:
        return True
    from . import crews

    try:
        crews.assert_writable(int(row["crew_id"]), str(row["created_by"]))
    except (db.NotFound, ValueError):
        return False
    return True


def resume_routine(routine_id: int, *, actor: str) -> dict:
    with db.transaction():
        _identity_locks(actor, _names(routine_id)[1])
        row = _readable(routine_id, actor, hold=True)
        _owner_only(row, actor)
        if row["status"] == "active":
            raise ValueError(f"Routine #{routine_id} is already active.")
        if not _owner_in_crew(row):
            raise ValueError(
                f"You are no longer in the crew of routine #{routine_id}."
                " Delete the routine, or ask the crew to add you."
            )
        # the agent can have gone since the save, and a resumed routine that
        # refuses at every firing pauses itself again a moment later
        _validated(
            {k: row[k] for k in _FIELDS},
            owner=actor,
            tier=row["visibility"],
            crew_id=row["crew_id"],
        )
        _refuse_over_cap(actor)
        # from now: a pause never catches up
        next_at = _stamp(next_occurrence(row, datetime.now(UTC)))
        db.execute(
            "UPDATE routines SET status = 'active', paused_reason = '', paused_by = '',"
            " next_at = ?, skipped_in_row = 0, updated_at = ? WHERE id = ?",
            (next_at, db.now(), routine_id),
        )
        db.log_activity(actor, "resume_routine", f"#{routine_id}")
    return {"id": routine_id, "status": "active", "next_at": next_at}


def delete_routine(routine_id: int, *, actor: str) -> dict:
    """The owner deletes a routine, or any reader once the owner is inactive.
    Its tasks stay and keep routine_id, so their acceptances stay counted
    apart (docs/intent/routines.md D11)."""
    with db.transaction():
        row = _readable(routine_id, actor, hold=True)
        if row["created_by"] != actor and users.is_active(str(row["created_by"])):
            _owner_only(row, actor)
        frag, vp = scope.visible_filter(scope.Viewer.for_actor(actor), "routines")
        db.execute(
            f"DELETE FROM routines WHERE id = ? AND {frag}",  # noqa: S608 — scope.visible_filter emits only bound marks
            (routine_id, *vp),
        )
        db.log_activity(actor, "delete_routine", f"#{routine_id}")
    return {"id": routine_id, "deleted": True}


def list_routines(viewer: scope.Viewer) -> list[dict]:
    frag, vp = scope.visible_filter(viewer, "routines", alias="r")
    tfrag, tp = scope.visible_filter(viewer, "tasks", alias="t")
    return db.query(
        f"SELECT r.*, latest.id AS latest_task_id, latest.status AS latest_task_status"  # noqa: S608 — scope.visible_filter emits only bound marks
        " FROM routines r LEFT JOIN LATERAL (SELECT t.id, t.status FROM tasks t"
        f" WHERE t.routine_id = r.id AND {tfrag} ORDER BY t.id DESC LIMIT 1) latest ON TRUE"
        f" WHERE {frag} ORDER BY r.status, r.next_at NULLS LAST, r.id",
        (*tp, *vp),
    )


def _local_day(moment: datetime) -> date:
    return moment.astimezone(config.TZ).date()


def fire_due(routine_id: int, now: datetime) -> str:
    """Fire one routine if it is due, in one transaction. Returns the outcome
    code ('fired', 'late', 'previous_open', 'paused'), or '' when nothing was
    due.

    The routine row is the once-per-occurrence claim: it is held FOR UPDATE,
    `next_at` is checked again under the hold, and it advances in the same
    transaction that creates the task. A second process waits on the row,
    reads the advanced value and does nothing."""
    with db.transaction():
        owner, agent = _names(routine_id)
        if not owner:
            return ""
        _identity_locks(owner, agent)
        row = db.query_one("SELECT * FROM routines WHERE id = ? FOR UPDATE", (routine_id,))
        if (
            not row
            or row["status"] != "active"
            # a rename between the probe and the locks: these are not the
            # identities this transaction holds, so the next tick fires it
            or row["created_by"] != owner
            or row["agent"] != agent
            or _moment(str(row["next_at"])) > now
        ):
            return ""
        latest = _moment(str(row["next_at"]))
        missed = 1
        upcoming = next_occurrence(row, latest)
        while upcoming <= now:
            latest, missed = upcoming, missed + 1
            upcoming = next_occurrence(row, latest)
        next_at = _stamp(upcoming)
        if not _owner_in_crew(row):
            _pause(routine_id, "owner_left_crew")
            db.log_activity("scheduler", "skip_routine", f"routine #{routine_id} owner_left_crew")
            _tell_owner(
                row,
                f"Skein paused {_named(row)} because you are no longer in its crew."
                " Delete the routine, or ask the crew to add you.",
            )
            return "paused"
        previous = db.query_one(
            "SELECT id, status FROM tasks WHERE routine_id = ? ORDER BY id DESC LIMIT 1",
            (routine_id,),
        )
        if previous and previous["status"] not in ("done", "void"):
            return _skip(row, int(previous["id"]), next_at, now)
        local_day = _local_day(latest)
        due_date = (
            (local_day + timedelta(days=int(row["due_days"]))).isoformat()
            if row["due_days"] is not None
            else ""
        )
        try:
            with db.savepoint():
                task_id = _create(row, local_day, due_date)
        except (ValueError, PermissionError, db.NotFound):
            # the refusal repeats at every tick until someone changes the
            # routine, so it pauses instead of retrying
            log.info("routine #%s refused at its firing", routine_id)
            _pause(routine_id, "fire_refused")
            db.log_activity("scheduler", "skip_routine", f"routine #{routine_id} fire_refused")
            _tell_owner(
                row,
                f"Skein paused {_named(row)} because it could not create or delegate the task."
                " Check the agent and the crew, then resume the routine.",
            )
            return "paused"
        outcome = "late" if now - latest > ON_TIME else "fired"
        db.execute(
            "UPDATE routines SET next_at = ?, last_outcome = ?, last_outcome_at = ?,"
            " skipped_in_row = 0 WHERE id = ?",
            (next_at, outcome, _stamp(now), routine_id),
        )
        detail = f"routine #{routine_id} -> task #{task_id}"
        if outcome == "late":
            detail += ", late"
        if missed > 1:
            detail += f", {missed} times in 1"
            _tell_owner(
                row,
                f"Routine #{routine_id} '{row['title']}' missed {missed} times because Skein"
                f" was not running. Skein created one task for the latest time,"
                f" {local_day.isoformat()} at {row['at_time']}: task #{task_id}.",
            )
        db.log_activity("scheduler", "fire_routine", detail)
        return outcome


def _create(row: dict, local_day: date, due_date: str) -> int:
    from . import delegation, work

    owner = str(row["created_by"])
    task = work.create_task(
        f"{row['title']} ({local_day.isoformat()})",
        row["description"],
        assignee=row["assignee"],
        priority=row["priority"],
        due_date=due_date,
        actor=owner,
        origin="human",
        visibility=row["visibility"],
        crew_id=int(row["crew_id"] or 0),
    )
    task_id = int(task["id"])
    db.execute("UPDATE tasks SET routine_id = ? WHERE id = ?", (row["id"], task_id))
    if row["agent"]:
        # the owner is actor and sponsor, and origin 'human' queues the wake:
        # the routine is the owner's standing consent (D2)
        delegation.delegate_task(
            task_id,
            str(row["agent"]),
            owner,
            acceptance_criteria=str(row["acceptance_criteria"]),
            actor=owner,
            origin="human",
        )
    return task_id


def _skip(row: dict, open_task: int, next_at: str, now: datetime) -> str:
    """One open occurrence at a time. The skip names no person: a routine
    whose work is unfinished says which task, never who did not finish it."""
    routine_id = int(row["id"])
    skipped = int(row["skipped_in_row"]) + 1
    db.log_activity(
        "scheduler", "skip_routine", f"routine #{routine_id} previous_open task #{open_task}"
    )
    if skipped >= AUTO_PAUSE_AFTER:
        _pause(routine_id, "nobody_finished")
        db.execute(
            "UPDATE routines SET skipped_in_row = ?, last_outcome = 'previous_open',"
            " last_outcome_at = ? WHERE id = ?",
            (skipped, _stamp(now), routine_id),
        )
        _tell_owner(
            row,
            f"Skein paused {_named(row)}. Skein skipped the last {skipped} times because"
            f" task #{open_task} is still open. Finish or close task #{open_task},"
            " then resume the routine.",
        )
        return "paused"
    db.execute(
        "UPDATE routines SET next_at = ?, skipped_in_row = ?, last_outcome = 'previous_open',"
        " last_outcome_at = ? WHERE id = ?",
        (next_at, skipped, _stamp(now), routine_id),
    )
    return "previous_open"


_COUNTED = {"fired": "fired", "late": "late", "previous_open": "skipped", "paused": "paused"}


def tick(now: datetime | None = None) -> dict:
    """Fire every due routine (the `routines` job, every 5 minutes).

    Each firing is its own transaction, so one failure leaves the others
    fired, and the failure marks the run partial. The result is counts only:
    jobs._outcome_detail copies it into job_outcomes, which has no tier, so a
    routine title must never reach it."""
    now = now or datetime.now(UTC)
    due = db.query(
        "SELECT id FROM routines WHERE status = 'active' AND next_at <= ? ORDER BY next_at LIMIT ?",
        (_stamp(now), DUE_BATCH),
    )
    counts = dict.fromkeys(("fired", "late", "skipped", "paused"), 0)
    failed = 0
    for row in due:
        try:
            outcome = fire_due(int(row["id"]), now)
        except Exception:
            log.exception("a routine firing failed")
            failed += 1
            continue
        if outcome in _COUNTED:
            counts[_COUNTED[outcome]] += 1
    if failed:
        return {**counts, "failed": failed, "status": "partial"}
    return counts


def pause_for_identity(name: str, kind: str) -> int:
    """Pause the routines a deactivation strands. users.set_active calls this
    inside its transaction, after its LOCK_IDENTITY, so a firing waiting on
    the same lock reads a paused row. A human's own routines pause quietly,
    because nobody can act on them. A routine that delegates to a deactivated
    agent tells its owner, who can pick another agent."""
    column, reason = (
        ("agent", "agent_unavailable")
        if kind == "agent"
        else (
            "created_by",
            "owner_inactive",
        )
    )
    rows = db.query(
        f"UPDATE routines SET status = 'paused', paused_reason = ?, paused_by = '',"  # noqa: S608 — column from the fixed pair above
        f" next_at = NULL, updated_at = ? WHERE {column} = ? AND status = 'active'"
        " RETURNING id, title, created_by, agent",
        (reason, db.now(), name),
    )
    if kind == "agent":
        for row in rows:
            _tell_owner(
                row,
                f"Skein paused {_named(row)} because agent {row['agent']} is deactivated."
                " Pick another agent, then resume the routine.",
            )
    return len(rows)
