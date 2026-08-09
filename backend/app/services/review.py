"""Pending-changes review flow: agents (and cautious humans) propose, humans
approve. Approval applies the payload through the same service registry the
rest of the platform uses, stamped origin='agent_verified'."""

import json

from .. import db
from . import lexicon, scope


def _registry() -> dict:
    from . import (
        absences,
        blockers,
        collab,
        delegation,
        engagements,
        intake,
        memory,
        playbooks,
        promises,
        schedule,
        weekly,
        work,
    )

    return {
        "milestone": {"create": work.create_milestone, "update": work.update_milestone},
        "task": {"create": work.create_task, "update": work.update_task},
        "question": {"create": collab.ask_question, "update": collab.answer_question},
        "question_assign": {"update": collab.assign_question},
        "decision": {"create": collab.record_decision, "update": collab.supersede_decision},
        "standup": {"create": collab.post_standup},
        "note": {"create": collab.save_note},
        "note_edit": {"update": collab.update_note},
        "note_delete": {"update": collab.delete_note},
        "event": {"create": schedule.schedule_event},
        "event_cancel": {"update": schedule.cancel_event},
        "blocker": {"create": blockers.raise_blocker, "update": blockers.resolve_blocker},
        "blocker_edit": {"update": blockers.edit_blocker},
        "engagement": {
            "create": engagements.create_engagement,
            "update": engagements.update_engagement,
        },
        "intake": {"create": intake.submit_request},
        "intake_edit": {"update": intake.edit_request},
        "lesson": {"create": engagements.record_lesson},
        "playbook": {"create": playbooks.instantiate},
        "weekly_plan": {"create": weekly.apply_plan},
        "promise": {
            "create": promises.add_promise,
            "update": promises.update_promise,
        },
        "promise_edit": {"update": promises.edit_promise},
        "promise_settle": {"update": promises.update_promise},
        "memory": {"create": memory.remember},
        "memory_forget": {"update": memory.forget},
        "delegation": {"create": delegation.delegate_task},
        "task_completion": {"update": delegation.accept_completion},
        "authority": {"create": delegation.set_authority},
        "absence": {"create": absences.add_absence},
    }


def unappliable(entity: str, payload: dict) -> str:
    """Why the service would refuse this payload at apply time, or "".

    A proposal is stored now and applied LATER, through the same service the
    REST door uses. A payload the service will refuse becomes a row that can
    only ever be rejected, and the reviewer is told why at the verdict — long
    after whoever wrote it could fix it. Worse, the row is already in the queue
    when the bound ships, so a deploy strands it.

    Only the free-text bounds are checked, which are the ones a pasted line or
    a model can exceed. Every other refusal a service makes (a missing
    milestone, a bad date) is about state that can change between the proposal
    and the verdict, and guessing at it here would drop proposals that WOULD
    have applied.
    """
    from .intake import DETAIL_LEN
    from .work import DESCRIPTION_LEN, TITLE_LEN

    caps = {
        "task": (("title", TITLE_LEN), ("description", DESCRIPTION_LEN)),
        "milestone": (("title", TITLE_LEN), ("description", DESCRIPTION_LEN)),
        "intake": (("detail", DETAIL_LEN),),
    }
    for field, cap in caps.get(entity, ()):
        if len(str(payload.get(field) or "")) > cap:
            return f"{entity} {field} must be {cap} characters or fewer"
    return ""


def propose_change(
    entity: str,
    action: str,
    payload: dict,
    summary: str = "",
    entity_id: int = 0,
    *,
    actor: str = "agent",
    origin: str = "agent",
    notify_team: bool = True,
    requested_by: str = "",
) -> dict:
    reg = _registry()
    if entity not in reg:
        raise ValueError(f"unknown entity — one of {sorted(reg)}")
    if action not in ("create", "update") or action not in reg[entity]:
        raise ValueError(f"unsupported action for {entity} — create or update")
    if action == "update" and not entity_id:
        raise ValueError("entity_id required for updates")
    # a proposal a reviewer must read is bounded like any other write —
    # oversized payloads would also fail at apply and wedge in the queue
    if len(json.dumps(payload)) > 20_000:
        raise ValueError("proposal payload too large — keep it under 20k characters")
    # HERE, not in one producer: the agent gate (tools/_gate.py) and the notes
    # ingester both file proposals, and a guard in either one leaves the other
    # storing rows that can never be approved.
    refusal = unappliable(entity, payload)
    if refusal:
        raise ValueError(refusal)
    pid = db.execute(
        "INSERT INTO pending_changes (entity, entity_id, action, payload, summary,"
        " proposed_by, origin, created_at, requested_by)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entity,
            entity_id or None,
            action,
            json.dumps(payload),
            summary or f"{action} {entity}",
            actor,
            origin,
            db.now(),
            requested_by or None,
        ),
    )
    db.log_activity(
        actor,
        "propose_change",
        f"#{pid} {action} {entity}" + (f" (asked by {requested_by})" if requested_by else ""),
    )
    if notify_team:  # bulk producers (ingestion) send ONE summary instead
        from .notifications import notify

        notify(
            "team",
            f"Review needed: #{pid} {summary or f'{action} {entity}'}",
            tier="digest",
            link="/review",
        )
    return {"id": pid, "status": "pending"}


def _check_reviewer(actor: str) -> None:
    """Verdicts are human work. No tool exposes approve/reject, but the REST
    path resolves any X-User — an agent identity must be refused here too."""
    from .users import is_agent

    if is_agent(actor):
        raise ValueError(f"'{actor}' is an agent identity — proposals are judged by humans")


def _sponsor_of(change: dict) -> str:
    """The task's CURRENT sponsor for a task_completion proposal ('' for
    everything else) — looked up at verdict time, so a re-delegation moves
    the verdict to the new sponsor."""
    if change["entity"] != "task_completion" or not change["entity_id"]:
        return ""
    task = db.query_one("SELECT sponsor FROM tasks WHERE id = ?", (change["entity_id"],))
    return (task["sponsor"] or "") if task else ""


def _sponsor_override(change: dict, actor: str, note: str) -> str:
    """Acceptance verdicts belong to the sponsor — they judged the work, so
    their verdict is the trust label. Anyone else may still act, but only
    with a reason on record (the sponsor is away, gone, or asked them to),
    and the verdict is marked an override so it never feeds a streak.
    Returns a label for the activity log when this verdict is an override,
    else ''."""
    if change["entity"] != "task_completion":
        return ""
    sponsor = _sponsor_of(change)
    if sponsor and actor == sponsor:
        return ""
    if not sponsor:
        # reassignment cleared the delegation: nobody sponsors this proposal
        # anymore, so NO verdict on it is a trust signal — reason required
        if not note.strip():
            raise ValueError(
                f"task #{change['entity_id']}'s delegation was cleared —"
                " judging this orphaned acceptance needs a note saying why"
            )
        return "orphaned delegation"
    if not note.strip():
        raise ValueError(
            f"task #{change['entity_id']} is sponsored by {sponsor} — acting"
            " for them needs a note saying why (it goes on the record)"
        )
    return sponsor


def _claim(
    change_id: int,
    new_status: str,
    note: str,
    actor: str,
    strong: bool = False,
    override: bool = False,
) -> None:
    """Compare-and-swap the pending -> reviewed transition so concurrent
    approve/reject calls can't both act on the same change."""
    claimed = db.execute_rowcount(
        "UPDATE pending_changes SET status = ?, reviewed_by = ?, review_note = ?,"
        " reviewed_at = ?, reviewed_strong = ?, reviewed_override = ?"
        " WHERE id = ? AND status = 'pending'",
        (new_status, actor, note, db.now(), int(strong), int(override), change_id),
    )
    if not claimed:
        change = db.query_one("SELECT status FROM pending_changes WHERE id = ?", (change_id,))
        if not change:
            raise db.NotFound(f"pending change #{change_id} not found")
        raise ValueError(f"change #{change_id} already {change['status']}")


def _assert_judgeable(change: dict, viewer: scope.Viewer) -> None:
    """A verdict on a row you cannot read is refused, in the same words as a
    proposal that does not exist.

    `_readable` hides these from the queue and `change_diff` returns no diff,
    but the VERDICT endpoints took a bare id — so a caller in no crew could
    walk ids and approve a proposal whose payload then overwrote a crew note's
    content, or applied a note_delete / memory_forget / event_cancel. The
    apply runs as `change["proposed_by"]`, an agent slug, and scope.is_machine
    lets a machine work a crew row on purpose, so assert_editable inside the
    handler never refuses it. This is the only place that can.

    NotFound with the same sentence as an absent proposal: any other wording
    tells the caller that #12 exists and is scoped (scope.missing).
    """
    tier = _governing_tier(change)
    # a VANISHED target is not refused here, unlike _readable: there is no row
    # left to protect, and approve_change has its own auto-reject path for it
    # ("target vanished") that a 404 would hide behind the wrong sentence.
    # _readable still drops it from the LIST, where the summary would show.
    if not isinstance(tier, tuple):  # None (unscoped) or "gone" (deleted)
        return
    if not scope.can_read(tier[0], tier[1], viewer, tier[2]):
        raise db.NotFound(f"pending change #{change['id']} not found")


def approve_change(
    change_id: int,
    note: str = "",
    *,
    actor: str = "system",
    strong: bool = False,
    viewer: scope.Viewer = scope.NOBODY,
) -> dict:
    _check_reviewer(actor)
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise db.NotFound(f"pending change #{change_id} not found")
    _assert_judgeable(change, viewer)
    # settle the already-reviewed case before any gating, so a non-sponsor
    # isn't told to fetch a note for a verdict that already happened
    if change["status"] != "pending":
        raise ValueError(f"change #{change_id} already {change['status']}")
    # the direct authority endpoint requires a personal key; the proposal
    # path must not be the weaker door to the same lever
    if change["entity"] == "authority" and not strong:
        raise ValueError(
            "authority changes need a strong identity — approve with your personal API key"
        )

    # resolve the handler BEFORE claiming — a stale entity/action must not
    # leave the row marked approved with nothing applied
    try:
        fn = _registry()[change["entity"]][change["action"]]
    except KeyError as exc:
        raise ValueError(f"no handler for {change['entity']}.{change['action']}") from exc
    payload = json.loads(change["payload"])
    sponsor = _sponsor_override(change, actor, note)
    _claim(change_id, "approved", note, actor, strong, override=bool(sponsor))
    try:
        # compound applies (playbook, weekly_plan) land atomically or not at
        # all — a failed apply rolls back, so pending is safe for EVERY entity
        with db.transaction():
            # authorship stays with the proposer: created_by must say who
            # wrote it, not who clicked approve (the verdict is recorded on
            # the pending_changes row + activity)
            author = change["proposed_by"] or actor
            if change["action"] == "update":
                result = fn(change["entity_id"], **payload, actor=author, origin="agent_verified")
            else:
                result = fn(**payload, actor=author, origin="agent_verified")
    except db.NotFound as exc:
        # the proposal's own target vanished (event cancelled via REST, row
        # hard-deleted): re-approving can never succeed, so a pending reset
        # would boomerang forever — settle it as rejected, on the record
        db.execute(
            "UPDATE pending_changes SET status = 'rejected', review_note = ? WHERE id = ?",
            (f"auto-rejected — target vanished: {exc}", change_id),
        )
        db.log_activity(actor, "reject_change", f"#{change_id} (target vanished)")
        _clear_review_ping(change_id)
        raise ValueError(
            f"could not apply {change['entity']}.{change['action']}: {exc}"
            " — proposal auto-rejected (its target no longer exists)"
        ) from exc
    except db.TerminalReject as exc:
        # a permanent policy block (an agent's own delegated-done proposal):
        # re-approving can never succeed, so settle it rejected like a vanished
        # target instead of resetting to pending, where it would clutter the
        # queue until a human rejected it by hand
        db.execute(
            "UPDATE pending_changes SET status = 'rejected', review_note = ? WHERE id = ?",
            (f"auto-rejected — {exc}", change_id),
        )
        db.log_activity(actor, "reject_change", f"#{change_id} (not applicable)")
        _clear_review_ping(change_id)
        raise ValueError(f"could not apply and auto-rejected: {exc}") from exc
    except Exception as exc:
        # ANY OTHER failure (IntegrityError, lock timeout, stale state)
        # resets the claim — an approved-but-never-applied proposal would
        # vanish from the queue. The reviewer's note survives the reset.
        db.execute(
            "UPDATE pending_changes SET status = 'pending', reviewed_by = NULL,"
            " reviewed_at = NULL, reviewed_strong = 0, reviewed_override = 0,"
            " review_note = ? WHERE id = ?",
            (f"apply failed: {exc}" + (f" (reviewer note: {note})" if note else ""), change_id),
        )
        raise ValueError(f"could not apply {change['entity']}.{change['action']}: {exc}") from exc

    db.execute(
        "UPDATE pending_changes SET result_id = ? WHERE id = ?", (result.get("id"), change_id)
    )
    applied = f"#{result['id']}" if result.get("id") is not None else "applied"
    db.log_activity(
        actor,
        "approve_change",
        f"#{change_id} -> {change['entity']} {applied}"
        + (f" (accepted for {sponsor})" if sponsor else ""),
    )
    _clear_review_ping(change_id)
    return {"id": change_id, "status": "approved", "result": result}


def _clear_review_ping(change_id: int) -> None:
    """The review is handled — its "Review needed" ping must not keep
    nagging. Called AFTER the apply succeeds (a failed apply resets the
    proposal to pending and must keep its notification unread)."""
    from .notifications import mark_read_matching

    mark_read_matching(f"Review needed: #{change_id} ")


def reject_change(
    change_id: int,
    note: str = "",
    *,
    actor: str = "system",
    strong: bool = False,
    viewer: scope.Viewer = scope.NOBODY,
) -> dict:
    _check_reviewer(actor)
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise db.NotFound(f"pending change #{change_id} not found")
    # a reject is a verdict too: it feeds rejection streaks and demotion, and
    # it settles a proposal against a row this caller cannot read
    _assert_judgeable(change, viewer)
    if change["status"] != "pending":
        raise ValueError(f"change #{change_id} already {change['status']}")
    # symmetric with approve: a non-sponsor reject feeds rejection streaks
    # (demotion input), so it needs the same reason-on-record
    sponsor = _sponsor_override(change, actor, note)
    _claim(change_id, "rejected", note, actor, strong, override=bool(sponsor))
    db.log_activity(
        actor,
        "reject_change",
        f"#{change_id}" + (f" (rejected for {sponsor})" if sponsor else ""),
    )
    _clear_review_ping(change_id)
    return {"id": change_id, "status": "rejected"}


_DIFF_TABLES = {
    "task": "tasks",
    "milestone": "milestones",
    "engagement": "engagements",
    "promise": "promises",
    "promise_edit": "promises",
    "promise_settle": "promises",
    "blocker": "blockers",
    "blocker_edit": "blockers",
    "question": "questions",
    "decision": "decisions",
    "note_edit": "notes",
    "note_delete": "notes",
    "event_cancel": "events",
    "intake_edit": "intake_requests",
    "memory_forget": "memories",
}

# What table an entity's proposal TARGETS. A superset of _DIFF_TABLES, which
# answers a narrower question (what can be rendered as a before/after) and so
# omits the two entities whose payload is not a column set: task_completion
# carries a free-text work summary, question_assign carries an assignee.
# _readable needs the TARGET, and reading it off _DIFF_TABLES let both of
# those bypass the tier check entirely — every reader saw a crew task's
# acceptance payload in the review queue, on the dashboard, and in the stats.
_TARGET_TABLE = {
    **_DIFF_TABLES,
    # updates _DIFF_TABLES cannot render (their payload is not a column set)
    "task_completion": "tasks",
    "question_assign": "questions",
    # every CREATE. These have no target row yet, so _DIFF_TABLES never needed
    # them — but the tier the row WOULD take is in the payload, and without an
    # entry here _readable had nothing to look up and kept the proposal, body
    # and all, for every reader.
    "task": "tasks",
    "milestone": "milestones",
    "question": "questions",
    "decision": "decisions",
    "standup": "standups",
    "note": "notes",
    "event": "events",
    "blocker": "blockers",
    "engagement": "engagements",
    "intake": "intake_requests",
    "lesson": "lessons",
    "promise": "promises",
    "memory": "memories",
    "absence": "absences",
    "delegation": "tasks",
}

# A CREATE whose subject is a row that ALREADY EXISTS names it in the payload.
# `delegation` is the case: delegate_task(task_id=...) changes a task that has
# its own tier, so there is nothing to declare in the payload and nothing in
# `entity_id`, which propose_change only requires for updates. Reading neither,
# such a proposal was shown to every reader AND judgeable by them — a
# non-member approved a delegation of a crew task they cannot see.
_CREATE_PARENT = {"delegation": ("tasks", "task_id")}

# Entities that address no scoped row at all, and why. Kept as an explicit
# list rather than an absence, so tests/test_review.py can prove _registry
# gained nothing that silently skips the tier check.
_UNTARGETED = {
    "playbook": "instantiates a whole engagement tree, no single target row",
    "weekly_plan": "commits a set of task ids, each already tier-checked on its own",
    "authority": "an agent's permission matrix, which carries no tier",
}

# columns worth showing a reviewer when a proposal would DESTROY the row —
# an empty payload must never mean an uninformed verdict
_DESTRUCTIVE_VIEW = {
    "note_delete": ("topic", "content"),
    "memory_forget": ("topic", "content", "user"),
    "event_cancel": ("title", "starts_at", "attendees"),
}


def change_diff(change_id: int, viewer: scope.Viewer = scope.NOBODY) -> dict:
    """Before/after view for update proposals: current row values for exactly
    the fields the payload would change.

    Filtered on the TARGET row, not on the proposal. `pending_changes` carries
    no tier of its own (scope.UNSCOPED) and this endpoint is CurrentUser, so
    an unfiltered read here handed any reader the current body of a private
    note or memory verbatim — _DESTRUCTIVE_VIEW renders `content` and `topic`
    for exactly the two entities whose deletion always files a proposal.
    A reader who cannot see the row gets no diff at all: the proposed half is
    the payload, which for an edit holds the new body.
    """
    change = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (change_id,))
    if not change:
        raise db.NotFound(f"pending change #{change_id} not found")
    table = _DIFF_TABLES.get(change["entity"])
    if change["action"] != "update" or not table or not change["entity_id"]:
        return {"id": change_id, "diff": None}
    frag, fp = scope.visible_filter(viewer, table) if table in scope.CLASSIFIED else ("1 = 1", [])
    row = db.query_one(
        f"SELECT * FROM {table} WHERE id = ? AND {frag}",  # noqa: S608 — table from constant map, and scope.visible_filter emits only bound marks
        (change["entity_id"], *fp),
    )
    if not row:
        return {"id": change_id, "diff": None}
    payload = json.loads(change["payload"])
    doomed = _DESTRUCTIVE_VIEW.get(change["entity"])
    if doomed:
        # deletion diff: show what would be destroyed; proposed side is empty
        current = {k: (row.get(k) if row else None) for k in doomed}
        return {
            "id": change_id,
            "diff": {"current": current, "proposed": dict.fromkeys(doomed, "")},
        }
    current = {k: (row.get(k) if row else None) for k in payload}
    return {"id": change_id, "diff": {"current": current, "proposed": payload}}


def mark_seen(ids: list[int], *, actor: str = "system") -> dict:
    """The review UI calls this when a human loads pending proposals —
    first-seen (claim_at) starts the active-review clock, so review burden
    can be measured as seen→verdict instead of created→verdict (which is
    dominated by queue wait, not human effort)."""
    batch = ids[:200]
    if not batch:
        return {"seen": 0}
    marks = ", ".join("?" for _ in batch)
    n = db.execute_rowcount(
        f"UPDATE pending_changes SET claim_at = ?"  # noqa: S608 — placeholders built above
        f" WHERE id IN ({marks}) AND status = 'pending' AND claim_at IS NULL",
        (db.now(), *batch),
    )
    return {"seen": n}


def review_stats(viewer: scope.Viewer = scope.NOBODY) -> dict:
    """The review inbox as a flywheel: every verdict is a labeled example.
    These stats show which proposal types earn trust and which waste reviewer
    time — the input to authority-matrix decisions."""
    by_entity = db.query(
        "SELECT entity,"
        " COUNT(*) AS proposed,"
        " SUM(status = 'approved') AS approved,"
        " SUM(status = 'rejected') AS rejected,"
        " SUM(status = 'pending') AS pending,"
        " ROUND(AVG(CASE WHEN reviewed_at IS NOT NULL THEN"
        " (julianday(reviewed_at) - julianday(created_at)) * 24 END), 1) AS avg_review_hours"
        " FROM pending_changes GROUP BY entity ORDER BY proposed DESC"
    )
    by_proposer = db.query(
        "SELECT proposed_by,"
        " COUNT(*) AS proposed,"
        " SUM(status = 'approved') AS approved,"
        " SUM(status = 'rejected') AS rejected"
        " FROM pending_changes GROUP BY proposed_by ORDER BY proposed DESC"
    )
    # the only list here that carries row TEXT. The aggregates above count
    # rows per entity and per proposer, which discloses nothing; `summary` is
    # built by the producer out of the target row's own title, so a rejected
    # proposal against a crew note republished it to the whole roster.
    rejection_reasons = _readable(
        db.query(
            "SELECT entity, entity_id, summary, review_note, reviewed_by FROM pending_changes"
            " WHERE status = 'rejected' AND review_note != '' ORDER BY id DESC LIMIT 20"
        ),
        viewer,
    )
    minutes = sorted(
        r["m"]
        for r in db.query(
            "SELECT (julianday(reviewed_at) - julianday(claim_at)) * 24 * 60 AS m"
            " FROM pending_changes WHERE reviewed_at IS NOT NULL AND claim_at IS NOT NULL"
        )
        if r["m"] is not None
    )
    # the shared primitive, not a third inline copy — services/stats.py exists
    # because two of these had already drifted apart
    from . import stats

    n = len(minutes)
    median = stats.median(minutes)
    return {
        "by_entity": by_entity,
        "by_proposer": by_proposer,
        "recent_rejections": rejection_reasons,
        # medians over means (docs/INSIGHTS.md contract)
        "active_review_minutes": {"median": median, "n": n},
    }


def _governing_tier(change: dict) -> tuple[str, int | None, str] | str | None:
    """The tier that decides who may see or judge one proposal.

    Returns `(visibility, crew_id, author)`, the string "gone" when the row it
    addresses has been deleted, or None when it addresses no scoped row at all
    (weekly_plan, authority, playbook).

    ONE resolver, because `_readable` and `_assert_judgeable` each grew their
    own and they disagreed: the list hid a create while the verdict endpoints
    applied it. A proposal that is invisible must not be approvable, and the
    only way to keep that true is for both to ask the same question.

    Three sources, in order: the row `entity_id` names (updates), the row the
    payload names (_CREATE_PARENT), then the tier the payload declares.
    """
    table = _TARGET_TABLE.get(change["entity"])
    if table not in scope.CLASSIFIED:
        return None
    try:
        payload = json.loads(change["payload"]) if change.get("payload") else {}
    except (TypeError, ValueError):
        payload = {}

    row_id = change["entity_id"]
    if not row_id and change["entity"] in _CREATE_PARENT:
        table, key = _CREATE_PARENT[change["entity"]]
        row_id = payload.get(key)
    if row_id:
        author = scope.CLASSIFIED[table]
        row = db.query_one(
            f"SELECT visibility, crew_id, {author} AS author FROM {table} WHERE id = ?",  # noqa: S608 — table and column from constant maps
            (row_id,),
        )
        return (row["visibility"], row["crew_id"], row["author"] or "") if row else "gone"

    # a create with no parent row: the tier it WOULD land at is declared here.
    # Absent because the caller chose none, and absent because the caller never
    # selected the payload column — both mean workspace, and neither can
    # disclose a body the reader is not already being shown.
    crew = payload.get("crew_id")
    return (
        str(payload.get("visibility") or scope.WORKSPACE),
        crew if isinstance(crew, int) else None,
        str(payload.get("author") or ""),
    )


def _readable(rows: list[dict], viewer: scope.Viewer) -> list[dict]:
    """Drop proposals whose target row the viewer may not read.

    `pending_changes` carries no tier of its own (scope.UNSCOPED) and this
    list returns the whole row — `payload` parsed, and a `summary` that four
    producers build out of the row's own text (delegation.submit_completion,
    tools/collab.py::delete_note, tools/memory.py::forget_memory,
    tools/schedule.py::cancel_event). Served unfiltered to every CurrentUser,
    the review queue is a full-text mirror of every scoped row somebody
    proposed a change to.

    Dropped, not blanked: a reviewer who cannot read the row cannot judge the
    change, so a redacted entry is a verdict taken blind. The crew keeps
    seeing its own.

    An entity with no tier at all (weekly_plan, authority, playbook) stays —
    those carry no row to be scoped by, and defaulting them out would empty
    the queue the whole review flow runs on.

    Eight readers quote `summary` or `payload` out of this table and each has
    to call this explicitly — GET /api/review, briefing.my_day,
    delegation.agent_inbox, review_stats, the handoff, the week-close ritual,
    and the two insights rules that write into findings.receipt. They do not
    arrive here on their own.
    """
    out = []
    for r in rows:
        tier = _governing_tier(r)
        if tier is None:
            # no scoped row to be judged by (weekly_plan, authority, playbook).
            # Keeping these is what makes the queue usable at all.
            out.append(r)
        elif not isinstance(tier, tuple):
            # "gone": the target row is DELETED. We cannot prove the viewer
            # could read it and the summary still quotes it, so this fails
            # closed — anything else makes deleting the row the way to
            # publish it.
            continue
        elif scope.can_read(tier[0], tier[1], viewer, tier[2]):
            out.append(r)
    return out


def list_changes(status: str = "pending", viewer: scope.Viewer = scope.NOBODY) -> list[dict]:
    if status:
        rows = db.query(
            "SELECT * FROM pending_changes WHERE status = ? ORDER BY id DESC LIMIT 200",
            (status,),
        )
    else:
        rows = db.query("SELECT * FROM pending_changes ORDER BY id DESC LIMIT 100")
    rows = _readable(rows, viewer)
    # only the pairs on THIS page: trust_scores computes for every pair in
    # the settled history, and a queue of 200 rows from one proposer would
    # otherwise pay for every agent the deployment has ever had.
    record = _trust_by_pair({(r["proposed_by"], r["entity"]) for r in rows}) if rows else {}
    for r in rows:
        r["payload"] = json.loads(r["payload"])
        # what this proposal is CALLED, resolved here so the header, the
        # checkbox label and the notification cannot drift apart
        r["label"] = lexicon.phrase(r["entity"], r["action"])
        # the UI shows whose verdict this is — acceptance belongs to the sponsor
        if r["entity"] == "task_completion":
            r["sponsor"] = _sponsor_of(r)
        r["record"] = record.get((r["proposed_by"], r["entity"]))
    return rows


def _trust_by_pair(wanted: set[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """This proposer's record on THIS entity, keyed for the queue.

    The reviewer judged every proposal blind: approval rate and streak were
    computed already and lived two pages away on /agents, so the one screen
    where the number decides something was the one screen without it. Read
    from `delegation.trust_scores` rather than recomputed — a second
    definition of "streak" that disagreed with the promotion job would be
    worse than none.

    Scoped to the pairs the page actually shows. `trust_scores` runs a
    per-pair lookup for every pair in the settled history, so an unfiltered
    call cost 123 queries to render a queue that needed one — the cost grew
    with the deployment's age rather than with the page.
    """
    from .delegation import (
        TRUST_STREAK,
        _authority_cutoff,
        _judged_pairs,
        promotion_blocked,
        trust_blocked,
        trust_scores,
    )
    from .users import is_agent

    # Why no streak CAN form, when none can — the same sentence Team → Agents
    # renders above its trust card. In trusted-header mode (the default) a
    # verdict is weak, so `recent_streak` is 0 for everyone: without this the
    # row read "8 of 8 approved (100%) · no run of approvals", which states a
    # perfect record and no run of approvals in one breath, and the promotion
    # line could never appear. An operator's fix, not a wait.
    blocked = trust_blocked()
    # one scan for the whole page: promotion_blocked below would otherwise
    # re-read every authority proposal per row (services/delegation.py)
    judged = _judged_pairs(_authority_cutoff())
    out: dict[tuple[str, str], dict] = {}
    for t in trust_scores(wanted):
        # AGENT proposers only. Ingest files proposals under the person who
        # pasted the notes (services/ingest.py, origin='human'), and /review is
        # team-visible — so keying this on the proposer alone would put one
        # teammate's approval history in front of the whole roster, which is
        # person-level data judging the PAST. The anti-surveillance rule is
        # enforced in the service layer, not by hoping a caller filters.
        # It is also the wrong question: the record exists to decide whether an
        # AGENT has earned more autonomy. Nobody scores a colleague's rate.
        if not is_agent(t["agent"]):
            continue
        out[(t["agent"], t["entity"])] = {
            "approved": t["approved"],
            "proposed": t["proposed"],
            "approval_rate": t["approval_rate"],
            "streak": t["recent_streak"],
            "streak_blocked": blocked,
            "level": t["current_level"],
            # said at the verdict, where the approval that earns it happens.
            # Only when this verdict is the one that closes the streak AND a
            # promotion is actually available from here — trust_scores makes
            # the same `review` check for its own suggestion.
            # asks delegation, never restates its rule: promotion_blocked
            # refuses the ALWAYS_REVIEW and NO_AUTHORITY entities and stays
            # silent for 28 days after a human declines. task_completion is
            # in NO_AUTHORITY and is also the entity a delegated agent
            # proposes on MOST, so a restatement here promised a promotion
            # that could never be filed, on the common case.
            "promotes_at": (
                TRUST_STREAK
                if t["recent_streak"] == TRUST_STREAK - 1
                and not promotion_blocked(t["agent"], t["entity"], t["current_level"], judged)
                else 0
            ),
        }
    return out
