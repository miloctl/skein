"""Blockers: raising, resolving, the escalation sweep, and the funeral."""

from datetime import UTC

import pytest

from app import db


def test_raise_blocker_bad_task_id_is_valueerror(fresh_db):
    from app.services import blockers

    with pytest.raises(ValueError, match="no task #999"):
        blockers.raise_blocker("stuck", task_id=999)


def test_resolve_blocker_unblocks_linked_task(fresh_db):
    from app.services import blockers, work

    t = work.create_task("build it")
    b = blockers.raise_blocker("stuck", task_id=t["id"])
    assert work.list_tasks(status="blocked")[0]["id"] == t["id"]
    blockers.resolve_blocker(b["id"])
    assert work.list_tasks()[0]["status"] == "in_progress"
    events = fresh_db.query("SELECT event_type, payload FROM extension_outbox ORDER BY seq")
    assert [row["event_type"] for row in events] == [
        "skein.task.created",
        "skein.task.updated",
        "skein.blocker.created",
        "skein.task.updated",
        "skein.blocker.updated",
    ]
    assert '"status"' in events[-1]["payload"]

    # services/scope.py::missing — one wording for the absent row and for the
    # row this caller may not read, so neither answers "does #999 exist"
    with pytest.raises(db.NotFound, match="no blocker #999"):
        blockers.resolve_blocker(999)


def test_blocker_funeral_after_three_days(fresh_db, monkeypatch):
    from datetime import datetime, timedelta

    from app.services import blockers, notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    b = blockers.raise_blocker("ancient blocker", escalate_after_hours=999)
    old = (datetime.now(UTC) - timedelta(days=4)).isoformat(timespec="seconds")
    fresh_db.execute("UPDATE blockers SET created_at = ? WHERE id = ?", (old, b["id"]))
    blockers.resolve_blocker(b["id"])
    msgs = [n["message"] for n in notifications.list_notifications("team")]
    assert any("Here lies" in m for m in msgs)


def test_sweep_returns_post_flip_state(client):
    from app.services import collab

    client.post("/api/decisions", json={"title": "T", "decision": "D", "review_by": "2026-01-01"})
    swept = collab.sweep_stale_decisions()
    assert swept and all(d["status"] == "stale" for d in swept)


def test_blocker_relationship_cannot_publish_a_private_task_id(fresh_db):
    from app.services import blockers, scope, users, work

    users.ensure_user("mira")
    task_id = work.create_task(
        "Private blocked work",
        actor="mira",
        visibility=scope.PRIVATE,
    )["id"]

    with pytest.raises(ValueError, match="blocker cannot be visible to more people"):
        blockers.raise_blocker("Published blocker", task_id=task_id, actor="mira")

    blocker_id = blockers.raise_blocker("Legacy blocker", actor="mira")["id"]
    fresh_db.execute(
        "UPDATE blockers SET task_id = ? WHERE id = ?",
        (task_id, blocker_id),
    )
    assert blockers.list_blockers()[0]["task_id"] is None
    assert blockers.list_blockers(viewer=scope.Viewer("mira", True))[0]["task_id"] == task_id


def test_the_escalation_sweep_announces_the_state_it_changed(fresh_db):
    """The sweep owns the open-to-escalated transition. A subscriber tracking
    blocker state has no other way to learn it happened."""
    from app.services import blockers

    b = blockers.raise_blocker("stuck", escalate_after_hours=1)
    # raise_blocker reads 0 as "use the default", so age the row instead
    fresh_db.execute(
        "UPDATE blockers SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
        (b["id"],),
    )
    fresh_db.execute("DELETE FROM extension_outbox")

    blockers.sweep_escalations()

    events = fresh_db.query("SELECT event_type FROM extension_outbox")
    assert [row["event_type"] for row in events] == ["skein.blocker.updated"]
    assert (
        fresh_db.query_one("SELECT status FROM blockers WHERE id = ?", (b["id"],))["status"]
        == "escalated"
    )


def test_edit_blocker_impact_moves_the_escalation_clock(client, fresh_db):
    """Impact was frozen at creation, so every web-filed blocker escalated at
    capture's default `medium` speed — and impact is the field that SETS the
    clock (DEFAULT_ESCALATION_HOURS)."""
    from app import db

    bid = client.post("/api/blockers", json={"title": "vendor stuck"}).json()["id"]
    out = client.patch(f"/api/blockers/{bid}", json={"impact": "critical"}).json()
    assert "impact" in out["updated"]
    row = db.query_one("SELECT impact, escalate_after_hours FROM blockers WHERE id = ?", (bid,))
    assert row["impact"] == "critical"
    assert row["escalate_after_hours"] == 2  # critical's default, not medium's 24


def test_edit_blocker_impact_keeps_a_custom_deadline(fresh_db):
    """A creator who set their own escalate_after_hours said something the
    impact default must not overwrite."""
    from app import db
    from app.services import blockers

    bid = blockers.raise_blocker("custom clock", escalate_after_hours=5, actor="tester")["id"]
    blockers.edit_blocker(bid, impact="critical", actor="tester")
    row = db.query_one("SELECT impact, escalate_after_hours FROM blockers WHERE id = ?", (bid,))
    assert row["impact"] == "critical" and row["escalate_after_hours"] == 5


def test_edit_blocker_refuses_an_unknown_impact(client, fresh_db):
    bid = client.post("/api/blockers", json={"title": "x"}).json()["id"]
    r = client.patch(f"/api/blockers/{bid}", json={"impact": "urgent"})
    assert r.status_code == 400
    assert "urgent" not in r.json()["detail"]  # errors never echo the rejected value
