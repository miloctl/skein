"""A routine's acceptances are counted apart from hand work, and they build
no streak (docs/intent/routines.md D11)."""

from datetime import datetime

import pytest


@pytest.fixture
def team(fresh_db, monkeypatch):
    from app import config
    from app.services import users

    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")


def _accept(task_id: int) -> None:
    from app.services import delegation, review, scope

    delegation.claim_task(task_id, actor="scout")
    pid = delegation.submit_completion(task_id, "done", actor="scout")["proposal_id"]
    review.approve_change(pid, actor="mira", strong=True, viewer=scope.Viewer("mira", True))


def _fire(fresh_db, rid: int) -> int:
    from app.services import routines

    due = datetime.fromisoformat(
        fresh_db.query_one("SELECT next_at FROM routines WHERE id = ?", (rid,))["next_at"]
    )
    assert routines.fire_due(rid, due) == "fired"
    return fresh_db.query_one(
        "SELECT id FROM tasks WHERE routine_id = ? ORDER BY id DESC LIMIT 1", (rid,)
    )["id"]


def _completion_row():
    from app.services import delegation

    return next(r for r in delegation.trust_scores() if r["entity"] == "task_completion")


def test_a_routine_acceptance_is_counted_apart(team, fresh_db):
    from app.services import delegation, routines, work

    rid = routines.create_routine(
        {"title": "Sweep", "weekdays": "1", "at_time": "07:00", "agent": "scout"}, actor="mira"
    )["id"]
    _accept(_fire(fresh_db, rid))
    hand = work.create_task("By hand", actor="mira")["id"]
    delegation.delegate_task(hand, "scout", "mira", actor="mira")
    _accept(hand)
    row = _completion_row()
    assert (row["proposed"], row["approved"]) == (1, 1)
    assert (row["routine_approved"], row["routine_rejected"]) == (1, 0)
    # the tasks keep routine_id, so a deleted routine's acceptance stays apart
    routines.delete_routine(rid, actor="mira")
    assert _completion_row()["routine_approved"] == 1


def test_routine_acceptances_build_no_streak(team, fresh_db):
    from app.services import routines

    rid = routines.create_routine(
        {"title": "Sweep", "weekdays": "1", "at_time": "07:00", "agent": "scout"}, actor="mira"
    )["id"]
    for _ in range(5):
        _accept(_fire(fresh_db, rid))
    row = _completion_row()
    assert row["routine_approved"] == 5
    assert (row["recent_streak"], row["last_verified_verdict"]) == (0, "")
