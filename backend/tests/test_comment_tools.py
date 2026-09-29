"""The agent side of comment threads (docs/intent/task-threads.md D4, D5).
A delegate answers on its own open task with no review. Every other agent
comment goes through the gate, and the unattended inbox lists what the agent
has not answered yet."""

import json

import pytest
from conftest import _turn

from app import config, db, ratelimit
from app.agents.identity import (
    reset_agent_identity,
    set_agent_identity,
    set_force_review,
)
from app.services import collab, comments, crews, delegation, users, work


def _tool(name: str):
    from app.tools import portfolio

    fn = getattr(portfolio, name)
    return getattr(fn, "_tool_func", None) or fn.__wrapped__


def _as(agent: str, name: str, **kwargs) -> dict:
    token = set_agent_identity(agent)
    try:
        return json.loads(_tool(name)(**kwargs))
    finally:
        reset_agent_identity(token)


def _delegated(**tier) -> int:
    for name in ("ava", "raj"):
        users.ensure_user(name)
    users.ensure_user("scout", kind="agent")
    task = work.create_task("Fix login", actor="ava", **tier)["id"]
    delegation.delegate_task(task, "scout", "ava", actor="ava", origin="human")
    db.execute("DELETE FROM agent_wakeups")
    return task


def _proposals() -> list[dict]:
    return db.query(
        "SELECT id, entity, status, payload FROM pending_changes WHERE entity = 'comment'"
    )


def test_the_delegate_answers_on_its_own_task_with_no_review(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    task = _delegated()
    out = _as("scout", "post_comment", body="Done: the target is now 17.", task_id=task)
    assert "error" not in out, out
    row = db.query_one("SELECT created_by, origin FROM comments WHERE id = ?", (out["id"],))
    assert (row["created_by"], row["origin"]) == ("scout", "agent")
    assert _proposals() == []


@pytest.mark.parametrize("entity", ["task", "comment"])
def test_forbidden_stops_the_delegates_comment(fresh_db, entity):
    task = _delegated()
    delegation.set_authority("scout", entity, "forbidden", actor="ava")
    out = _as("scout", "post_comment", body="hello", task_id=task)
    assert "forbidden" in out["error"]
    assert db.query_one("SELECT 1 FROM comments") is None


def test_a_consulted_agent_does_not_comment(fresh_db):
    task = _delegated()
    set_force_review(True)
    try:
        out = _as("scout", "post_comment", body="hello", task_id=task)
    finally:
        set_force_review(False)
    assert "asked for an opinion" in out["error"]
    assert db.query_one("SELECT 1 FROM comments") is None


def test_the_direct_path_spends_the_write_bucket(fresh_db, monkeypatch):
    monkeypatch.setitem(ratelimit.LIMITS, "write", 1)
    ratelimit.reset()
    task = _delegated()
    assert "error" not in _as("scout", "post_comment", body="one", task_id=task)
    assert "error" in _as("scout", "post_comment", body="two", task_id=task)
    assert db.query_one("SELECT COUNT(*) AS n FROM comments")["n"] == 1


def test_a_chat_turn_files_a_comment_on_another_task_for_review(fresh_db, monkeypatch):
    from app.main import create_app
    from app.services import review

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("scout", kind="agent")
    users.ensure_user("ava")
    task = work.create_task("Someone else's task", actor="ava")["id"]
    with _turn("ava"):
        out = _as("scout", "post_comment", body="Is this still needed?", task_id=task)
    assert out.get("status") == "pending", out
    assert db.query_one("SELECT 1 FROM comments") is None
    proposal = _proposals()[0]
    from app.services import scope

    review.approve_change(
        proposal["id"],
        actor="ava",
        strong=True,
        viewer=scope.Viewer("ava", True),
        policy_registry=create_app().state.skein_registry,
    )
    row = db.query_one("SELECT created_by, origin, body FROM comments WHERE task_id = ?", (task,))
    assert (row["created_by"], row["origin"]) == ("scout", "agent_verified")


def test_what_a_chat_turn_cannot_comment_on_files_nothing(fresh_db, monkeypatch):
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    for name in ("ava", "mira"):
        users.ensure_user(name)
    users.ensure_user("scout", kind="agent")
    crew = crews.create_crew("Platform", actor="ava")["id"]
    crew_task = work.create_task("Crew task", actor="ava", visibility="crew", crew_id=crew)["id"]
    crew_decision = collab.record_decision(
        "Crew call", "x", actor="ava", visibility="crew", crew_id=crew
    )["id"]
    private = work.create_task("Mine", actor="ava", visibility="private")["id"]
    with _turn("ava"):
        crew_out = _as("scout", "post_comment", body="x", task_id=crew_task)
        decision_out = _as("scout", "post_comment", body="x", decision_id=crew_decision)
        private_out = _as("scout", "post_comment", body="x", task_id=private)
    assert "crew" in crew_out["error"] and "crew" in decision_out["error"]
    assert private_out["error"] == (
        "An agent cannot comment on a private record. Write the comment yourself."
    )
    assert _proposals() == [] and db.query_one("SELECT 1 FROM comments") is None


def test_with_nobody_asking_an_agent_comments_only_on_its_own_task(fresh_db):
    _delegated()
    other = work.create_task("Not scout's", actor="ava")["id"]
    out = _as("scout", "post_comment", body="hello", task_id=other)
    assert "delegated to you" in out["error"]
    assert _proposals() == [] and db.query_one("SELECT 1 FROM comments") is None


def test_the_inbox_lists_a_comment_until_the_agent_answers(fresh_db):
    task = _delegated()
    first = comments.add_comment("@scout target is 17", task_id=task, actor="ava")["id"]
    listed = delegation.agent_inbox("scout")["new_comments"]
    assert [(c["id"], c["task_id"], c["created_by"]) for c in listed] == [(first, task, "ava")]
    delegation.report_progress(task, "updated the target", actor="scout")
    assert delegation.agent_inbox("scout")["new_comments"] == []
    # db.now() has seconds: the answer must be older than the edit below
    db.execute("UPDATE task_worklog SET created_at = '2026-01-01T00:00:00+00:00'")
    # an edit after the answer asks again
    comments.edit_comment(first, "@scout target is 18", actor="ava")
    assert [c["body"] for c in delegation.agent_inbox("scout")["new_comments"]] == [
        "@scout target is 18"
    ]
    _as("scout", "post_comment", body="On it.", task_id=task)
    assert delegation.agent_inbox("scout")["new_comments"] == []


def test_the_rest_inbox_carries_no_comment_text(client, fresh_db):
    from conftest import _strong

    task = _delegated()
    comments.add_comment("secret plan", task_id=task, actor="ava")
    body = client.get("/api/agents/scout/inbox", headers=_strong(client, "ava")).json()
    assert "secret plan" not in json.dumps(body)


def test_the_delegate_reads_a_crew_task_thread_it_cannot_read_by_tier(fresh_db):
    users.ensure_user("ava")
    crew = crews.create_crew("Platform", actor="ava")["id"]
    task = _delegated(visibility="crew", crew_id=crew)
    comments.add_comment("crew steer", task_id=task, actor="ava")
    out = _as("scout", "read_comments", task_id=task)
    assert [c["body"] for c in out["comments"]] == ["crew steer"]


def test_a_rule_on_the_tasks_project_refuses_the_agents_comment(fresh_db, monkeypatch):
    """A gated comment is judged on its thread's parent, so a workplace rule
    on a project governs the comments on that project's tasks."""
    from app.extensions import PolicyContribution, PolicyDecision, PolicyEffect, SkeinModule
    from app.extensions.policy import reset_policy_engine, set_policy_engine
    from app.extensions.registry import ExtensionRegistry
    from app.services import engagements

    monkeypatch.setattr(config, "AGENT_REVIEW", True)

    def deny(request):
        if request.resource.type == "comment" and request.resource.project_type == "prototype":
            return PolicyDecision(PolicyEffect.DENY, ("prototype threads are closed",))
        return None

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.7.0",
        policies=(PolicyContribution("acme.workplace.threads", deny),),
    )
    users.ensure_user("ava")
    users.ensure_user("scout", kind="agent")
    engagement = engagements.create_engagement("Atlas", project_class="prototype", actor="ava")
    task = work.create_task("Prototype task", engagement_id=engagement["id"], actor="ava")["id"]
    engine = set_policy_engine(ExtensionRegistry.build((module,)).policy_engine)
    try:
        with _turn("ava"):
            out = _as("scout", "post_comment", body="Is this needed?", task_id=task)
    finally:
        reset_policy_engine(engine)
    assert "error" in out, out
    assert _proposals() == [] and db.query_one("SELECT 1 FROM comments") is None


def test_a_pending_comment_takes_its_parents_tier(fresh_db):
    """A proposed comment names its thread in the payload, not in entity_id.
    Read from neither, a non-member read and approved a reply on a crew
    decision they cannot see."""
    from app.services import review, scope

    for name in ("ava", "mallory"):
        users.ensure_user(name)
    users.ensure_user("scout", kind="agent")
    crew = crews.create_crew("Alpha", actor="ava")["id"]
    decision = collab.record_decision(
        "Crew call", "x", actor="ava", visibility="crew", crew_id=crew
    )
    pid = review.propose_change(
        "comment",
        "create",
        {"body": "SECRET reply", "decision_id": decision["id"]},
        summary="comment",
        actor="scout",
    )["id"]
    listed = [c["id"] for c in review.list_changes(viewer=scope.Viewer("mallory", True))]
    assert pid not in listed
    assert pid in [c["id"] for c in review.list_changes(viewer=scope.Viewer("ava", True))]
    with pytest.raises(db.NotFound):
        review.approve_change(
            pid, actor="mallory", strong=True, viewer=scope.Viewer("mallory", True)
        )


def test_the_prune_keeps_the_thread_a_settled_comment_proposal_names(fresh_db):
    """Past the horizon a settled proposal loses its text but keeps the keys
    its tier comes from (retention._tier_keys). A comment names one of three
    parents, and without its key the record reads as the workspace tier."""
    from datetime import UTC, datetime, timedelta

    from app.services import retention, review

    users.ensure_user("ava")
    users.ensure_user("scout", kind="agent")
    decision = collab.record_decision("Call", "x", actor="ava")["id"]
    pid = review.propose_change(
        "comment", "create", {"body": "old text", "decision_id": decision}, actor="scout"
    )["id"]
    review.reject_change(pid, note="no", actor="ava", strong=True)
    old = (datetime.now(UTC) - timedelta(days=retention.DERIVED_COPY_DAYS + 1)).isoformat()
    db.execute("UPDATE pending_changes SET reviewed_at = ? WHERE id = ?", (old, pid))
    retention.prune()
    payload = json.loads(
        db.query_one("SELECT payload FROM pending_changes WHERE id = ?", (pid,))["payload"]
    )
    assert payload == {"decision_id": decision}
