"""Blockers: raising, resolving, the escalation sweep, and the funeral."""

from datetime import UTC

import pytest


def test_raise_blocker_bad_task_id_is_valueerror(fresh_db):
    from app.services import blockers

    with pytest.raises(ValueError, match="task #999 not found"):
        blockers.raise_blocker("stuck", task_id=999)


def test_resolve_blocker_unblocks_linked_task(fresh_db):
    from app.services import blockers, work

    t = work.create_task("build it")
    b = blockers.raise_blocker("stuck", task_id=t["id"])
    assert work.list_tasks(status="blocked")[0]["id"] == t["id"]
    blockers.resolve_blocker(b["id"])
    assert work.list_tasks()[0]["status"] == "in_progress"

    with pytest.raises(ValueError, match="not found"):
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
