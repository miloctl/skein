"""The weekly rhythm: draft, plan through the review inbox, week validation, rituals, and the claim that stops a double run."""

import pytest
from conftest import _ago


def test_week_rituals_produce_packets_and_notify(client, fresh_db):
    from pathlib import Path

    from app.services import artifact_files, promises, rituals, users

    users.ensure_user("mira")
    promises.add_promise("demo to ops", due_date="2020-01-01", actor="mira")
    close = rituals.week_close(actor="mira", force=True)
    assert close["items"] >= 1 and "Promises due or overdue" in close["markdown"]
    opened = rituals.week_open(actor="mira", force=True)
    assert opened["briefed"] >= 1 and "mira" in opened["markdown"]
    for row in fresh_db.query("SELECT path, content_sha256 FROM artifacts WHERE kind = 'ritual'"):
        assert row["content_sha256"] == artifact_files.content_sha256(
            Path(row["path"]).read_bytes()
        )
    # personal notification landed for the obligation owner
    notes = client.get("/api/notifications", headers={"X-User": "mira"}).json()
    assert any("Your week:" in n["message"] for n in notes)


def test_manual_ritual_run_consumes_the_weekly_claim(fresh_db):
    from app.services import rituals, users

    users.ensure_user("mira")
    manual = rituals.week_open(actor="mira", force=True)
    assert "markdown" in manual
    scheduled = rituals.week_open(actor="scheduler")
    assert scheduled.get("skipped") == "already ran this week"


def test_apply_plan_skips_missing_tasks(client, fresh_db):
    from app.services import weekly

    t1 = client.post("/api/tasks", json={"title": "real"}).json()
    out = weekly.apply_plan("2026-W31", [t1["id"], 999999], actor="tester")
    assert out["committed"] == 1 and out["skipped"] == [999999]
    row = fresh_db.query_one("SELECT committed_week FROM tasks WHERE id = ?", (t1["id"],))
    assert row["committed_week"] == "2026-W31"

    with pytest.raises(ValueError):
        weekly.apply_plan("2026-W31", [999999])


def test_week_validation_everywhere(client):
    assert client.get("/api/week/draft?week=banana").status_code == 400
    assert client.get("/api/week?week=2026-W99").status_code == 400  # no such ISO week
    t = client.post("/api/tasks", json={"title": "x"}).json()
    assert (
        client.patch(f"/api/tasks/{t['id']}", json={"committed_week": "2026-W00"}).status_code
        == 400
    )


def test_committed_week_clearable(client, fresh_db):
    t = client.post("/api/tasks", json={"title": "x"}).json()
    client.patch(f"/api/tasks/{t['id']}", json={"committed_week": "2026-W31"})
    client.patch(f"/api/tasks/{t['id']}", json={"committed_week": "-"})
    row = fresh_db.query_one("SELECT committed_week FROM tasks WHERE id = ?", (t["id"],))
    assert row["committed_week"] is None


def test_weekly_claim_not_burned_by_empty_draft(fresh_db):
    from app.services import users, weekly, work

    assert weekly.propose_weekly_plan()["skipped"] == "nothing to commit"
    users.ensure_user("ann")
    fresh_db.execute("UPDATE users SET kind = 'human' WHERE name = 'ann'")
    from app.services.collab import post_standup

    post_standup("ann", today="x")
    work.create_task("late task", assignee="ann", actor="ann")
    out = weekly.propose_weekly_plan()
    assert out.get("status") == "pending"  # the empty run did not consume the week


def test_weekly_draft_and_plan_via_review_inbox(client, fresh_db):
    from app.services import weekly

    client.post("/api/standups", json={"today": "here"})  # tester becomes active human
    t1 = client.post(
        "/api/tasks", json={"title": "a", "assignee": "tester", "priority": "urgent"}
    ).json()
    t2 = client.post("/api/tasks", json={"title": "b", "assignee": "tester"}).json()

    draft = client.get("/api/week/draft").json()
    ids = [i["task_id"] for i in draft["items"]]
    assert ids[0] == t1["id"] and t2["id"] in ids  # urgent first

    out = weekly.propose_weekly_plan(actor="scheduler")
    assert out["status"] == "pending"

    # while the proposal waits, the week view names it — the Health card
    # offered "Draft a plan" beside a pending plan for the SAME week, and the
    # drafter it invited filed a duplicate proposal
    week = client.get("/api/week").json()
    assert week["pending_proposal"] == {
        "id": out["id"],
        "summary": week["pending_proposal"]["summary"],
    }
    assert "Weekly commitment line" in week["pending_proposal"]["summary"]

    approved = client.post(f"/api/review/{out['id']}/approve", json={}).json()
    assert approved["status"] == "approved"

    week = client.get("/api/week").json()
    # settled: the pointer is gone with the pending status
    assert week["pending_proposal"] is None
    assert week["committed"] == 2
    client.patch(f"/api/tasks/{t1['id']}", json={"status": "done"})
    week = client.get("/api/week").json()
    assert week["done"] == 1 and week["kept_percent"] == 50

    # claim: second propose in the same week is a no-op
    assert "skipped" in weekly.propose_weekly_plan(actor="scheduler")


def test_committed_week_validation(client):
    t = client.post("/api/tasks", json={"title": "x"}).json()
    r = client.patch(f"/api/tasks/{t['id']}", json={"committed_week": "next week"})
    assert r.status_code == 400
    ok = client.patch(f"/api/tasks/{t['id']}", json={"committed_week": "2026-W31"})
    assert ok.status_code == 200


def test_stale_wip_nudge_claims_week(client, fresh_db, monkeypatch):
    from app.services import notifications, portfolio

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    t = client.post("/api/tasks", json={"title": "old", "assignee": "ava"}).json()
    client.patch(f"/api/tasks/{t['id']}", json={"status": "in_progress"})
    fresh_db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (_ago(10), t["id"]))
    assert portfolio.nudge_stale_wip()["nudged"] == 1
    assert portfolio.nudge_stale_wip()["nudged"] == 0  # claimed for this week
    msgs = [n["message"] for n in notifications.list_notifications("ava", unread_only=False)]
    assert any("in progress" in m for m in msgs)


def test_agent_recorded_promises_surface_in_week_open(fresh_db):
    from app.services import promises, rituals, users

    users.ensure_user("mira")
    users.ensure_user("scribe", kind="agent")
    promises.add_promise("send the SOW", due_date="2020-01-02", actor="scribe")
    opened = rituals.week_open(actor="mira", force=True)
    assert "Recorded by agents" in opened["markdown"]
    assert "send the SOW" in opened["markdown"]


@pytest.mark.parametrize("job_name", ["weekly-plan", "stale-wip-nudge"])
def test_weekly_job_failure_rolls_back_claim_and_notifications(fresh_db, monkeypatch, job_name):
    from app.services import jobs, notifications, users, work

    for person in ("ava", "mira"):
        users.ensure_user(person)
        task = work.create_task(f"work for {person}", assignee=person, actor=person)
        work.update_task(task["id"], status="in_progress", actor=person)
        fresh_db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (_ago(10), task["id"]))
    before = fresh_db.query("SELECT id FROM notifications ORDER BY id")
    spec = next(job for job in jobs.JOBS if job.name == job_name)
    original = notifications.notify
    calls = 0

    def failed_notice(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == (2 if job_name == "stale-wip-nudge" else 1):
            raise RuntimeError("notification interrupted")
        return result

    with monkeypatch.context() as failing:
        failing.setattr(notifications, "notify", failed_notice)
        jobs.run_job(spec)
    assert fresh_db.query_row("SELECT status FROM job_outcomes WHERE job = ?", (job_name,)) == {
        "status": "error"
    }
    assert fresh_db.query("SELECT * FROM job_runs WHERE job = ?", (job_name,)) == []
    assert fresh_db.query("SELECT id FROM notifications ORDER BY id") == before
    assert fresh_db.query("SELECT * FROM pending_changes") == []

    jobs.run_job(spec)
    assert (
        fresh_db.query_row(
            "SELECT status FROM job_outcomes WHERE job = ? ORDER BY id DESC LIMIT 1", (job_name,)
        )["status"]
        == "ok"
    )
    assert (
        fresh_db.query_row("SELECT COUNT(*) AS n FROM job_runs WHERE job = ?", (job_name,))["n"]
        == 1
    )
    if job_name == "weekly-plan":
        assert fresh_db.query_row("SELECT COUNT(*) AS n FROM pending_changes")["n"] == 1
    else:
        notices = fresh_db.query(
            "SELECT * FROM notifications WHERE message LIKE '%in progress more than%'"
        )
        assert len(notices) == 2
        assert {notice["user"] for notice in notices} == {"ava", "mira"}


@pytest.mark.parametrize("job_name", ["weekly-plan", "stale-wip-nudge"])
def test_weekly_job_skip_does_not_record_another_success(fresh_db, job_name):
    from app.services import jobs, users, work

    users.ensure_user("ava")
    task = work.create_task("ongoing work", assignee="ava", actor="ava")
    work.update_task(task["id"], status="in_progress", actor="ava")
    fresh_db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (_ago(10), task["id"]))
    spec = next(job for job in jobs.JOBS if job.name == job_name)
    jobs.run_job(spec)
    first = fresh_db.query("SELECT * FROM job_outcomes WHERE job = ?", (job_name,))
    assert len(first) == 1 and first[0]["status"] == "ok"
    jobs.run_job(spec)
    assert fresh_db.query("SELECT * FROM job_outcomes WHERE job = ?", (job_name,)) == first


def test_weekly_summary_agrees_with_its_own_count(fresh_db):
    """This summary reaches a reader on My Day, on Approvals, and in a
    notification. It shipped "1 tasks" — CLAUDE.md requires sentence-form
    text to compute plurals, and a string carrying a number gets no warmth
    allowance to hide behind."""
    from app.services import users, weekly, work

    users.ensure_user("solo")
    work.create_task(title="the only open task", assignee="solo")
    weekly.propose_weekly_plan(actor="scheduler")
    summary = fresh_db.query_row(
        "SELECT summary FROM pending_changes WHERE entity = 'weekly_plan'"
    )["summary"]
    assert "1 task (" in summary, summary
    assert "1 tasks" not in summary
