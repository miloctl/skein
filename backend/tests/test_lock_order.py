"""Two transactions that lock the same rows in opposite orders deadlock.

PostgreSQL detects the cycle and aborts one side, which answers 503
(db.BUSY_ERRORS) - so the failure is a refused request, never a hang, and a
test that races two transactions is a flaky test. These pin the ORDER
instead: the statement order inside one transaction is its lock order.
"""

import re

from app import db
from app.services import policy_context, retention, users, work


def test_a_week_plan_holds_its_tasks_in_id_order(client, monkeypatch):
    """Two plans that name the same tasks in different orders must not each
    hold one task and wait for the other."""
    ids = [work.create_task(f"plan {n}", actor="tester")["id"] for n in range(3)]
    held = []
    real = policy_context.hold_resource

    def spy(entity, entity_id):
        if entity == "task":
            held.append(entity_id)
        real(entity, entity_id)

    monkeypatch.setattr(policy_context, "hold_resource", spy)
    r = client.post("/api/week/plan", json={"task_ids": [ids[2], ids[0], ids[1]]})
    assert r.status_code == 200, r.text
    assert held == sorted(ids)


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
