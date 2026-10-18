"""Two transactions that lock the same rows in opposite orders deadlock.

PostgreSQL detects the cycle and aborts one side, which answers 503
(db.BUSY_ERRORS) - so the failure is a refused request, never a hang, and a
test that races two transactions is a flaky test. These pin the ORDER
instead: the statement order inside one transaction is its lock order.
"""

import re

from app import db
from app.services import retention, users, work


def _row_locks(monkeypatch) -> list[tuple[str, int]]:
    """(table, id) for every row-locking SELECT, first acquisition only.
    db.query_one goes through db.query, so one spy sees both."""
    taken: list[tuple[str, int]] = []
    lock = re.compile(r"FROM (\w+) WHERE id = \?.* FOR (?:UPDATE|SHARE|KEY SHARE)$")
    real = db.query

    def spy(sql, params=()):
        m = lock.search(sql)
        if m and (m.group(1), params[0]) not in taken:
            taken.append((m.group(1), params[0]))
        return real(sql, params)

    monkeypatch.setattr(db, "query", spy)
    return taken


def test_a_week_plan_holds_its_tasks_in_id_order_and_no_parent(client, monkeypatch):
    """Two plans that name the same tasks in different orders must not each
    hold one task and wait for the other. A plan moves no link, so it holds
    no parent (work.hold_task_parents)."""
    ma = work.create_milestone("A", project="Apollo", actor="tester")["id"]
    mb = work.create_milestone("B", project="Apollo", actor="tester")["id"]
    ta = work.create_task("ta", milestone_id=ma, actor="tester")["id"]
    tb = work.create_task("tb", milestone_id=mb, actor="tester")["id"]
    taken = _row_locks(monkeypatch)
    r = client.post("/api/week/plan", json={"task_ids": [tb, ta]})
    assert r.status_code == 200, r.text
    assert taken == [("tasks", ta), ("tasks", tb)], taken


def test_an_approved_weekly_plan_holds_its_tasks_in_id_order(fresh_db, monkeypatch):
    """An approved proposal applies through weekly.apply_plan with nothing
    held, so it takes the same order as the direct plan it can cross."""
    from app.services import review, scope, weekly

    ta = work.create_task("ta", actor="tester")["id"]
    tb = work.create_task("tb", actor="tester")["id"]
    change = review.propose_change(
        "weekly_plan",
        "create",
        {"week": weekly.current_week(), "task_ids": [tb, ta]},
        actor="scheduler",
    )
    taken = _row_locks(monkeypatch)
    review.approve_change(change["id"], actor="ava", strong=True, viewer=scope.Viewer("ava", True))
    assert [row for row in taken if row[0] == "tasks"] == [("tasks", ta), ("tasks", tb)], taken


def test_a_task_update_holds_a_parent_only_when_it_moves_a_link(client, monkeypatch):
    """An atomic PATCH /api/milestones holds its milestone while it waits for
    a second worker thread. A task update that holds that milestone for a
    priority change fills the pool behind it, and every REST request in the
    process waits for lock_timeout."""
    ma = work.create_milestone("A", project="Apollo", actor="tester")["id"]
    mb = work.create_milestone("B", project="Apollo", actor="tester")["id"]
    task = work.create_task("t", milestone_id=ma, actor="tester")["id"]
    taken = _row_locks(monkeypatch)
    r = client.patch(f"/api/tasks/{task}", json={"priority": "high"})
    assert r.status_code == 200, r.text
    assert [row for row in taken if row[0] != "tasks"] == [], taken
    r = client.patch(f"/api/tasks/{task}", json={"milestone_id": mb})
    assert r.status_code == 200, r.text
    assert ("milestones", mb) in taken, taken


def test_a_task_create_holds_its_engagement_before_its_milestone(fresh_db, monkeypatch):
    """update_engagement holds the engagement and then writes its milestones.
    A create that holds the milestone first meets the engagement in its
    insert's foreign key check, and each waits on the other."""
    from app.services import engagements

    eng = engagements.create_engagement("Atlas", actor="tester")["id"]
    ms = work.create_milestone("Gate", project="Atlas", actor="tester")["id"]
    taken = _row_locks(monkeypatch)
    work.create_task("t", milestone_id=ms, engagement_id=eng, actor="tester")
    parents = [row for row in taken if row[0] in ("engagements", "milestones")]
    assert parents == [("engagements", eng), ("milestones", ms)], taken


def test_a_public_crew_create_holds_its_crew_before_its_milestone(fresh_db, monkeypatch):
    """work.create_task takes the crew row (scope.resolve_write) before the
    parents, and a crew-tier create_engagement holds the crew and then the
    orphan milestones. With the milestone first, the public door deadlocks
    with that adoption."""
    from app.extensions import ExtensionRegistry, JobExecutionContext
    from app.extensions.policy import PolicySubject
    from app.public import CreateTaskCommand, WorkItems
    from app.public.work import _bind_execution_context
    from app.services import crews

    users.ensure_user("ava")
    crew = crews.create_crew("Ops", actor="ava")["id"]
    ms = work.create_milestone("Orphan", project="Apollo", actor="ava")["id"]
    items = WorkItems(ExtensionRegistry.build(()).policy_engine)
    subject = PolicySubject("atlas-sync", kind="service")
    execution = _bind_execution_context(
        items,
        JobExecutionContext(
            policy=items._policy,
            work_items=items,
            subject=subject,
            run_id="r1",
            namespace="atlas.workplace.sync",
        ),
        subject=subject,
        namespace="atlas.workplace.sync",
        receipt_namespace="job:atlas.workplace.sync",
        correlation_id="r1",
        actor="ava",
        actor_kind="human",
        read_name="ava",
        read_strong=True,
    )
    taken = _row_locks(monkeypatch)
    items.create_task(
        CreateTaskCommand(title="t", milestone_id=ms, visibility="crew", crew_id=crew),
        execution.command_context(project_type=""),
    )
    assert taken.index(("crews", crew)) < taken.index(("milestones", ms)), taken


def test_the_prune_writes_shared_tables_in_rename_order(fresh_db, monkeypatch):
    """retention.prune is one transaction, and so is a rename. For the tables
    both write, the prune follows users._ATTRIBUTION, the order a plain
    rename walks."""
    from datetime import UTC, datetime, timedelta

    from app.services import review

    # one settled proposal past the horizon, or the pending_changes write
    # never runs and the order cannot be seen
    change = review.propose_change(
        "standup", "create", {"author": "ava", "yesterday": "x"}, actor="scout", requested_by="ava"
    )
    review.reject_change(change["id"], actor="ava", viewer=review.scope.Viewer("ava", True))
    old = (datetime.now(UTC) - timedelta(days=retention.DERIVED_COPY_DAYS + 1)).isoformat()
    db.execute("UPDATE pending_changes SET reviewed_at = ? WHERE id = ?", (old, change["id"]))
    written = []
    statement = re.compile(r"^\s*(?:DELETE\s+FROM|UPDATE)\s+(\w+)", re.I)

    def spying(name):
        real = getattr(db, name)

        def spy(sql, *args, **kwargs):
            m = statement.match(sql)
            if m and (not written or written[-1] != m.group(1)):
                written.append(m.group(1))
            return real(sql, *args, **kwargs)

        monkeypatch.setattr(db, name, spy)

    for name in ("execute", "execute_rowcount", "query"):
        spying(name)
    retention.prune(actor="tester")
    rank = {table: i for i, table in enumerate(users._ATTRIBUTION)}
    shared = [table for table in written if table in rank]
    assert {"pending_changes", "notifications", "browser_sessions"} <= set(shared), written
    assert shared == sorted(shared, key=rank.__getitem__), shared
