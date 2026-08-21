"""One engagement, whole — and nothing from an engagement the reader cannot open.

The brief composes seven surfaces. Every one of them carries a tier, so the
tests here are mostly about the doors: an unreadable engagement must answer
exactly like an absent one, and no section may reach a row its own filter would
refuse.
"""

import pytest

from app.services import engagement_brief, scope


def _brief(name: str, eid: int) -> dict:
    return engagement_brief.brief(eid, scope.Viewer(name, True))


def test_an_unreadable_engagement_answers_like_an_absent_one(client, fresh_db):
    """Ids are sequential integers. If a refused read said "forbidden" and an
    absent one said "no such row", a caller could map the whole portfolio by
    walking ids (services/scope.py::Viewer)."""
    from app.services import crews, engagements, users

    for n in ("insider", "outsider"):
        users.ensure_user(n)
    crew = crews.create_crew("ops", actor="insider")
    eng = engagements.create_engagement(
        "Secret migration",
        project_class="migration",
        actor="insider",
        visibility=scope.CREW,
        crew_id=crew["id"],
    )
    with pytest.raises(Exception) as absent:
        _brief("outsider", 99999)
    with pytest.raises(Exception) as refused:
        _brief("outsider", eng["id"])
    assert str(absent.value) == str(refused.value).replace(str(eng["id"]), "99999")

    assert _brief("insider", eng["id"])["engagement"]["name"] == "Secret migration"


def test_since_yesterday_counts_only_this_engagement(client, fresh_db):
    """The strip answers "what moved HERE since yesterday" — a task finished on
    another engagement must not inflate it, and a fresh blocker must count."""
    from app.services import blockers, engagements, users, work

    users.ensure_user("ada")
    mine = engagements.create_engagement("Mine", actor="ada")
    other = engagements.create_engagement("Other", actor="ada")
    done_here = work.create_task("here", engagement_id=mine["id"], actor="ada")
    done_there = work.create_task("there", engagement_id=other["id"], actor="ada")
    work.update_task(done_here["id"], status="done", actor="ada")
    work.update_task(done_there["id"], status="done", actor="ada")
    open_here = work.create_task("carrier", engagement_id=mine["id"], actor="ada")
    b = blockers.raise_blocker("stuck", task_id=open_here["id"], actor="ada")

    strip = _brief("ada", mine["id"])["since_yesterday"]
    assert strip == {"tasks_done": 1, "blockers_opened": 1, "blockers_resolved": 0}

    blockers.resolve_blocker(b["id"], actor="ada")
    strip = _brief("ada", mine["id"])["since_yesterday"]
    assert strip["blockers_resolved"] == 1


def test_the_brief_carries_what_the_seven_surfaces_carry(client, fresh_db):
    from app.services import blockers, engagements, users, work

    users.ensure_user("ava")
    eng = engagements.create_engagement("Atlas", project_class="migration", actor="ava")
    m = work.create_milestone("Cutover", project="Atlas", due_date="2020-01-01", actor="ava")
    work.update_milestone(m["id"], engagement_id=eng["id"], actor="ava")
    t = work.create_task("wire the gate", milestone_id=m["id"], actor="ava")
    b = blockers.raise_blocker("vendor silent", owner="ava", task_id=t["id"], actor="ava")

    out = _brief("ava", eng["id"])
    assert out["engagement"]["name"] == "Atlas"
    assert [x["id"] for x in out["milestones"]] == [m["id"]]
    assert t["id"] in [x["id"] for x in out["tasks"]]
    assert b["id"] in [x["id"] for x in out["blockers"]]
    # health is the SAME pass every other surface reads, not a second one
    assert out["health"]["color"] in ("red", "yellow", "green")
    # an overdue milestone is a receipt, and the receipt resolves its own row
    assert any(
        r["entity"] == "milestone" and r["id"] == m["id"]
        for rc in out["health"]["receipts"]
        for r in rc["refs"]
    )


def test_next_actions_agree_with_the_portfolio_queue(client, fresh_db):
    """The brief narrows the queue's own rows rather than re-running its
    predicates: two sets would let one page recommend what the other does not."""
    from app.services import blockers, engagements, intervention, users, work

    users.ensure_user("ava")
    eng = engagements.create_engagement("Atlas", project_class="migration", actor="ava")
    m = work.create_milestone("Cutover", project="Atlas", actor="ava")
    work.update_milestone(m["id"], engagement_id=eng["id"], actor="ava")
    t = work.create_task("wire the gate", milestone_id=m["id"], actor="ava")
    b = blockers.raise_blocker(
        "vendor silent", owner="ava", impact="critical", task_id=t["id"], actor="ava"
    )
    fresh_db.execute(
        "UPDATE blockers SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (b["id"],)
    )
    blockers.sweep_escalations()

    viewer = scope.Viewer("ava", True)
    mine = _brief("ava", eng["id"])["next_actions"]
    whole = intervention.interventions(viewer, limit=50)
    assert any(a["entity"] == "blocker" and a["entity_id"] == b["id"] for a in mine)
    # every row in the brief is a row of the portfolio queue, unchanged
    for row in mine:
        assert row in whole


def test_a_crew_blocker_does_not_ride_a_readable_engagement(client, fresh_db):
    from app.services import blockers, crews, engagements, users, work

    for n in ("insider", "outsider"):
        users.ensure_user(n)
    crew = crews.create_crew("ops", actor="insider")
    eng = engagements.create_engagement("Atlas", project_class="migration", actor="insider")
    t = work.create_task(
        "quiet work",
        engagement_id=eng["id"],
        actor="insider",
        visibility=scope.CREW,
        crew_id=crew["id"],
    )
    blockers.raise_blocker(
        "crew-only outage",
        owner="insider",
        task_id=t["id"],
        actor="insider",
        visibility=scope.CREW,
        crew_id=crew["id"],
    )
    out = _brief("outsider", eng["id"])
    assert out["engagement"]["name"] == "Atlas"  # the engagement is workspace
    assert not any("crew-only" in str(b["title"]) for b in out["blockers"])
    assert not any("quiet work" in str(x["title"]) for x in out["tasks"])


def test_a_drift_finding_reaches_the_engagement_it_names(client, fresh_db):
    """`intervention` links a plan_drift row to /engagement/{id} because that
    page carries the drift. A brief that dropped findings meant the manager
    followed that link and read "nothing in the queue belongs to this
    engagement" four cards above the drift itself."""
    from app.services import playbooks, users, work

    users.ensure_user("ava")
    eid = playbooks.instantiate("prototype", "Atlas", actor="ava")["engagement"]["id"]
    for t in ("hotfix the sync", "redo the demo data", "chase legal"):
        work.create_task(t, engagement_id=eid, actor="ava")

    from app.services import insights

    insights.run_findings()
    out = _brief("ava", eid)
    drift = [a for a in out["next_actions"] if a["kind"].startswith("finding")]
    assert drift, "the drift finding must reach the page its own link points at"
    assert "drifted" in drift[0]["title"]


def test_another_engagements_finding_stays_out(client, fresh_db):
    from app.services import engagements, playbooks, users, work

    users.ensure_user("ava")
    eid = playbooks.instantiate("prototype", "Atlas", actor="ava")["engagement"]["id"]
    for t in ("a", "b", "c"):
        work.create_task(t, engagement_id=eid, actor="ava")
    other = engagements.create_engagement("Orion", project_class="migration", actor="ava")

    from app.services import insights

    insights.run_findings()
    assert not [
        a for a in _brief("ava", other["id"])["next_actions"] if a["kind"].startswith("finding")
    ]
