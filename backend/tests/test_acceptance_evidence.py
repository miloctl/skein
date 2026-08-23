"""What a sponsor is shown at the verdict point, and what nobody else is.

The evidence block puts a task's state and the agent's own worklog beside the
Approve button. Both are the task's own text, so this file pins the doors: the
task read takes the viewer's tier filter, and the worklog door opens for the
sponsor alone — passing the PROPOSER's name there would open a crew worklog to
whoever happened to load the queue.
"""

import pytest

from app.services import crews, delegation, review, scope, users, work


@pytest.fixture
def sponsored(fresh_db):
    """A crew task delegated to an agent, submitted for acceptance."""
    for name in ("sponsor", "outsider", "member"):
        users.ensure_user(name)
    crew = crews.create_crew("ops", actor="sponsor")
    crews.add_member(crew["id"], "member", actor="sponsor")
    task = work.create_task(
        "Summarize the pricing pages",
        actor="sponsor",
        visibility=scope.CREW,
        crew_id=crew["id"],
    )
    delegation.delegate_task(task["id"], "research-agent", sponsor="sponsor", actor="sponsor")
    delegation.claim_task(task["id"], actor="research-agent")
    delegation.report_progress(task["id"], "pulled 4 of 6 pages", actor="research-agent")
    delegation.submit_completion(task["id"], "all six done", actor="research-agent")
    return {"crew": crew["id"], "task": task["id"]}


def _evidence(viewer_name: str, strong: bool = True) -> dict:
    viewer = scope.Viewer(viewer_name, strong)
    rows = review.list_changes("pending", viewer)
    accepts = [r for r in rows if r["entity"] == "task_completion"]
    return accepts[0].get("evidence", {}) if accepts else {}


def test_the_sponsor_reads_the_worklog_beside_the_verdict(sponsored):
    ev = _evidence("sponsor")
    assert ev["title"] == "Summarize the pricing pages"
    assert [w["note"] for w in ev["worklog"]] == ["pulled 4 of 6 pages"]


def test_a_reader_outside_the_crew_gets_no_task_text(sponsored):
    """The proposal row can still list — its summary passes `_readable` on its
    own terms — but the task's title and worklog are a SECOND read of a scoped
    row and take their own filter."""
    assert _evidence("outsider") == {}


def test_a_crew_member_who_is_not_the_sponsor_reads_no_worklog(sponsored):
    """The tier admits them to the task. The delegation door does not: the
    door is `actor in (delegated_agent, sponsor)`, resolved from the VIEWER's
    name — passing the proposer's name would open it for every reader."""
    ev = _evidence("member")
    assert ev["title"] == "Summarize the pricing pages"
    # crew-tier worklog rows are readable by the crew, so this member sees the
    # note through the TIER, never through the delegation door
    assert all(w["author"] == "research-agent" for w in ev["worklog"])


def test_the_queue_never_emits_an_empty_evidence_object(sponsored):
    """The KEY is absent or the block is whole — never `{}`.

    `{}` is truthy in JavaScript, so the renderer's `c.evidence ? …` guard
    passes and the component reads `.length` of an absent worklog. One
    unreadable task would take down the entire Approvals list, which is the
    surface a reviewer uses to clear that proposal.
    """
    for viewer in ("sponsor", "member", "outsider"):
        for row in review.list_changes("pending", scope.Viewer(viewer, True)):
            if "evidence" in row:
                assert row["evidence"], f"{viewer} got an empty evidence object"
                assert "worklog" in row["evidence"]


def test_a_handover_between_submit_and_verdict_is_named(sponsored):
    """Authority follows the CURRENT sponsor by design. A verdict that moved
    to somebody who never watched the work is a receipt the reviewer needs."""
    assert _evidence("sponsor").get("sponsor_was") == ""

    users.ensure_user("stand-in")
    # a sponsor must be able to READ the task they judge, so the stand-in joins
    # the crew first (services/delegation.py::delegate_task refuses otherwise)
    crews.add_member(sponsored["crew"], "stand-in", actor="sponsor")
    delegation.delegate_task(
        sponsored["task"], "research-agent", sponsor="stand-in", actor="sponsor"
    )
    ev = _evidence("stand-in")
    assert ev["sponsor_was"] == "sponsor"


def test_criteria_row_references_show_their_current_state(fresh_db):
    """The rows a criterion names, resolved to their state at verdict time.

    Deterministic display beside the verdict, never a verdict: the sponsor
    still clicks. A named row the viewer cannot see comes back with an empty
    state — the same one sentence an absent row gets."""
    from app.services import blockers

    users.ensure_user("sponsor")
    blocker = blockers.raise_blocker("gpu quota", actor="sponsor")
    named = work.create_task("named dependency", actor="sponsor")
    task = work.create_task("build the harness", actor="sponsor")
    delegation.delegate_task(
        task["id"],
        "research-agent",
        sponsor="sponsor",
        actor="sponsor",
        acceptance_criteria=(
            f"blocker #{blocker['id']} resolved, task #{named['id']} done,"
            f" task #9999 handled, PR #42 merged"
        ),
    )
    delegation.claim_task(task["id"], actor="research-agent")
    delegation.submit_completion(task["id"], "done", actor="research-agent")

    ev = _evidence("sponsor")
    states = {(r["entity"], r["id"]): r["state"] for r in ev["criteria_refs"]}
    assert states[("blocker", blocker["id"])] == "open"
    assert states[("task", named["id"])] == "todo"
    assert states[("task", 9999)] == ""  # absent row, empty state
    assert ("pr", 42) not in states  # unknown word is not a reference

    work.update_task(named["id"], status="done", actor="sponsor")
    ev = _evidence("sponsor")
    states = {(r["entity"], r["id"]): r["state"] for r in ev["criteria_refs"]}
    assert states[("task", named["id"])] == "done"  # state as of THIS read


def test_workplace_policy_can_hide_a_visible_criterion_reference(fresh_db):
    users.ensure_user("sponsor")
    dependency = work.create_task("policy-hidden dependency", actor="sponsor")
    task = work.create_task("visible acceptance", actor="sponsor")
    delegation.delegate_task(
        task["id"],
        "research-agent",
        sponsor="sponsor",
        acceptance_criteria=f"task #{dependency['id']} done",
        actor="sponsor",
    )
    delegation.claim_task(task["id"], actor="research-agent")
    delegation.submit_completion(task["id"], "done", actor="research-agent")
    seen = []

    def policy(entity, entity_id, _attributes):
        seen.append((entity, entity_id))
        return not (entity == "task" and entity_id == dependency["id"])

    rows = review.list_changes("pending", scope.Viewer("sponsor", True), resource_filter=policy)
    acceptance = next(row for row in rows if row["entity"] == "task_completion")
    assert acceptance["evidence"]["criteria_refs"] == [
        {"entity": "task", "id": dependency["id"], "state": ""}
    ]
    assert ("task", dependency["id"]) in seen


def test_criteria_status_reads_are_batched_by_entity(fresh_db, monkeypatch):
    users.ensure_user("sponsor")
    dependencies = [work.create_task(f"dependency {i}", actor="sponsor") for i in range(6)]
    task = work.create_task("batched acceptance", actor="sponsor")
    delegation.delegate_task(
        task["id"],
        "research-agent",
        sponsor="sponsor",
        acceptance_criteria=", ".join(f"task #{row['id']} done" for row in dependencies),
        actor="sponsor",
    )
    delegation.claim_task(task["id"], actor="research-agent")
    delegation.submit_completion(task["id"], "done", actor="research-agent")
    original = review.db.query
    status_reads = []

    def count(sql, params=()):
        if "SELECT id, status FROM tasks WHERE id IN" in sql:
            status_reads.append(tuple(params))
        return original(sql, params)

    monkeypatch.setattr(review.db, "query", count)
    rows = review.list_changes("pending", scope.Viewer("sponsor", True))
    acceptance = next(row for row in rows if row["entity"] == "task_completion")
    assert len(acceptance["evidence"]["criteria_refs"]) == 6
    assert len(status_reads) == 1


def test_a_criterion_naming_a_hidden_row_does_not_confirm_it(fresh_db):
    """A visible acceptance can name a hidden dependency without confirming it."""
    for name in ("sponsor", "member"):
        users.ensure_user(name)
    crew = crews.create_crew("secret ops", actor="member")
    hidden = work.create_task(
        "crew secret",
        actor="member",
        visibility=scope.CREW,
        crew_id=crew["id"],
    )
    task = work.create_task("visible acceptance", actor="sponsor")
    delegation.delegate_task(
        task["id"],
        "research-agent",
        sponsor="sponsor",
        acceptance_criteria=f"task #{hidden['id']} done",
        actor="sponsor",
    )
    delegation.claim_task(task["id"], actor="research-agent")
    delegation.submit_completion(task["id"], "done", actor="research-agent")
    refs = _evidence("sponsor")["criteria_refs"]
    assert refs == [{"entity": "task", "id": hidden["id"], "state": ""}]


def test_the_service_bounds_acceptance_criteria_on_every_door(fresh_db):
    """The REST door capped this and the tool/MCP doors did not, so the
    Approvals card rendered unbounded text."""
    users.ensure_user("sponsor")
    task = work.create_task("bounded", actor="sponsor")
    with pytest.raises(ValueError, match="1000 characters"):
        delegation.delegate_task(
            task["id"],
            "research-agent",
            sponsor="sponsor",
            actor="sponsor",
            acceptance_criteria="x" * 1001,
        )


def test_only_the_sponsor_closes_delegated_work(sponsored):
    """The agent half of this guard existed and the human half did not, so any
    teammate who could reach PATCH /api/tasks/{id} closed delegated work with
    one field — no verdict, no reason on record, no trust signal."""
    with pytest.raises(PermissionError, match="sponsored by sponsor"):
        work.update_task(sponsored["task"], status="done", actor="member")

    # the sponsor's own hand is not blocked: the verdict is theirs either way
    work.update_task(sponsored["task"], status="done", actor="sponsor")
    assert work.get_task(sponsored["task"], scope.Viewer("sponsor", True))["status"] == "done"
