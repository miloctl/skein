"""Routines: a weekly template that creates a task, and with an agent a
delegation, each time it is due (docs/intent/routines.md)."""

import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

WEEKLY = {"title": "Monday sweep", "weekdays": "1", "at_time": "07:00"}


@pytest.fixture
def people(fresh_db):
    from app.services import crews, users

    for name in ("mira", "bo"):
        users.ensure_user(name)
    users.ensure_user("scout", kind="agent")
    return {"crew": crews.create_crew("Ops", actor="mira")["id"]}


def _routine(**over):
    from app.services import routines

    actor = over.pop("actor", "mira")
    return routines.create_routine({**WEEKLY, **over}, actor=actor)


def _viewer(name):
    from app.services import scope

    return scope.Viewer(name, True)


def _row(fresh_db, rid):
    return fresh_db.query_one("SELECT * FROM routines WHERE id = ?", (rid,))


def _due(fresh_db, rid) -> datetime:
    return datetime.fromisoformat(_row(fresh_db, rid)["next_at"])


def _tasks(fresh_db, rid):
    return fresh_db.query("SELECT * FROM tasks WHERE routine_id = ? ORDER BY id", (rid,))


def _notices(fresh_db, user, like):
    return fresh_db.query(
        'SELECT message FROM notifications WHERE "user" = ? AND message LIKE ?', (user, like)
    )


def test_next_occurrence_keeps_the_week_parity_and_the_wall_clock(monkeypatch):
    from app import config
    from app.services import routines

    monkeypatch.setattr(config, "TZ", ZoneInfo("America/New_York"))
    weekly = {"weekdays": "1,4", "at_time": "07:00", "every_weeks": 1, "starts_on": "2026-10-05"}
    after = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)  # Monday 08:00 EDT, past 07:00
    assert routines.next_occurrence(weekly, after) == datetime(2026, 10, 8, 11, 0, tzinfo=UTC)
    # every other Wednesday from the week of 2026-10-07: the next week is off
    fortnight = {"weekdays": "3", "at_time": "10:00", "every_weeks": 2, "starts_on": "2026-10-07"}
    first = routines.next_occurrence(fortnight, datetime(2026, 10, 1, tzinfo=UTC))
    assert first == datetime(2026, 10, 7, 14, 0, tzinfo=UTC)
    assert routines.next_occurrence(fortnight, first) == datetime(2026, 10, 21, 14, 0, tzinfo=UTC)
    # a spring-forward gap runs an hour later, and an overlap runs the first time
    gap = {"weekdays": "7", "at_time": "02:30", "every_weeks": 1, "starts_on": "2026-03-08"}
    assert routines.next_occurrence(gap, datetime(2026, 3, 7, tzinfo=UTC)) == datetime(
        2026, 3, 8, 7, 30, tzinfo=UTC
    )
    overlap = {"weekdays": "7", "at_time": "01:30", "every_weeks": 1, "starts_on": "2026-11-01"}
    assert routines.next_occurrence(overlap, datetime(2026, 10, 31, tzinfo=UTC)) == datetime(
        2026, 11, 1, 5, 30, tzinfo=UTC
    )


def test_the_second_one_thirty_of_a_fall_back_night_fires_nothing(people, fresh_db, monkeypatch):
    from app import config
    from app.services import routines

    monkeypatch.setattr(config, "TZ", ZoneInfo("America/New_York"))
    rid = _routine(weekdays="7", at_time="01:30", starts_on="2026-11-01")["id"]
    assert _due(fresh_db, rid) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert routines.fire_due(rid, datetime(2026, 11, 1, 5, 30, tzinfo=UTC)) == "fired"
    assert routines.fire_due(rid, datetime(2026, 11, 1, 6, 30, tzinfo=UTC)) == ""
    assert len(_tasks(fresh_db, rid)) == 1
    assert _tasks(fresh_db, rid)[0]["title"] == "Monday sweep (2026-11-01)"


def test_missed_times_fire_once_late_dated_with_the_latest(people, fresh_db):
    from app.services import routines

    rid = _routine()["id"]
    first = _due(fresh_db, rid)
    now = first + timedelta(days=14, hours=1)
    assert routines.fire_due(rid, now) == "late"
    made = _tasks(fresh_db, rid)
    latest = (first + timedelta(days=14)).date().isoformat()
    assert [t["title"] for t in made] == [f"Monday sweep ({latest})"]
    assert _due(fresh_db, rid) > now
    assert _row(fresh_db, rid)["last_outcome"] == "late"
    notices = _notices(fresh_db, "mira", "%missed 3 times%")
    assert len(notices) == 1 and f"task #{made[0]['id']}" in notices[0]["message"]
    # a time a year old still fires once
    rid2 = _routine(title="Yearly")["id"]
    assert routines.fire_due(rid2, _due(fresh_db, rid2) + timedelta(days=365)) == "late"
    assert len(_tasks(fresh_db, rid2)) == 1


def test_a_resumed_routine_never_catches_up(people, fresh_db):
    from app.services import routines

    rid = _routine()["id"]
    routines.pause_routine(rid, actor="mira")
    routines.resume_routine(rid, actor="mira")
    now = datetime.now(UTC)
    assert _due(fresh_db, rid) > now
    assert routines.fire_due(rid, now) == ""
    assert _tasks(fresh_db, rid) == []


def test_two_ticks_on_one_due_routine_make_one_task(people, fresh_db):
    from app.services import routines

    rid = _routine()["id"]
    now = _due(fresh_db, rid) + timedelta(minutes=1)
    start = threading.Barrier(2)
    outcomes: list[str] = []
    errors: list[Exception] = []

    def fire():
        try:
            start.wait(timeout=5)
            outcomes.append(routines.fire_due(rid, now))
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=fire) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert errors == []
    assert sorted(outcomes) == ["", "fired"]
    assert len(_tasks(fresh_db, rid)) == 1


def test_an_agent_routine_delegates_with_the_owner_as_sponsor(people, fresh_db):
    from app.services import routines

    rid = _routine(agent="scout", acceptance_criteria="a list of stale decisions")["id"]
    due = _due(fresh_db, rid)
    assert routines.fire_due(rid, due) == "fired"
    task = _tasks(fresh_db, rid)[0]
    assert task["title"] == f"Monday sweep ({due.date().isoformat()})"
    assert (task["created_by"], task["origin"], task["sponsor"]) == ("mira", "human", "mira")
    assert task["delegated_agent"] == "scout"
    assert task["acceptance_criteria"] == "a list of stale decisions"
    wake = fresh_db.query_one("SELECT * FROM agent_wakeups WHERE agent = 'scout'")
    assert (wake["status"], wake["requested_by"], wake["trigger_task_id"]) == (
        "pending",
        "mira",
        task["id"],
    )


def test_an_open_task_skips_and_the_third_skip_pauses(people, fresh_db):
    from app.services import routines

    rid = _routine()["id"]
    due = _due(fresh_db, rid)
    assert routines.fire_due(rid, due) == "fired"
    for week in (1, 2):
        assert routines.fire_due(rid, due + timedelta(days=7 * week)) == "previous_open"
    assert _row(fresh_db, rid)["skipped_in_row"] == 2
    assert routines.fire_due(rid, due + timedelta(days=21)) == "paused"
    row = _row(fresh_db, rid)
    assert (row["status"], row["paused_reason"], row["next_at"]) == (
        "paused",
        "nobody_finished",
        None,
    )
    task_id = _tasks(fresh_db, rid)[0]["id"]
    notices = _notices(fresh_db, "mira", "%skipped the last 3 times%")
    assert len(notices) == 1 and f"task #{task_id} is still open" in notices[0]["message"]
    assert len(_tasks(fresh_db, rid)) == 1


def test_a_finished_task_lets_the_next_time_fire_and_resets_the_count(people, fresh_db):
    from app.services import routines, work

    rid = _routine()["id"]
    due = _due(fresh_db, rid)
    routines.fire_due(rid, due)
    routines.fire_due(rid, due + timedelta(days=7))
    work.update_task(_tasks(fresh_db, rid)[0]["id"], status="done", actor="mira")
    assert routines.fire_due(rid, due + timedelta(days=14)) == "fired"
    assert _row(fresh_db, rid)["skipped_in_row"] == 0
    assert len(_tasks(fresh_db, rid)) == 2


def test_an_owner_who_left_the_crew_pauses_the_routine(people, fresh_db):
    from app.services import crews, routines

    crews.add_member(people["crew"], "bo", actor="mira")
    rid = _routine(actor="bo", visibility="crew", crew_id=people["crew"])["id"]
    crews.remove_member(people["crew"], "bo", actor="mira")
    assert routines.fire_due(rid, _due(fresh_db, rid)) == "paused"
    assert _row(fresh_db, rid)["paused_reason"] == "owner_left_crew"
    assert _tasks(fresh_db, rid) == []


def test_a_refused_firing_pauses_and_creates_nothing(people, fresh_db):
    from app.services import crews, routines

    crews.add_member(people["crew"], "bo", actor="mira")
    rid = _routine(visibility="crew", crew_id=people["crew"], assignee="bo")["id"]
    # the assignee can no longer read a crew task, so create_task refuses
    crews.remove_member(people["crew"], "bo", actor="mira")
    assert routines.fire_due(rid, _due(fresh_db, rid)) == "paused"
    assert _row(fresh_db, rid)["paused_reason"] == "fire_refused"
    assert _tasks(fresh_db, rid) == []
    assert fresh_db.query_one("SELECT COUNT(*) AS n FROM tasks")["n"] == 0
    assert len(_notices(fresh_db, "mira", "%could not create the task. Check the assignee%")) == 1


def test_deactivation_pauses_what_it_strands(people, fresh_db):
    from app.services import routines, users

    users.ensure_user("ana")
    owners = _routine(actor="ana")["id"]
    delegating = _routine(title="Sweep", agent="scout")["id"]
    users.set_active("ana", False, actor="mira")
    assert _row(fresh_db, owners)["paused_reason"] == "owner_inactive"
    assert _notices(fresh_db, "ana", "%routine%") == []
    users.set_active("scout", False, actor="mira")
    assert _row(fresh_db, delegating)["paused_reason"] == "agent_unavailable"
    assert len(_notices(fresh_db, "mira", "%because agent scout is deactivated%")) == 1
    # reactivation resumes nothing
    users.set_active("scout", True, actor="mira")
    assert _row(fresh_db, delegating)["status"] == "paused"
    assert routines.fire_due(delegating, datetime.now(UTC) + timedelta(days=30)) == ""


def test_only_the_owner_changes_a_routine_and_a_non_reader_sees_nothing(people, fresh_db):
    from app import db
    from app.services import routines, scope

    rid = _routine(visibility="crew", crew_id=people["crew"])["id"]
    from app.services import crews

    crews.add_member(people["crew"], "bo", actor="mira")
    with pytest.raises(PermissionError, match=f"Only mira can change routine #{rid}"):
        routines.update_routine(rid, {"title": "x"}, actor="bo")
    # any reader pauses, and the owner hears who did
    routines.pause_routine(rid, actor="bo")
    assert len(_notices(fresh_db, "mira", f"bo paused your routine #{rid}%")) == 1
    crews.remove_member(people["crew"], "bo", actor="mira")
    with pytest.raises(db.NotFound) as hidden:
        routines.pause_routine(rid, actor="bo")
    with pytest.raises(db.NotFound) as absent:
        routines.pause_routine(999_999, actor="bo")
    assert str(hidden.value).replace(str(rid), "#") == str(absent.value).replace("999999", "#")
    assert routines.list_routines(scope.Viewer("bo", True)) == []
    with pytest.raises(ValueError, match="A private routine cannot delegate"):
        _routine(agent="scout", visibility="private")


def test_a_shared_task_hides_a_routine_its_reader_cannot_open(people, fresh_db):
    from app.services import routines, scope, sharing, work

    rid = _routine(visibility="crew", crew_id=people["crew"])["id"]
    routines.fire_due(rid, _due(fresh_db, rid))
    tid = _tasks(fresh_db, rid)[0]["id"]
    assert work.get_task(tid, scope.Viewer("mira", True))["routine_id"] == rid
    sharing.share_with_team("tasks", tid, actor="mira")
    bo = scope.Viewer("bo", True)
    assert work.get_task(tid, bo)["routine_id"] is None
    assert next(t for t in work.list_tasks_joined(bo) if t["id"] == tid)["routine_id"] is None
    raw = fresh_db.query_one("SELECT * FROM tasks WHERE id = ?", (tid,))
    assert work.redact_task_relationships([raw], bo)[0]["routine_id"] is None


def test_the_tick_fires_every_due_routine_and_survives_one_failure(people, fresh_db, monkeypatch):
    from app.services import routines

    ids = [_routine(title=f"Sweep {n}")["id"] for n in range(3)]
    now = max(_due(fresh_db, rid) for rid in ids) + timedelta(minutes=1)
    real = routines.fire_due

    def fire(rid, when):
        if rid == ids[1]:
            raise RuntimeError("a database fault")
        return real(rid, when)

    monkeypatch.setattr(routines, "fire_due", fire)
    out = routines.tick(now)
    assert out == {
        "fired": 2,
        "late": 0,
        "skipped": 0,
        "paused": 0,
        "failed": 1,
        "status": "partial",
    }
    assert [len(_tasks(fresh_db, rid)) for rid in ids] == [1, 0, 1]


def test_a_restart_fires_a_missed_time_once_and_records_only_counts(people, fresh_db):
    from app.services import jobs

    rid = _routine(title="Private sweep", visibility="private")["id"]
    # the server was down when the time came: next_at is 20 minutes gone
    past = (datetime.now(UTC) - timedelta(minutes=20)).isoformat(timespec="seconds")
    fresh_db.execute("UPDATE routines SET next_at = ? WHERE id = ?", (past, rid))
    spec = next(s for s in jobs.JOBS if s.name == "routines")
    assert spec.catch_up and spec.retry_safe
    jobs.run_job(spec)
    assert len(_tasks(fresh_db, rid)) == 1
    assert _row(fresh_db, rid)["last_outcome"] == "late"
    outcome = fresh_db.query_one(
        "SELECT detail FROM job_outcomes WHERE job = 'routines' ORDER BY id DESC LIMIT 1"
    )
    assert "late=1" in outcome["detail"] and "Private sweep" not in outcome["detail"]


def test_the_routes_ask_for_a_proved_identity_and_the_owner(client, people, fresh_db):
    from conftest import _strong

    from app.services import crews

    body = {**WEEKLY, "visibility": "crew", "crew_id": people["crew"]}
    # a typed trusted-header name cannot create standing work under a name
    assert client.post("/api/routines", json=body, headers={"X-User": "mira"}).status_code in (
        401,
        403,
    )
    mira = _strong(name="mira")
    made = client.post("/api/routines", json=body, headers=mira)
    assert made.status_code == 201
    rid = made.json()["id"]
    listed = client.get("/api/routines", headers=mira).json()
    row = next(r for r in listed["routines"] if r["id"] == rid)
    assert listed["zone"] and row["next_local"] and row["can_edit"] and row["open_task_id"] is None
    crews.add_member(people["crew"], "bo", actor="mira")
    bo = _strong(name="bo")
    refused = client.patch(f"/api/routines/{rid}", json={"title": "Mine now"}, headers=bo)
    assert refused.status_code == 403
    assert (
        refused.json()["detail"] == f"Only mira can change routine #{rid}. Pause it, or ask mira."
    )
    assert client.post(f"/api/routines/{rid}/pause", headers={"X-User": "bo"}).status_code == 200
    crews.remove_member(people["crew"], "bo", actor="mira")
    hidden = client.post(f"/api/routines/{rid}/pause", headers=bo)
    absent = client.post("/api/routines/999999/pause", headers=bo)
    assert hidden.status_code == absent.status_code == 404
    assert hidden.json()["detail"].replace(str(rid), "#") == absent.json()["detail"].replace(
        "999999", "#"
    )
    bad = client.post("/api/routines", json={**WEEKLY, "at_time": "7am"}, headers=mira)
    assert bad.status_code == 422 or (bad.status_code == 400 and "7am" not in bad.text)


def test_a_workplace_policy_can_refuse_a_new_routine(people):
    from conftest import _strong
    from fastapi.testclient import TestClient

    from app.extensions import PolicyContribution, SkeinModule
    from app.extensions.policy import PolicyDecision, PolicyEffect
    from app.main import create_app

    guarded = (
        "skein.rest.post.routines",
        "skein.rest.patch.routines",
        "skein.rest.post.routines.resume",
    )

    def deny(request):
        if request.action in guarded and (request.resource.attributes or {}).get("agent"):
            return PolicyDecision(PolicyEffect.DENY, ("no standing delegations",))
        return None

    module = SkeinModule(
        module_id="acme.routines",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.7.0",
        policies=(PolicyContribution("acme.routines.no-agents", deny),),
    )
    from app.services import routines

    # written before the rule, then paused: its resume restarts a standing
    # delegation the rule refuses
    earlier = routines.create_routine({**WEEKLY, "agent": "scout"}, actor="mira")["id"]
    routines.pause_routine(earlier, actor="mira")
    with TestClient(create_app(modules=(module,)), headers=_strong(name="mira")) as client:
        assert client.post("/api/routines", json={**WEEKLY, "agent": "scout"}).status_code == 403
        made = client.post("/api/routines", json=WEEKLY)
        assert made.status_code == 201
        rid = made.json()["id"]
        # an edit that adds the agent is the same standing delegation
        added = client.patch(f"/api/routines/{rid}", json={"agent": "scout"})
        assert added.status_code == 403
        assert client.post(f"/api/routines/{earlier}/resume").status_code == 403
    row = next(r for r in routines.list_routines(_viewer("mira")) if r["id"] == rid)
    assert row["agent"] == ""


def test_a_bad_schedule_is_refused_not_rounded_or_crashed(people):
    from datetime import date

    for bad, text in (
        ({"every_weeks": 0}, "every_weeks must be a whole number from 1 to 4"),
        ({"starts_on": "9999-12-31"}, "starts_on must be within the next year"),
        ({"starts_on": (date.today() + timedelta(days=400)).isoformat()}, "within the next year"),
    ):
        with pytest.raises(ValueError, match=text):
            _routine(**bad)


def test_a_rename_locks_routines_before_crews():
    """users.rename_user rewrites _ATTRIBUTION in order. A firing holds its
    routine row, then its crew row (crews.assert_writable): crews first in
    the rename is a lock cycle, and one of the two dies as a deadlock."""
    from app.services import users

    order = list(users._ATTRIBUTION)
    assert order.index("routines") < order.index("crews")
    assert order.index("routines") < order.index("crew_members")


def test_a_deactivated_owner_cannot_create_or_resume(people, fresh_db):
    from app.services import routines, users

    rid = _routine()["id"]
    users.set_active("mira", False, actor="bo")
    with pytest.raises(PermissionError, match="mira is deactivated"):
        routines.resume_routine(rid, actor="mira")
    with pytest.raises(PermissionError, match="mira is deactivated"):
        _routine(title="Another")
    assert _row(fresh_db, rid)["status"] == "paused"


def test_an_edit_cannot_empty_a_field_or_bend_a_date(people):
    from app.services import routines

    rid = _routine()["id"]
    with pytest.raises(ValueError, match="starts_on cannot be empty"):
        routines.update_routine(rid, {"starts_on": None}, actor="mira")
    with pytest.raises(ValueError, match="every_weeks cannot be empty"):
        routines.update_routine(rid, {"every_weeks": None}, actor="mira")
    with pytest.raises(ValueError, match="starts_on must be YYYY-MM-DD"):
        _routine(starts_on="20261005")
    # due_days is the one field an edit may clear
    routines.update_routine(rid, {"due_days": 2}, actor="mira")
    routines.update_routine(rid, {"due_days": None}, actor="mira")


def test_a_deactivated_crew_pauses_with_its_own_reason(people, fresh_db):
    from app.services import crews, routines

    rid = _routine(visibility="crew", crew_id=people["crew"])["id"]
    crews.update_crew(people["crew"], active=False, actor="mira")
    assert routines.fire_due(rid, _due(fresh_db, rid)) == "paused"
    assert _row(fresh_db, rid)["paused_reason"] == "crew_inactive"
    assert len(_notices(fresh_db, "mira", "%because its crew is deactivated%")) == 1
    assert _notices(fresh_db, "mira", "%no longer in its crew%") == []


def test_an_owner_who_left_the_crew_cannot_edit_it(people):
    from app.services import crews, routines

    crews.add_member(people["crew"], "bo", actor="mira")
    rid = _routine(actor="bo", visibility="crew", crew_id=people["crew"])["id"]
    crews.remove_member(people["crew"], "bo", actor="mira")
    with pytest.raises(ValueError, match="You are no longer in the crew of routine"):
        routines.update_routine(rid, {"title": "Written after leaving"}, actor="bo")


def test_a_week_count_must_be_a_number(client, people):
    from conftest import _strong

    sent = client.post(
        "/api/routines", json={**WEEKLY, "every_weeks": True}, headers=_strong(name="mira")
    )
    assert sent.status_code == 422
