"""Drift from a playbook's plan, while the engagement still runs.

The snapshot is written at KICKOFF, so the diff exists from day one — and
nothing read it until close, when the only thing left to do about it is write
a lesson. A milestone that has already moved twice and four unplanned tasks are
facts a team can still act on in week three.
"""

from app.services import insights, scope


def _engagement_from_playbook(actor: str = "ava") -> int:
    """The engagement id a kickoff produced. `instantiate` returns the whole
    plan it created; only the engagement matters here."""
    from app.services import playbooks, users

    users.ensure_user(actor)
    return playbooks.instantiate("prototype", "Atlas", actor=actor)["engagement"]["id"]


def test_a_plan_that_landed_as_planned_says_nothing(client, fresh_db):
    _engagement_from_playbook()
    assert insights._r_plan_drift() == []


def test_a_little_drift_says_nothing(client, fresh_db):
    """Every engagement drifts. A rule that fires on one moved date is a rule
    the reader learns to skip, and then the one that mattered goes unread."""
    from app.services import work

    eid = _engagement_from_playbook()
    work.create_task("one unplanned thing", engagement_id=eid, actor="ava")
    assert insights._r_plan_drift() == []


def test_drift_past_the_threshold_names_what_moved(client, fresh_db):
    from app.services import work

    eid = _engagement_from_playbook()
    for t in ("hotfix the vendor sync", "redo the demo data", "chase legal"):
        work.create_task(t, engagement_id=eid, actor="ava")

    fired = insights._r_plan_drift()
    assert len(fired) == 1
    f = fired[0]
    assert f["rule_id"] == "plan_drift"
    assert "3 tasks added outside the plan" in f["message"]
    # the receipt names the engagement, so the manager queue can resolve it
    assert f["receipt"]["engagement_id"] == eid
    # and it fires WHILE the engagement runs, which is the whole point
    assert f["subject"] == f"engagement-{eid}"


def test_an_engagement_with_no_playbook_is_not_drifting(client, fresh_db):
    from app.services import engagements, users, work

    users.ensure_user("ava")
    eng = engagements.create_engagement("by hand", project_class="migration", actor="ava")
    for t in ("a", "b", "c", "d"):
        work.create_task(t, engagement_id=eng["id"], actor="ava")
    # no kickoff snapshot means no plan to drift from — not "no drift"
    assert insights._r_plan_drift() == []


def test_a_private_task_title_never_reaches_the_finding(client, fresh_db):
    """`findings` carries no tier, and this message reaches the digest and the
    manager queue. The diff is read at the workspace tier for that reason."""
    from app.services import work

    eid = _engagement_from_playbook()
    for t in ("open one", "open two"):
        work.create_task(t, engagement_id=eid, actor="ava")
    work.create_task(
        "the secret one",
        engagement_id=eid,
        actor="ava",
        visibility=scope.PRIVATE,
    )
    fired = insights._r_plan_drift()
    assert not any("secret" in f["message"] for f in fired)
