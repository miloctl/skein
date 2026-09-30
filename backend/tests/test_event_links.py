"""Items that came out of a meeting: the event_id link on seven kinds, its
tier containment, the meeting's tier stamped on ingested proposals, and every
reader that must hide a link to a meeting it cannot read."""

import json
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from conftest import _strong
from fastapi.testclient import TestClient

from app import config, db
from app.services import scope


@pytest.fixture()
def people(fresh_db):
    from app.services import crews, users

    for name in ("ana", "ben", "tester"):
        users.ensure_user(name)
    ops = crews.create_crew("ops", actor="ana")["id"]
    lab = crews.create_crew("lab", actor="ana")["id"]
    crews.add_member(ops, "tester", actor="ana")
    return {"ops": ops, "lab": lab}


def _meeting(title="sync", actor="ana", **kw):
    from app.services import schedule

    return schedule.schedule_event(title, "2026-10-02T10:00", actor=actor, **kw)["id"]


def test_an_item_is_never_wider_than_its_meeting(people):
    from app.services import collab, work

    private = _meeting(visibility="private")
    with pytest.raises(ValueError, match="cannot be visible to more people"):
        work.create_task("team task", actor="ana", event_id=private)
    ops_meeting = _meeting(visibility="crew", crew_id=people["ops"])
    with pytest.raises(ValueError, match="cannot be visible to more people"):
        collab.save_note(
            "lab notes",
            "x",
            actor="ana",
            visibility="crew",
            crew_id=people["lab"],
            event_id=ops_meeting,
        )
    # narrower is fine: only the author reads a private row
    team = _meeting()
    nid = collab.save_note("mine", "x", actor="ana", visibility="private", event_id=team)["id"]
    assert db.query_one("SELECT event_id FROM notes WHERE id = ?", (nid,))["event_id"] == team


def test_a_hidden_meeting_reads_like_an_absent_one(people):
    from app.services import work

    hidden = _meeting(visibility="private")
    with pytest.raises(ValueError) as refused:
        work.create_task("ben's task", actor="ben", event_id=hidden)
    with pytest.raises(ValueError) as absent:
        work.create_task("ben's task", actor="ben", event_id=999999)
    assert str(refused.value).replace(str(hidden), "N") == str(absent.value).replace("999999", "N")


def test_a_cancelled_meeting_keeps_what_came_out_of_it(people):
    from app.services import promises, schedule, work

    eid = _meeting()
    tid = work.create_task("follow up", actor="ana", event_id=eid)["id"]
    pid = promises.add_promise("send the deck", "Acme", actor="ana", event_id=eid)["id"]
    schedule.cancel_event(eid, actor="ana")
    assert db.query_one("SELECT event_id FROM tasks WHERE id = ?", (tid,))["event_id"] is None
    assert db.query_one("SELECT event_id FROM promises WHERE id = ?", (pid,))["event_id"] is None


def test_ingested_lines_take_the_meetings_tier(client, people):
    eid = _meeting("ops review", actor="tester", visibility="crew", crew_id=people["ops"])
    strong = _strong(client)
    r = client.post(
        "/api/ingest",
        json={"text": "todo: draft the rollout plan", "event_id": eid},
        headers=strong,
    )
    assert r.status_code == 200, r.text
    (proposal,) = r.json()["proposals"]
    approved = client.post(f"/api/review/{proposal['id']}/approve", json={}, headers=strong)
    assert approved.json()["status"] == "approved", approved.text
    task = db.query_one("SELECT * FROM tasks WHERE title = 'draft the rollout plan'")
    # without the stamp the task lands at the workspace tier, and the crew
    # meeting refuses the link at approval
    assert (task["visibility"], task["crew_id"], task["event_id"]) == ("crew", people["ops"], eid)


def test_ingest_refuses_a_meeting_the_paster_cannot_read(client, people):
    hidden = _meeting(visibility="private")
    r = client.post("/api/ingest", json={"text": "todo: something real", "event_id": hidden})
    assert r.status_code == 404
    assert client.post("/api/ingest", json={"text": "todo: x y z", "event": 1}).status_code == 422


def test_an_approval_after_the_meeting_is_gone_waits_with_a_reason(client, people):
    from app.services import schedule

    eid = _meeting(actor="tester")
    strong = _strong(client)
    (proposal,) = client.post(
        "/api/ingest", json={"text": "decision: ship on friday", "event_id": eid}, headers=strong
    ).json()["proposals"]
    schedule.cancel_event(eid, actor="tester")
    client.post(f"/api/review/{proposal['id']}/approve", json={}, headers=strong)
    row = db.query_one(
        "SELECT status, review_note FROM pending_changes WHERE id = ?", (proposal["id"],)
    )
    assert row["status"] == "pending" and f"no event #{eid}" in row["review_note"]


def test_the_meeting_lists_only_what_its_reader_may_read(client, people):
    from app.services import collab, schedule, work

    eid = _meeting()
    work.create_task("team follow-up", actor="ana", event_id=eid)
    collab.ask_question("who owns it?", "ana", actor="ana", event_id=eid)
    work.create_task("ben's own", actor="ben", visibility="private", event_id=eid)
    items = client.get(f"/api/events/{eid}/items").json()
    assert [t["title"] for t in items["task"]] == ["team follow-up"]
    assert [q["title"] for q in items["question"]] == ["who owns it?"]
    # a count that included ben's private task would tell the reader it exists
    assert schedule.linked_counts([eid], scope.Viewer("tester", True)) == {eid: 2}
    assert schedule.linked_counts([eid], scope.Viewer("ben", True)) == {eid: 3}


def test_my_day_says_what_came_out_of_a_meeting(client, people):
    from app.services import work

    eid = _meeting(actor="tester")
    db.execute(
        "UPDATE events SET starts_at = ?, created_at = ? WHERE id = ?",
        (
            (datetime.now(UTC) - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M"),
            (datetime.now(UTC) - timedelta(days=1)).isoformat(),
            eid,
        ),
    )
    work.create_task("first", actor="tester", event_id=eid)
    work.create_task("second", actor="tester", event_id=eid)
    items = client.get("/api/briefing").json()["attention"]
    meeting = next(i for i in items if i["kind"] == "meeting")
    assert "2 items came out of it" in meeting["reason"]


def test_link_and_unlink_after_the_fact(client, people):
    from app.services import collab

    eid = _meeting(actor="tester")
    did = collab.record_decision("go", "we go", decided_by="tester", actor="tester")["id"]
    r = client.post(f"/api/events/{eid}/links", json={"kind": "decision", "item_id": did})
    assert r.status_code == 200
    assert [d["id"] for d in client.get(f"/api/events/{eid}/items").json()["decision"]] == [did]
    assert (
        client.post(f"/api/events/{eid}/links", json={"kind": "lesson", "item_id": did}).status_code
        == 400
    )
    assert client.delete(f"/api/events/{eid}/links/decision/{did}").status_code == 200
    assert client.get(f"/api/events/{eid}/items").json()["decision"] == []
    again = client.delete(f"/api/events/{eid}/links/decision/{did}")
    assert again.status_code == 400 and "not linked" in again.json()["detail"]


def test_a_share_waits_for_its_meeting(client, people):
    from app.services import collab

    eid = _meeting(actor="tester", visibility="private")
    nid = collab.save_note(
        "prep", "x", author="tester", actor="tester", visibility="private", event_id=eid
    )["id"]
    r = client.post(f"/api/share/notes/{nid}", headers=_strong(client))
    assert r.status_code == 400 and "fewer people can see" in r.json()["detail"]


def test_a_task_hides_a_meeting_its_reader_left(client, people):
    from app.services import crews, work

    eid = _meeting(visibility="crew", crew_id=people["ops"])
    tid = work.create_task("my prep", actor="tester", visibility="private", event_id=eid)["id"]
    strong = _strong(client)
    assert client.get(f"/api/tasks/{tid}", headers=strong).json()["event_id"] == eid
    crews.remove_member(people["ops"], "tester", actor="ana")
    assert client.get(f"/api/tasks/{tid}", headers=strong).json()["event_id"] is None
    listed = client.get("/api/tasks", headers=strong).json()
    assert next(t for t in listed if t["id"] == tid)["event_id"] is None
    # the joined reads the agent tools use, without the REST projection
    viewer = scope.Viewer("tester", True)
    assert work.get_task(tid, viewer)["event_id"] is None
    assert next(t for t in work.list_tasks_joined(viewer) if t["id"] == tid)["event_id"] is None
    # and the raw rows My Day, the briefs and the export pass through
    raw = db.query_one("SELECT * FROM tasks WHERE id = ?", (tid,))
    assert work.redact_task_relationships([raw], viewer)[0]["event_id"] is None


def test_the_export_drops_a_link_to_a_meeting_it_leaves_out(people):
    from app.services import admin, collab

    team = _meeting()
    private = _meeting(visibility="private")
    linked = collab.save_note("kept", "x", actor="ana", event_id=team)["id"]
    legacy = collab.save_note("legacy", "x", actor="ana")["id"]
    db.execute("UPDATE notes SET event_id = ? WHERE id = ?", (private, legacy))
    path = admin.export()["path"]
    with open(path) as f:
        dump = json.load(f)
    notes = {row["id"]: row for row in dump["notes"]}
    assert notes[linked]["event_id"] == team
    assert notes[legacy]["event_id"] is None


def test_an_agent_links_at_creation(client, people, monkeypatch):
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import users
    from app.tools.work import create_task as create_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("scout", kind="agent")
    eid = _meeting()
    token = set_agent_identity("scout")
    try:
        out = json.loads(create_tool(title="book the room", event_id=eid))
    finally:
        reset_agent_identity(token)
    approved = client.post(f"/api/review/{out['id']}/approve", json={}, headers=_strong(client))
    assert approved.json()["status"] == "approved", approved.text
    row = db.query_one("SELECT event_id FROM tasks WHERE title = 'book the room'")
    assert row["event_id"] == eid


def test_the_items_honor_the_project_rule(fresh_db):
    from test_policy_axis import CANARY, _app, _engagements

    from app.services import work

    std, reg = _engagements("mallory")
    eid = _meeting(actor="mallory", engagement_id=std)
    work.create_task("standard follow-up", engagement_id=std, actor="mallory", event_id=eid)
    work.create_task(f"follow-up {CANARY}", engagement_id=reg, actor="mallory", event_id=eid)
    with TestClient(_app(), headers={"X-User": "mallory"}) as c:
        r = c.get(f"/api/events/{eid}/items")
        assert r.status_code == 200 and CANARY not in r.text and "standard follow-up" in r.text


def test_the_card_ties_on_a_row_filed_with_its_meeting(people):
    from app.services import fieldguide, work

    work.create_task("unlinked", actor="ana")
    assert not fieldguide.PREDICATES["meeting_links"]("ana")
    work.create_task("linked", actor="ana", event_id=_meeting())
    assert fieldguide.PREDICATES["meeting_links"]("ana")


def test_a_late_link_is_never_wider_than_its_meeting(client, people):
    from app.services import collab, schedule

    private = _meeting(actor="tester", visibility="private")
    nid = collab.save_note("team note", "x", author="tester", actor="tester")["id"]
    with pytest.raises(ValueError, match="cannot be visible to more people"):
        schedule.link_item(private, "note", nid, actor="tester")
    # through the route, another person's meeting reads like an absent one
    hidden = _meeting(visibility="private")
    refused = client.post(f"/api/events/{hidden}/links", json={"kind": "note", "item_id": nid})
    absent = client.post("/api/events/999999/links", json={"kind": "note", "item_id": nid})
    assert refused.status_code == absent.status_code == 400
    assert refused.json()["detail"].replace(str(hidden), "N") == absent.json()["detail"].replace(
        "999999", "N"
    )


def test_a_link_is_judged_on_the_items_project(fresh_db):
    from test_policy_axis import _app, _engagements

    from app.services import work

    std, reg = _engagements("mallory")
    eid = _meeting(actor="mallory", engagement_id=std)
    regulated = work.create_task("regulated work", engagement_id=reg, actor="mallory")["id"]
    with TestClient(_app(), headers={"X-User": "mallory"}) as c:
        r = c.post(f"/api/events/{eid}/links", json={"kind": "task", "item_id": regulated})
        assert r.status_code == 403
    assert db.query_one("SELECT event_id FROM tasks WHERE id = ?", (regulated,))["event_id"] is None


def test_the_count_honors_the_policy(people):
    from app.services import schedule, work

    eid = _meeting()
    work.create_task("kept", actor="ana", event_id=eid)
    denied = work.create_task("denied", actor="ana", event_id=eid)["id"]

    def rule(entity, entity_id, _attributes):
        return not (entity == "task" and entity_id == denied)

    viewer = scope.Viewer("tester", True)
    assert schedule.linked_counts([eid], viewer) == {eid: 2}
    assert schedule.linked_counts([eid], viewer, resource_filter=rule) == {eid: 1}


def test_a_void_task_leaves_the_meeting(client, people):
    from app.services import schedule, work

    eid = _meeting()
    tid = work.create_task("never mind", actor="ana", event_id=eid)["id"]
    work.update_task(tid, status="void", actor="ana")
    assert client.get(f"/api/events/{eid}/items").json()["task"] == []
    assert schedule.linked_counts([eid], scope.Viewer("tester", True)) == {}


@pytest.mark.parametrize("kind", ["task", "decision", "promise", "intake"])
def test_every_kind_waits_for_its_meeting_to_share(client, people, kind):
    from app.services import collab, intake, promises, work

    eid = _meeting(actor="tester", visibility="private")
    k = {"actor": "tester", "visibility": "private", "event_id": eid}
    table, row = {
        "task": ("tasks", lambda: work.create_task("prep", **k)),
        "decision": ("decisions", lambda: collab.record_decision("go", "we go", **k)),
        "promise": ("promises", lambda: promises.add_promise("deck", "Acme", **k)),
        "intake": ("requests", lambda: intake.submit_request("ask", **k)),
    }[kind]
    rid = row()["id"]
    r = client.post(f"/api/share/{table}/{rid}", headers=_strong(client))
    assert r.status_code == 400 and "fewer people can see" in r.json()["detail"]


def test_an_agent_cannot_queue_a_meeting_it_cannot_link(client, people, monkeypatch):
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import users
    from app.tools.work import create_task as create_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    users.ensure_user("scout", kind="agent")
    for meeting in (
        _meeting(visibility="crew", crew_id=people["ops"]),
        _meeting(visibility="private"),
        999999,
    ):
        token = set_agent_identity("scout")
        try:
            out = json.loads(create_tool(title="book the room", event_id=meeting))
        finally:
            reset_agent_identity(token)
        # refused before the queue: a proposal naming the meeting would put
        # its id in front of every reader of the queue, and never apply
        assert "error" in out, out
    assert db.query_one("SELECT COUNT(*) AS n FROM pending_changes")["n"] == 0


def test_an_agent_cannot_file_under_a_meeting_the_policy_denies(fresh_db):
    from test_policy_axis import _deny_regulated, _engagements, _module

    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.extensions.policy import reset_policy_engine, set_policy_engine
    from app.extensions.registry import ExtensionRegistry
    from app.services import users
    from app.tools.work import create_task as create_tool

    std, reg = _engagements("mallory")
    regulated = _meeting(actor="mallory", engagement_id=reg)
    users.ensure_user("scout", kind="agent")
    engine = set_policy_engine(ExtensionRegistry.build((_module(_deny_regulated),)).policy_engine)
    token = set_agent_identity("scout")
    try:
        denied = json.loads(create_tool(title="under the regulated meeting", event_id=regulated))
        allowed = json.loads(create_tool(title="standard work", engagement_id=std))
    finally:
        reset_agent_identity(token)
        reset_policy_engine(engine)
    assert "denied" in denied.get("error", "").lower(), denied
    assert "error" not in allowed, allowed
    assert db.query_one("SELECT id FROM tasks WHERE title = 'under the regulated meeting'") is None


def test_ingest_judges_the_meeting_and_the_paster(client, people):
    from test_policy_axis import _app, _engagements

    from app.services import ingest, leases

    original_stop = leases._stop
    original_threads = (leases._thread, leases._sweeper)
    try:
        _std, reg = _engagements("mallory")
        regulated = _meeting(actor="mallory", engagement_id=reg)
        # client owns the lifespan. A second entry replaces its lease maintenance handles.
        with closing(TestClient(_app(), headers={"X-User": "mallory"})) as c:
            r = c.post("/api/ingest", json={"text": "todo: under it", "event_id": regulated})
            assert r.status_code == 403
            assert leases._stop is original_stop
            assert (leases._thread, leases._sweeper) == original_threads
            assert all(thread.is_alive() for thread in original_threads)
        assert leases._stop is original_stop
        assert (leases._thread, leases._sweeper) == original_threads
        assert all(thread.is_alive() for thread in original_threads)
        # a weak name reads the workspace tier only: its own crew's meeting reads
        # like an absent one, and nothing is proposed into a queue nobody reads
        crew_meeting = _meeting(actor="tester", visibility="crew", crew_id=people["ops"])
        refused = client.post("/api/ingest", json={"text": "todo: x y z", "event_id": crew_meeting})
        absent = client.post("/api/ingest", json={"text": "todo: x y z", "event_id": 999999})
        assert refused.status_code == absent.status_code == 404
        assert db.query_one("SELECT COUNT(*) AS n FROM pending_changes")["n"] == 0
        # the service holds the same line for any caller that is not a strong one
        with pytest.raises(ValueError, match=f"no event #{crew_meeting}"):
            ingest.ingest_notes("todo: x y z", actor="tester", private=False, event_id=crew_meeting)
    finally:
        # A nested lifespan replaces the stop handle. Keep the original so a
        # regression cannot leave maintenance running against the next database.
        original_stop.set()
        for thread in original_threads:
            thread.join(5)
            assert not thread.is_alive()


def test_an_assignee_who_cannot_read_the_meeting_is_handed_back(client, people):
    eid = _meeting(actor="tester", visibility="private")
    r = client.post(
        "/api/ingest",
        json={"text": "q: ana: can you own the rollout?\ntodo: book the room", "event_id": eid},
        headers=_strong(client),
    )
    body = r.json()
    assert [p["kind"] for p in body["proposals"]] == ["task"]
    assert any("own the rollout" in line for line in body["unclassified"])


def test_rescheduling_and_approving_do_not_deadlock(fresh_db, monkeypatch):
    """schedule.update_event holds the event, then its new engagement. The
    approval of a create that names both held the engagement, then the
    event, and the two deadlocked when they met."""
    import threading

    from app.services import engagements, policy_context, review, schedule

    gid = engagements.create_engagement("Atlas", actor="ops")["id"]
    eid = schedule.schedule_event("sync", "2026-10-02T10:00", actor="ops")["id"]
    proposal = review.propose_change(
        "task", "create", {"title": "ship", "engagement_id": gid, "event_id": eid}, actor="scribe"
    )
    held, moved = threading.Event(), threading.Event()
    real = policy_context.hold_resource

    def pausing(entity, entity_id):
        real(entity, entity_id)
        if threading.current_thread().name == "approver" and not held.is_set():
            held.set()
            moved.wait(1.5)

    monkeypatch.setattr(policy_context, "hold_resource", pausing)
    errors: list[BaseException] = []

    def approve():
        try:
            review.approve_change(proposal["id"], actor="ops", strong=True)
        except BaseException as exc:
            errors.append(exc)

    def relink():
        assert held.wait(5)
        try:
            schedule.update_event(eid, engagement_id=gid, actor="ops")
        except BaseException as exc:
            errors.append(exc)
        finally:
            moved.set()

    threads = [threading.Thread(target=approve, name="approver"), threading.Thread(target=relink)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert errors == []
    assert db.query_one("SELECT status FROM pending_changes WHERE id = ?", (proposal["id"],)) == {
        "status": "approved"
    }


def test_an_event_edit_decides_and_writes_in_one_transaction(client, fresh_db, monkeypatch):
    """The workplace rule judges the event's engagement. Decided outside the
    write's transaction, a relink that lands in between moves the edit onto
    work the rule denies."""
    from app.services import policy_context, schedule

    eid = schedule.schedule_event("sync", "2026-10-02T10:00", actor="tester")["id"]
    seen = []
    real = policy_context.hold_resource

    def spy(entity, entity_id):
        seen.append((entity, entity_id, db.in_transaction()))
        real(entity, entity_id)

    monkeypatch.setattr(policy_context, "hold_resource", spy)
    assert client.patch(f"/api/events/{eid}", json={"title": "renamed"}).status_code == 200
    assert ("event", eid, True) in seen
