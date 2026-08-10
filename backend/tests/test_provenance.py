"""The chain behind a row: how it was made, who judged it, what since.

`origin` is a label. The reason to trust a row — or to look again — is the
chain, and every link was already stored in a different table with no surface
that put them together.
"""

import pytest

from app.services import provenance, scope


def test_an_approved_proposal_names_its_reviewer(client, fresh_db):
    from app.services import review, users

    users.ensure_user("mira")
    p = review.propose_change(
        "task", "create", {"title": "wire the gate"}, actor="planner", requested_by="mira"
    )
    out = review.approve_change(p["id"], actor="mira", strong=True)

    chain = provenance.lineage("task", out["result"]["id"], scope.Viewer("mira", True))
    assert chain["origin"] == "agent_verified"
    assert chain["created_by"] == "planner"  # authorship stays with the proposer
    assert chain["proposal"]["proposed_by"] == "planner"
    assert chain["proposal"]["requested_by"] == "mira"
    assert chain["proposal"]["reviewed_by"] == "mira"
    assert chain["verdict_is_weak"] is False


def test_a_weak_verdict_says_so(client, fresh_db):
    """In trusted-header mode a name is whatever the caller typed, so the
    verdict records a click and not a person — the same distinction the trust
    score refuses to count."""
    from app.services import review, users

    users.ensure_user("mira")
    p = review.propose_change("task", "create", {"title": "x"}, actor="planner")
    out = review.approve_change(p["id"], actor="mira", strong=False)
    chain = provenance.lineage("task", out["result"]["id"], scope.Viewer("mira", True))
    assert chain["verdict_is_weak"] is True


def test_a_rejected_proposal_is_not_a_lineage(client, fresh_db):
    """`result_id` is stamped at APPLY. Matching on entity and id instead would
    attach a proposal that was refused to the row it never became."""
    from app.services import review, users, work

    users.ensure_user("mira")
    t = work.create_task("made by hand", actor="mira")
    p = review.propose_change(
        "task", "update", {"title": "renamed"}, entity_id=t["id"], actor="planner"
    )
    review.reject_change(p["id"], actor="mira", note="no")
    chain = provenance.lineage("task", t["id"], scope.Viewer("mira", True))
    assert chain["proposal"] is None
    assert chain["origin"] == "human"


def test_history_names_changes_and_not_another_rows(client, fresh_db):
    """The ledger's detail starts with `#<id>`, and blocker ids and task ids are
    independent number spaces — an id match with no action test crossed them."""
    from app.services import blockers, users, work

    users.ensure_user("mira")
    t = work.create_task("wire the gate", actor="mira")
    work.update_task(t["id"], status="in_progress", actor="mira")
    # a blocker that happens to share the task's id
    blockers.raise_blocker("unrelated", owner="mira", actor="mira")

    chain = provenance.lineage("task", t["id"], scope.Viewer("mira", True))
    assert [h["action"] for h in chain["history"]] == ["update_task"]


def test_an_unreadable_row_answers_like_an_absent_one(client, fresh_db):
    from app.services import crews, users, work

    for n in ("insider", "outsider"):
        users.ensure_user(n)
    crew = crews.create_crew("ops", actor="insider")
    t = work.create_task("quiet", actor="insider", visibility=scope.CREW, crew_id=crew["id"])
    with pytest.raises(Exception) as refused:
        provenance.lineage("task", t["id"], scope.Viewer("outsider", True))
    with pytest.raises(Exception) as absent:
        provenance.lineage("task", 99999, scope.Viewer("outsider", True))
    assert str(refused.value).replace(str(t["id"]), "99999") == str(absent.value)


def test_a_converted_task_names_the_finding_that_asked_for_it(client, fresh_db):
    from app import db
    from app.services import insights, users, work

    users.ensure_user("mira")
    # a finding row in the shape the rules write (services/insights.py::_fire),
    # rather than hoping a rule fires against the fixture: a skipped test pins
    # nothing, and this one exists to pin the backlink
    db.execute(
        "INSERT INTO findings (rule_id, severity, subject, message, receipt, week, created_at)"
        " VALUES ('aging_wip', 'medium', 'task-1', 'Three tasks have sat in progress"
        " for over two weeks.', '{}', '2026-W33', ?)",
        (db.now(),),
    )
    found = insights.list_findings()
    assert found, "the fixture must file exactly one finding"
    made = insights.convert_finding(found[0]["id"], "task", actor="mira")
    task = work.get_task(made["id"], scope.Viewer("mira", True))
    assert task["source_finding"]["id"] == found[0]["id"]
    assert task["source_finding"]["message"] == found[0]["message"]
