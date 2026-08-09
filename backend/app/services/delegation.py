"""Agents as first-class teammates: task delegation with a human sponsor,
an authority matrix per (agent, entity), a mission-control view, and trust
scores computed from the review inbox — promotion is suggested, never automatic."""

import json
from datetime import UTC

from .. import config, db
from ..agents.identity import refuse_when_consultative
from . import scope
from .users import ensure_user

LEVELS = ("autonomous", "notify", "review", "forbidden")

# Entities the authority matrix must never carry: internal flows file them and
# no agent tool passes them to the gate, so a grant would be a placebo.
# routes/api.py::get_agent_entities serves this same set to the picker.
NO_AUTHORITY = frozenset({"authority", "task_completion", "weekly_plan"})
TRUST_STREAK = 5  # consecutive approvals before we suggest promotion


def delegate_task(
    task_id: int, agent: str, sponsor: str, *, actor: str = "system", origin: str = "human"
) -> dict:
    if not agent.strip():
        raise ValueError("agent name is required")
    sponsor = sponsor.strip()
    sponsor_row = db.query_one("SELECT kind FROM users WHERE name = ? AND active = 1", (sponsor,))
    if not sponsor_row or sponsor_row["kind"] != "human":
        raise ValueError(
            "sponsor must be an active human teammate — the sponsor"
            " receives the acceptance proposal, so a typo here means nobody does"
        )
    # an agent naming itself is not a delegation, it's a land-grab; the
    # human-approved proposal path (origin agent_verified) stays open
    if agent.strip() == actor and origin != "agent_verified":
        raise ValueError("an agent cannot delegate a task to itself — propose it instead")
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        raise scope.missing("tasks", task_id)
    scope.assert_editable("tasks", task, actor, verb="delegate")
    # A private task has ONE reader, and an agent is not it. THIS RAISE IS THE
    # ONLY BARRIER: claim_task, report_progress, accept_completion and
    # submit_completion below each gate on `delegated_agent` alone and never
    # call scope.assert_editable, so once a private task carries a delegate
    # nothing downstream refuses it. It would also put the title in
    # agent_inbox.
    if task["visibility"] == scope.PRIVATE:
        raise ValueError(
            "a private task has one reader, so it cannot be delegated."
            " Pick a crew, or make this task visible to everyone on the roster."
        )
    # the notify below quotes the task title to whatever name the caller
    # passed, and the sponsor then reviews the agent's work on the task
    scope.assert_readable_by(
        task["visibility"],
        task["crew_id"],
        sponsor,
        label="sponsor",
        author=task["created_by"],
    )
    ensure_user(agent, kind="agent")
    db.execute(
        "UPDATE tasks SET delegated_agent = ?, sponsor = ?, assignee = ?, updated_at = ?"
        " WHERE id = ?",
        (agent, sponsor, agent, db.now(), task_id),
    )
    db.log_activity(actor, "delegate_task", f"#{task_id} -> {agent} (sponsor: {sponsor})")
    from .notifications import notify

    notify(
        sponsor,
        f"You sponsor task #{task_id} '{task['title']}' delegated to {agent}.",
        tier="digest",
        link="/agents",
    )
    return {"id": task_id, "delegated_agent": agent, "sponsor": sponsor}


def _check_not_forbidden(actor: str) -> None:
    """The delegation trio bypasses gated_write by design (working your own
    delegation is direct), but the kill switch must still hold."""
    if authority_level(actor, "task") == "forbidden":
        raise ValueError(f"'{actor}' is forbidden on tasks — ask a human to lift it")


def _assert_readable_or_missing(task: dict, actor: str, task_id: int) -> None:
    """A task that exists but is not this actor's answers like one that does
    not exist, UNLESS the actor could read it anyway.

    The informative refusals below ("not delegated to you", "written by its
    delegate or sponsor only") are worth keeping — they tell a legitimate
    caller what went wrong. They are also a 400 where an absent id gives a
    404, so on a row the caller cannot read the pair reports existence, and
    ids are sequential (services/scope.py::Viewer.for_actor names the attack).
    Readable, the message discloses nothing the caller could not already
    fetch. Unreadable, it is the only thing that says the row is there.
    """
    if not scope.can_read(
        task["visibility"],
        task["crew_id"],
        scope.Viewer.for_actor(actor),
        task.get("created_by", ""),
    ):
        raise scope.missing("tasks", task_id)


def claim_task(task_id: int, *, actor: str, origin: str = "agent") -> dict:
    """The agent picks up its delegated task: todo -> in_progress. Direct
    (not review-gated) — status motion on the agent's own delegation is
    reversible and the sponsor is told."""
    refuse_when_consultative("claim delegated tasks")
    _check_not_forbidden(actor)
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        raise scope.missing("tasks", task_id)
    if task["delegated_agent"] != actor:
        _assert_readable_or_missing(task, actor, task_id)
        raise ValueError(f"task #{task_id} is not delegated to '{actor}'")
    if task["status"] not in ("todo", "blocked"):
        raise ValueError(f"task #{task_id} is {task['status']} — nothing to claim")
    db.execute(
        "UPDATE tasks SET status = 'in_progress', updated_at = ? WHERE id = ?",
        (db.now(), task_id),
    )
    db.log_activity(
        actor, "claim_task", scope.detail(task["visibility"], f"#{task_id}", task["title"])
    )
    if task["sponsor"]:
        from .notifications import notify

        notify(
            task["sponsor"],
            f"{actor} started on task #{task_id} '{task['title']}'.",
            tier="digest",
            link="/agents",
        )
    return {"id": task_id, "status": "in_progress"}


def report_progress(task_id: int, note: str, *, actor: str, origin: str = "agent") -> dict:
    """Append a worklog entry — the agent's running account, readable by the
    sponsor before the acceptance verdict. Additive, so direct (like standups)
    — but only for the parties in the loop: the worklog is evidence the
    sponsor judges on, so nobody else may write into it."""
    note = note.strip()
    if not note:
        raise ValueError("the progress note is required")
    if len(note) > 2000:
        raise ValueError("keep progress notes under 2000 characters")
    refuse_when_consultative("write to a worklog")
    _check_not_forbidden(actor)
    # visibility and crew_id ride along on a SELECT that already runs: a
    # worklog note is the task's text, and a workspace child under a scoped
    # task publishes what the task was scoped to hide
    task = db.query_one(
        "SELECT delegated_agent, sponsor, status, title, visibility, crew_id, created_by"
        " FROM tasks WHERE id = ?",
        (task_id,),
    )
    if not task:
        raise scope.missing("tasks", task_id)
    if actor not in (task["delegated_agent"], task["sponsor"]):
        _assert_readable_or_missing(task, actor, task_id)
        raise ValueError(f"task #{task_id}'s worklog is written by its delegate or sponsor only")
    if task["status"] == "done":
        raise ValueError(f"task #{task_id} is done — its worklog is history now")
    tier, cid = scope.inherit(task)
    wid = db.execute(
        "INSERT INTO task_worklog (task_id, author, note, origin, created_at,"
        " visibility, crew_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (task_id, actor, note, origin, db.now(), tier, cid),
    )
    db.log_activity(actor, "report_progress", scope.detail(tier, f"task #{task_id}", note[:80]))
    return {"id": wid, "task_id": task_id}


def list_worklog(
    task_id: int, limit: int = 50, viewer: scope.Viewer = scope.NOBODY, *, actor: str = ""
) -> list[dict]:
    """The worklog on a task, for a reader who may see it.

    `actor` is the delegation door, and it exists because READ has to match
    WRITE. report_progress lets exactly two identities write here — the task's
    delegated_agent and its sponsor — and gates on those columns alone. With
    only the tier filter, an agent delegated a CREW task could write notes it
    could not read back, and got `no task #N` for a task it was working.

    An agent holds no crew membership (crews.add_member refuses agent
    identities), so no Viewer built from its name would ever reach that row.
    Passing an unfiltered `1 = 1` instead — the shape agent_inbox uses — would
    reach EVERY private worklog by walking sequential ids, so the door is the
    delegation itself, checked per task, and nothing wider.

    A private task cannot carry a delegate at all (delegate_task refuses one),
    so this door opens onto crew and workspace rows only."""
    # Clamped HERE, not at each door: every caller that forwards a
    # model-supplied limit would otherwise have to remember, and the MCP twin
    # (app/mcp_server.py) did not. A negative LIMIT in SQLite means NO limit,
    # so an unclamped value pulls every note on the task into a context window.
    limit = max(1, min(int(limit or 50), 50))
    # A party to the delegation reads it whatever the tier says — the write
    # rule in report_progress, applied to the read. Resolved from the task's
    # own columns, per task, so it can never widen into "agents read
    # everything".
    party = False
    if actor:
        row = db.query_one("SELECT delegated_agent, sponsor FROM tasks WHERE id = ?", (task_id,))
        party = row is not None and actor in (row["delegated_agent"], row["sponsor"])
    # the existence check takes the same filter as the worklog below, so an
    # unreadable task answers exactly like an absent one. Unfiltered, a
    # private task returned 200 [] and an absent id returned 404 — which reads
    # off which ids exist, for sequential integers (scope.Viewer.for_actor).
    tfrag, tp = scope.visible_filter(viewer, "tasks")
    if not party and not db.query_one(
        f"SELECT id FROM tasks WHERE id = ? AND {tfrag}",  # noqa: S608 — scope.visible_filter emits only bound marks
        (task_id, *tp),
    ):
        raise scope.missing("tasks", task_id)
    if party:
        return db.query(
            "SELECT * FROM task_worklog WHERE task_id = ? ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        )
    # the worklog is the task's own text, and its parent may be invisible to
    # this reader — the child has to be filtered on its own tier, not the
    # task's existence check above
    frag, vp = scope.visible_filter(viewer, "task_worklog")
    return db.query(
        f"SELECT * FROM task_worklog WHERE task_id = ? AND {frag}"  # noqa: S608 — scope.visible_filter emits only bound marks
        " ORDER BY id DESC LIMIT ?",
        (task_id, *vp, limit),
    )


def accept_completion(
    task_id: int, summary: str = "", *, actor: str = "", origin: str = ""
) -> dict:
    """Registry apply target for task_completion proposals: the sponsor's
    approval IS the acceptance — mark done, close the loop."""
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        raise scope.missing("tasks", task_id)
    if task["status"] == "done":
        raise ValueError(f"task #{task_id} is already done")
    # a reassignment between submit and verdict voids the proposal — the
    # acceptance must be for work the proposer still owns
    if actor and task["delegated_agent"] != actor:
        raise ValueError(f"task #{task_id} is no longer delegated to '{actor}'")
    # completed_at, not just status: flow metrics filter on it, so a delegated
    # task accepted here would otherwise count zero in cycle time AND in
    # throughput. The forge routes every delegated close to this function, so
    # the more work a team delegates, the more of its throughput disappears.
    db.execute(
        "UPDATE tasks SET status = 'done', completed_at = ?, updated_at = ? WHERE id = ?",
        (db.now(), db.now(), task_id),
    )
    if summary:
        tier, cid = scope.inherit(task)
        db.execute(
            "INSERT INTO task_worklog (task_id, author, note, origin, created_at,"
            " visibility, crew_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                actor or "agent",
                f"[accepted] {summary}",
                origin or "agent_verified",
                db.now(),
                tier,
                cid,
            ),
        )
    db.log_activity(
        actor or "agent",
        "complete_task",
        scope.detail(task["visibility"], f"#{task_id}", task["title"]),
    )
    return {"id": task_id, "status": "done"}


def submit_completion(task_id: int, summary: str, *, actor: str, requested_by: str = "") -> dict:
    """File the acceptance proposal. ALWAYS a proposal (never direct) — the
    sponsor's verdict is the whole point of the loop, and every verdict is a
    labeled trust signal for this agent."""
    if not summary.strip():
        raise ValueError("say what was done — the sponsor reads this summary")
    # guarded like the rest of the trio even though the outcome is already a
    # proposal: this one pings the sponsor at `immediate` tier and takes the
    # one-pending-proposal slot below, so a member asked for an opinion would
    # interrupt a human mid-consultation over work nobody requested
    refuse_when_consultative("submit work for acceptance")
    _check_not_forbidden(actor)
    task = db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
    if not task:
        raise scope.missing("tasks", task_id)
    if task["delegated_agent"] != actor:
        _assert_readable_or_missing(task, actor, task_id)
        raise ValueError(f"task #{task_id} is not delegated to '{actor}'")
    if task["status"] == "done":
        raise ValueError(f"task #{task_id} is already done")
    dup = db.query_one(
        "SELECT id FROM pending_changes WHERE entity = 'task_completion'"
        " AND entity_id = ? AND status = 'pending'",
        (task_id,),
    )
    if dup:
        raise ValueError(
            f"task #{task_id} already awaits acceptance (proposal #{dup['id']})"
            " — wait for the sponsor's verdict"
        )
    from .review import propose_change

    p = propose_change(
        "task_completion",
        "update",
        {"summary": summary.strip()},
        # scope.detail, not an f-string: this summary is served by
        # GET /api/review, by my_day's pending_reviews, and by rituals'
        # week-close artifact on disk — so a crew task's title reached the
        # roster three ways. This call passes notify_team=False, so the team
        # notification is NOT one of them.
        summary=scope.detail(
            task["visibility"],
            f"accept task #{task_id}",
            f"'{task['title']}': {summary.strip()[:80]}",
        ),
        entity_id=task_id,
        actor=actor,
        origin="agent",
        notify_team=False,
        requested_by=requested_by,
    )
    if task["sponsor"]:
        from .notifications import notify

        notify(
            task["sponsor"],
            f"{actor} submitted task #{task_id} '{task['title']}' for your"
            f" acceptance (proposal #{p['id']}).",
            tier="immediate",
            link="/review",
        )
    return {"proposal_id": p["id"], "task_id": task_id, "status": "pending"}


def set_authority(
    agent: str,
    entity: str,
    level: str,
    expected_current: str = "",
    *,
    actor: str = "system",
    origin: str = "human",
) -> dict:
    agent = agent.strip()
    if not agent or agent == "anonymous":
        raise ValueError("agent name is required")
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    # streak-filed proposals pin the from-level: a stale proposal must never
    # override what a human set in the meantime — above all the kill switch
    if expected_current and authority_level(agent, entity) != expected_current:
        raise ValueError(
            f"{agent}/{entity} is now '{authority_level(agent, entity)}',"
            f" not '{expected_current}' — this proposal is stale. Re-run the review"
        )
    # the kill switch must not be self-serviceable: an agent identity (e.g. a
    # key issued to one) can never grant or lift authority — humans only
    actor_row = db.query_one("SELECT kind FROM users WHERE name = ?", (actor,))
    if (actor_row and actor_row["kind"] == "agent") or actor == agent:
        raise ValueError("authority levels are set by humans, not by the agent itself")
    from ..tools._gate import ALWAYS_REVIEW
    from .review import _registry

    if entity not in _registry():
        raise ValueError(f"unknown entity — one of {sorted(_registry())}")
    # routes/api.py hides these from the picker because no agent tool passes
    # them to the gate. Validating there but not here let a direct POST store
    # a grant the picker cannot produce and the gate never reads — a row on
    # the authority card naming a power that does not exist.
    if entity in NO_AUTHORITY:
        raise ValueError(f"'{entity}' carries no authority level — no agent tool writes it")
    # _gate.py takes the review path for these BEFORE it reads the level, so
    # storing autonomous or notify renders "acts alone" on a destructive row
    # while every such write still waits for a human. Refuse the level rather
    # than display a correction: an unrepresentable state cannot be displayed
    # wrongly.
    if entity in ALWAYS_REVIEW and level in ("autonomous", "notify"):
        raise ValueError(f"'{entity}' always waits for a human — set it to 'review' or 'forbidden'")
    ensure_user(agent, kind="agent")
    # authority half-life: elevated grants carry a review-by date (90d
    # default) — "nothing in Skein is trusted forever, not decisions, not
    # agents." The authority_stale findings rule nags past it; reconfirm by
    # re-granting.
    review_by = None
    if level in ("autonomous", "notify"):
        from datetime import timedelta

        review_by = (db.today() + timedelta(days=90)).isoformat()
    db.execute(
        "INSERT INTO agent_authority (agent, entity, level, updated_by, updated_at, review_by)"
        " VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (agent, entity) DO UPDATE SET level = excluded.level,"
        " updated_by = excluded.updated_by, updated_at = excluded.updated_at,"
        " review_by = excluded.review_by",
        (agent, entity, level, actor, db.now(), review_by),
    )
    db.log_activity(actor, "set_authority", f"{agent}/{entity} -> {level}")
    return {"agent": agent, "entity": entity, "level": level, "review_by": review_by}


def authority_level(agent: str, entity: str) -> str:
    """Default is 'review' — new agents earn autonomy, they don't start with it."""
    row = db.query_one(
        "SELECT level FROM agent_authority WHERE agent = ? AND entity = ?",
        (agent, entity),
    )
    return row["level"] if row else "review"


def authority_matrix(agent: str = "") -> list[dict]:
    if agent:
        return db.query("SELECT * FROM agent_authority WHERE agent = ? ORDER BY entity", (agent,))
    return db.query("SELECT * FROM agent_authority ORDER BY agent, entity")


def trust_blocked() -> str:
    """Why no trust CAN accrue, or "" when it can. An empty trust card reads
    as "nobody has proposed anything yet" — but two deployment settings make
    the streak structurally unreachable, and under those the card is telling
    an operator to wait for something that will never arrive.

    Deterministic and observed, never predicted: the second case counts real
    verdicts rather than guessing what the auth mode will produce."""
    if not config.AGENT_REVIEW:
        # with the gate off, a review-level write applies directly and files
        # no proposal at all — so there is no verdict to earn trust with
        # (tools/_gate.py takes the direct branch on `not config.AGENT_REVIEW`)
        return (
            "The review gate is off, so agent writes apply directly and record no verdict."
            " Trust cannot increase. To collect verdicts, set SKEIN_AGENT_REVIEW=1."
        )
    settled = (
        db.query_one(
            "SELECT COUNT(*) AS n, SUM(reviewed_strong = 1 AND reviewed_override = 0) AS strong"
            " FROM pending_changes WHERE status != 'pending'"
        )
        # an aggregate always returns one row, but query_one is typed Optional
        # and a bare index here is what mypy refuses
        or {}
    )
    total, strong = int(settled.get("n") or 0), int(settled.get("strong") or 0)
    if total and not strong:
        return (
            f"Skein recorded {total} verdict{'' if total == 1 else 's'}."
            " Nobody used a personal API key to approve or reject."
            " Only key-authenticated verdicts count toward a promotion streak."
            " Before you approve, paste your key in Settings, step 2."
        )
    return ""


def trust_scores(pairs: set[tuple[str, str]] | None = None) -> list[dict]:
    """Approval stats per (proposer, entity) from pending_changes — every
    review verdict is already a labeled trust signal.

    `pairs` narrows the work to the (proposer, entity) rows a caller will
    actually read. The per-pair loop below runs three queries EACH, so an
    unfiltered call costs three times every pair the deployment has ever
    settled — a cost that grows with its age. The Approvals queue asks about
    the handful on one page (services/review.py::_trust_by_pair).
    """
    rows = db.query(
        "SELECT proposed_by AS agent, entity,"
        " COUNT(*) AS proposed,"
        " SUM(status = 'approved') AS approved,"
        " SUM(status = 'rejected') AS rejected"
        " FROM pending_changes WHERE status != 'pending'"
        " GROUP BY proposed_by, entity ORDER BY proposed DESC"
    )
    if pairs is not None:
        rows = [r for r in rows if (r["agent"], r["entity"]) in pairs]
    for r in rows:
        # promotion suggestions count only strong-identity verdicts — a
        # spoofed X-User must not be able to walk an agent to autonomous
        recent = db.query(
            "SELECT status FROM pending_changes WHERE proposed_by = ? AND entity = ?"
            " AND status != 'pending' AND reviewed_strong = 1 AND reviewed_override = 0"
            " ORDER BY reviewed_at DESC, id DESC LIMIT ?",
            (r["agent"], r["entity"], TRUST_STREAK),
        )
        streak = 0
        for row in recent:
            if row["status"] != "approved":
                break
            streak += 1
        rejection_streak = 0
        for row in recent:
            if row["status"] != "rejected":
                break
            rejection_streak += 1
        r["approval_rate"] = round(r["approved"] / r["proposed"], 2) if r["proposed"] else 0
        r["recent_streak"] = streak
        r["rejection_streak"] = rejection_streak
        r["current_level"] = authority_level(r["agent"], r["entity"])
        r["suggestion"] = (
            f"{streak} straight approvals — consider promoting to autonomous"
            if streak >= TRUST_STREAK and r["current_level"] == "review"
            else ""
        )
    return rows


DEMOTION_STREAK = 3


def promotion_blocked(agent: str, entity: str, level: str) -> str:
    """Why a promotion cannot be proposed for this pair, or "".

    ONE definition, because two surfaces act on it: `review_authority` files
    the proposal, and the Approvals queue tells a reviewer that the next
    approval will file one. A restatement in either place is a promise the
    other does not keep — `task_completion` is the highest-volume entity a
    delegated agent proposes on, and it can never be promoted at all.
    """
    from ..tools._gate import ALWAYS_REVIEW
    from .users import is_agent

    if not is_agent(agent):
        return "authority levels apply to agent identities only"
    if entity in NO_AUTHORITY:
        # set_authority refuses these outright, so a filed proposal would
        # wedge in the queue rather than apply
        return f"'{entity}' never carries an authority level"
    if entity in ALWAYS_REVIEW:
        # the gate takes the review path for these BEFORE it reads the level
        return f"'{entity}' always waits for a human"
    if level != "review":
        return "a promotion climbs one rung, from 'needs approval'"
    if _authority_recently_judged(agent, entity):
        return "a human judged this pair's authority in the last 28 days"
    return ""


def _authority_recently_judged(agent: str, entity: str) -> bool:
    """A pending authority proposal, or one a human declined inside 28 days.
    Refiling either is nagging, so `review_authority` stays silent — and the
    queue must not advertise what the job will decline to file."""
    from datetime import datetime, timedelta

    cutoff = (datetime.now(UTC) - timedelta(days=28)).isoformat(timespec="seconds")
    for p in db.query(
        "SELECT payload FROM pending_changes WHERE entity = 'authority'"
        " AND (status = 'pending' OR (status = 'rejected' AND reviewed_at > ?))",
        (cutoff,),
    ):
        row = json.loads(p["payload"])
        if row.get("agent") == agent and row.get("entity") == entity:
            return True
    return False


def review_authority(*, actor: str = "scheduler") -> dict:
    """A2: turn earned trust into FILED PROPOSALS instead of a buried hint.
    Promotions climb one rung (review -> notify) on a strong-verdict approval
    streak; demotions to review fire on a strong-verdict rejection streak.
    The system only proposes — a human approves, and agents can never
    approve anything, so there is no self-promotion path."""
    from datetime import datetime, timedelta

    filed = []
    # don't refile what's pending, and don't nag weekly about what a human
    # just declined — a rejection buys 28 days of silence for that pair
    recent_cutoff = (datetime.now(UTC) - timedelta(days=28)).isoformat(timespec="seconds")
    seen = {
        (json.loads(p["payload"]).get("agent"), json.loads(p["payload"]).get("entity"))
        for p in db.query(
            "SELECT payload FROM pending_changes WHERE entity = 'authority'"
            " AND (status = 'pending' OR (status = 'rejected' AND reviewed_at > ?))",
            (recent_cutoff,),
        )
    }
    from .users import is_agent

    # authority levels only mean something for agent identities on entities
    # the gate consults: streaks from humans, the scheduler, or the meta
    # entities would mint nonsense agent rows if proposed
    for r in trust_scores():
        if not is_agent(r["agent"]):
            continue
        target = None
        why = ""
        if r["recent_streak"] >= TRUST_STREAK and not promotion_blocked(
            r["agent"], r["entity"], r["current_level"]
        ):
            target = "notify"
            why = f"{r['recent_streak']} straight strong-verdict approvals"
        elif r["rejection_streak"] >= DEMOTION_STREAK and r["current_level"] in (
            "autonomous",
            "notify",
        ):
            target = "review"
            why = f"{r['rejection_streak']} straight strong-verdict rejections"
        if not target or (r["agent"], r["entity"]) in seen:
            continue
        from .review import propose_change

        p = propose_change(
            "authority",
            "create",
            {
                "agent": r["agent"],
                "entity": r["entity"],
                "level": target,
                "expected_current": r["current_level"],
            },
            summary=f"authority: {r['agent']}/{r['entity']}"
            f" {r['current_level']} -> {target} ({why})"
            + (
                " — notify means direct writes with an FYI, no pre-review"
                if target == "notify"
                else ""
            ),
            actor=actor,
            origin="agent",
            notify_team=False,
        )
        filed.append({"proposal_id": p["id"], "agent": r["agent"], "entity": r["entity"]})
    if filed:
        from .notifications import notify

        notify(
            "team",
            f"{len(filed)} authority change{'' if len(filed) == 1 else 's'}"
            " proposed from review history —"
            " promote or demote in Inbox → Approvals.",
            tier="digest",
            link="/review",
        )
    return {"filed": len(filed), "proposals": filed}


def mission_control() -> list[dict]:
    """One row per agent identity: what it holds, what it's waiting on."""
    agents = db.query("SELECT name FROM users WHERE kind = 'agent' AND active = 1 ORDER BY name")
    out = []
    for a in agents:
        name = a["name"]
        open_tasks = db.query_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE delegated_agent = ? AND status != 'done'",
            (name,),
        )
        pending = db.query_one(
            "SELECT COUNT(*) AS n FROM pending_changes WHERE proposed_by = ?"
            " AND status = 'pending'",
            (name,),
        )
        last = db.query_one("SELECT MAX(created_at) AS ts FROM activity WHERE actor = ?", (name,))
        out.append(
            {
                "agent": name,
                "open_tasks": open_tasks["n"] if open_tasks else 0,
                "pending_proposals": pending["n"] if pending else 0,
                "last_seen": last["ts"] if last else None,
                "authority": authority_matrix(name),
            }
        )
    return out


def agent_inbox(agent: str, viewer: "scope.Viewer | None" = None) -> dict:
    """Ambient inbox: everything an agent should look at when it wakes up.
    Deterministic — the same view a human gets from my_day, agent-shaped.

    `viewer=None` means the agent is reading its OWN inbox — the tool door
    passes agent_identity(), the MCP door passes its ACTOR — and the rows stay
    unfiltered: a crew task delegated to an agent is work that agent has to
    see, and crews.add_member refuses an agent identity — so a Viewer built
    from an agent's own name carries no crews and would strip exactly those
    rows (the workspace ones would still come back, which is what makes the
    loss easy to miss).

    GET /api/agents/{agent}/inbox is the other door. It takes the agent name
    off the URL and answers any CurrentUser, so it passes the CALLER's viewer
    — without one, a human read every crew task title delegated to any agent
    by walking the roster of agent names.
    """
    if not db.query_one("SELECT id FROM users WHERE name = ?", (agent,)):
        raise db.NotFound(
            f"no such agent '{agent}'. Check the name: a typo reads as an empty inbox."
        )
    tfrag, tp = ("1 = 1", []) if viewer is None else scope.visible_filter(viewer, "tasks")
    qfrag, qp = ("1 = 1", []) if viewer is None else scope.visible_filter(viewer, "questions")
    tasks = db.query(
        "SELECT id, title, status, priority, sponsor FROM tasks"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" WHERE delegated_agent = ? AND status != 'done' AND {tfrag}"
        " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1"
        " WHEN 'medium' THEN 2 ELSE 3 END, id",
        (agent, *tp),
    )
    questions = db.query(
        "SELECT id, question, asked_by FROM questions WHERE assigned_to = ?"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" AND status = 'open' AND {qfrag} ORDER BY id",
        (agent, *qp),
    )
    # through _readable on the REST door for the same reason the two queries
    # above take a filter: `summary` and `review_note` quote the target row's
    # own text, and GET /api/agents/{agent}/inbox answers any CurrentUser with
    # the agent name off the URL. The agent's own doors (viewer is None) keep
    # everything — a rejection it cannot read is a correction it cannot act on.
    from .review import _readable

    rejected = db.query(
        "SELECT id, entity, entity_id, summary, review_note, reviewed_by FROM pending_changes"
        " WHERE proposed_by = ? AND status = 'rejected' ORDER BY id DESC LIMIT 10",
        (agent,),
    )
    if viewer is not None:
        rejected = _readable(rejected, viewer)
    # `notifications` carries no tier (scope.UNSCOPED) and its bodies quote
    # scoped rows, so the REST door gets counts and the agent gets the text.
    notifications = db.query(
        "SELECT id, message, link, created_at FROM notifications"
        " WHERE user = ? AND read_at IS NULL ORDER BY id DESC LIMIT 20",
        (agent,),
    )
    if viewer is not None:
        notifications = [{k: v for k, v in n.items() if k != "message"} for n in notifications]
    # The agent's own last note per open task — the continuity an agent
    # resuming on a later day has nowhere else to get. Its chat session does
    # not carry it: the conversation manager drops the oldest messages and
    # pin_first is inert across turns (agents/team_agent.py). Without this,
    # day 3 restarts from the task title and repeats day 1's dead ends.
    #
    # Own notes only, and the REST door gets none: this is the agent's
    # working memory, and GET /api/agents/{agent}/inbox takes the agent name
    # off the URL and answers any CurrentUser — the same reason `message`
    # is stripped from notifications above. read_worklog is the full record,
    # filtered on its own tier.
    last_notes: list[dict] = []
    if viewer is None and tasks:
        # bare `note`/`created_at` beside MAX(id) resolve to the row that
        # HELD the max — a documented SQLite guarantee for min/max aggregates,
        # not the undefined pick other engines make of a bare column
        last_notes = db.query(
            "SELECT task_id, MAX(id) AS id, note, created_at FROM task_worklog"  # noqa: S608 — the interpolation below emits bound marks only
            f" WHERE author = ? AND task_id IN ({','.join('?' * len(tasks))})"
            " GROUP BY task_id ORDER BY task_id",
            (agent, *[t["id"] for t in tasks]),
        )
    return {
        "agent": agent,
        "delegated_tasks": tasks,
        "open_questions": questions,
        "rejected_proposals": rejected,
        "notifications": notifications,
        "last_progress": last_notes,
    }
