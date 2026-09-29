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
    assert out["error"].startswith("Policy denied this write."), out
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


def _shared_then_delegated(first_tier: dict) -> tuple[int, int]:
    """A task written narrow, commented on, then shared with the team and
    delegated: a comment keeps the tier it was written at (D2)."""
    from app.services import sharing

    for name in ("ava", "bo"):
        users.ensure_user(name)
    users.ensure_user("scout", kind="agent")
    task = work.create_task("Plan", actor="ava", **first_tier)["id"]
    narrow = comments.add_comment("NARROW salary numbers", task_id=task, actor="ava")["id"]
    sharing.share_with_team("tasks", task, actor="ava")
    comments.add_comment("team update", task_id=task, actor="ava")
    delegation.delegate_task(task, "scout", "ava", actor="ava", origin="human")
    return task, narrow


@pytest.mark.parametrize("tier", ["private", "crew"])
def test_the_delegates_door_never_opens_a_comment_narrower_than_the_task(fresh_db, tier):
    """The door exists because an agent holds no crews, not to widen what a
    comment was written for. Through `/as scout`, bo read ava's private
    comment, and the unattended inbox sent it to the model."""
    first = {"visibility": "private"}
    if tier == "crew":
        users.ensure_user("ava")
        first = {"visibility": "crew", "crew_id": crews.create_crew("Alpha", actor="ava")["id"]}
    task, _ = _shared_then_delegated(first)
    unattended = _as("scout", "read_comments", task_id=task)
    assert [c["body"] for c in unattended["comments"]] == ["team update"]
    with _turn("bo"):
        driven = _as("scout", "read_comments", task_id=task)
    assert [c["body"] for c in driven["comments"]] == ["team update"]
    assert [c["body"] for c in delegation.agent_inbox("scout")["new_comments"]] == ["team update"]


def test_the_worklog_door_never_opens_a_note_narrower_than_the_task(fresh_db):
    """The same door on the worklog: a note written while the task was a
    crew's stays the crew's after the task is shared."""
    from app.services import sharing

    for name in ("ava", "bo"):
        users.ensure_user(name)
    users.ensure_user("scout", kind="agent")
    crew = crews.create_crew("Alpha", actor="ava")["id"]
    task = work.create_task("Plan", actor="ava", visibility="crew", crew_id=crew)["id"]
    delegation.delegate_task(task, "scout", "ava", actor="ava", origin="human")
    delegation.report_progress(task, "crew-only finding", actor="scout")
    sharing.share_with_team("tasks", task, actor="ava")
    with _turn("bo"):
        notes = _as("scout", "read_worklog", task_id=task)
    assert notes["worklog"] == []


def test_read_comments_returns_the_newest_in_order(fresh_db):
    """The wake prompt sends the agent to the thread for the NEW comment, and
    a limit that kept the oldest dropped exactly that one."""
    task = _delegated()
    for n in range(3):
        comments.add_comment(f"comment {n}", task_id=task, actor="ava")
    out = _as("scout", "read_comments", task_id=task, limit=2)
    assert [c["body"] for c in out["comments"]] == ["comment 1", "comment 2"]


def test_a_crew_thread_read_marks_the_turn(fresh_db):
    """A later write in the same turn must not carry crew text into a
    proposal the whole team reviews (tools/_gate.py)."""
    from app.agents.identity import read_scoped_this_turn

    users.ensure_user("ava")
    crew = crews.create_crew("Alpha", actor="ava")["id"]
    task = _delegated(visibility="crew", crew_id=crew)
    comments.add_comment("crew steer", task_id=task, actor="ava")
    with _turn("ava"):
        _as("scout", "read_comments", task_id=task)
        assert read_scoped_this_turn() is True


def test_deleting_the_agents_answer_asks_again(fresh_db):
    task = _delegated()
    asked = comments.add_comment("@scout which target?", task_id=task, actor="ava")["id"]
    answer = _as("scout", "post_comment", body="17.", task_id=task)["id"]
    assert delegation.agent_inbox("scout")["new_comments"] == []
    comments.delete_comment(answer, actor="ava")
    assert [c["id"] for c in delegation.agent_inbox("scout")["new_comments"]] == [asked]


def test_deleting_an_approved_agent_comment_clears_its_proposal_text(fresh_db, monkeypatch):
    from app.main import create_app
    from app.services import review, scope

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("ava")
    users.ensure_user("scout", kind="agent")
    task = work.create_task("Someone else's task", actor="ava")["id"]
    with _turn("ava"):
        _as("scout", "post_comment", body="SECRET guess", task_id=task)
    pid = _proposals()[0]["id"]
    review.approve_change(
        pid,
        actor="ava",
        strong=True,
        viewer=scope.Viewer("ava", True),
        policy_registry=create_app().state.skein_registry,
    )
    cid = db.query_one("SELECT id FROM comments")["id"]
    comments.delete_comment(cid, actor="ava")
    payload = db.query_one("SELECT payload FROM pending_changes WHERE id = ?", (pid,))["payload"]
    assert "SECRET" not in payload
    assert json.loads(payload)["task_id"] == task


def test_every_ungated_writer_is_governed_by_workplace_policy():
    """The gate is where workplace policy decides an agent write. A writer
    that skips it on purpose must be wrapped instead (agents/core_tools.py),
    or a project rule that stops report_progress lets the same delegate post
    a comment on the same task."""
    from test_gate_coverage import UNGATED_WRITERS

    from app.agents.core_tools import SPECIALIZED_WRITE_TOOLS

    assert set(UNGATED_WRITERS) == SPECIALIZED_WRITE_TOOLS


def test_the_wake_prompt_names_only_tools_the_wake_turn_holds():
    import re

    from app.services.agent_runner import _WAKE
    from app.services.agent_wakeups import WAKE_TOOLS
    from app.tools import ALL_TOOLS

    known = {getattr(t, "tool_name", getattr(t, "__name__", "")) for t in ALL_TOOLS}
    named = {word for word in re.findall(r"[a-z_]+", _WAKE) if word in known}
    assert {"read_comments", "post_comment", "my_agent_inbox"} <= named
    assert named <= WAKE_TOOLS


def test_a_crew_blocker_takes_no_gated_comment(fresh_db, monkeypatch):
    from app.services import blockers

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("ava")
    users.ensure_user("scout", kind="agent")
    crew = crews.create_crew("Alpha", actor="ava")["id"]
    blocker = blockers.raise_blocker("crew blocker", actor="ava", visibility="crew", crew_id=crew)[
        "id"
    ]
    with _turn("ava"):
        out = _as("scout", "post_comment", body="x", blocker_id=blocker)
    assert "crew" in out["error"]
    assert _proposals() == []
