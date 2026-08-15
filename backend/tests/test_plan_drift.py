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


def test_an_evening_finish_west_of_utc_is_not_a_slip(client, fresh_db, monkeypatch):
    """`completed_at` is a UTC timestamp and a milestone's `due_date` is a
    TEAM-local date, so slicing the timestamp compares two calendars.

    West of UTC an evening finish carries the NEXT UTC day, so a milestone
    delivered on time reads as one day late. Three of those clear
    PLAN_DRIFT_ALARM and file a permanent medium finding — findings are unique
    per (rule, subject, week) — against an engagement that is exactly on plan.
    """
    import importlib

    from app import config, db
    from app.services import playbooks

    monkeypatch.setenv("SKEIN_TZ", "America/New_York")
    importlib.reload(config)
    try:
        eid = _engagement_from_playbook()
        rows = db.query("SELECT id, due_date FROM milestones WHERE engagement_id = ?", (eid,))
        assert len(rows) >= 3, "the playbook must lay out enough milestones to trip the alarm"
        for m in rows:
            # 20:00 New York on the due date itself — the same team-day, and
            # the next UTC day. Done ON TIME by every reading a person has.
            local_evening = f"{m['due_date']}T20:00:00-04:00"
            utc = (
                db.query_one("SELECT (?::timestamp)::text AS u", (local_evening,))["u"].replace(
                    " ", "T"
                )
                + "+00:00"
            )
            db.execute(
                "UPDATE milestones SET status = 'done', completed_at = ? WHERE id = ?",
                (utc, m["id"]),
            )
        moved = playbooks.close_out_diff(eid)["slipped"]
        assert moved == [], f"on-time evening finishes reported as slip: {moved}"
        assert insights._r_plan_drift() == []
    finally:
        # reloaded back inside `finally`: config is module state, and a zone
        # left set here follows every later test in this process
        monkeypatch.setenv("SKEIN_TZ", "")
        importlib.reload(config)
        assert config.TZ_NAME == "UTC"


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
