"""Comment threads on tasks, decisions and blockers
(docs/intent/task-threads.md). A comment takes its parent's tier, a reader
who cannot see the parent sees nothing of the thread, and the ledger carries
ids, never the text."""

import pytest
from conftest import _strong

from app import db
from app.services import blockers, collab, comments, crews, users, work

PARENTS = ("tasks", "decisions", "blockers")


def _crew() -> int:
    for name in ("ava", "mira", "dana", "bo"):
        users.ensure_user(name)
    cid = crews.create_crew("Platform", actor="ava")["id"]
    for member in ("mira", "dana"):
        crews.add_member(cid, member, actor="ava")
    return cid


def _parent(kind: str, *, actor: str = "ava", crew: int = 0) -> int:
    tier = {"visibility": "crew", "crew_id": crew} if crew else {}
    if kind == "tasks":
        return work.create_task("Fix login", actor=actor, **tier)["id"]
    if kind == "decisions":
        return collab.record_decision("Freeze", "Freeze Friday", actor=actor, **tier)["id"]
    return blockers.raise_blocker("Staging is down", actor=actor, **tier)["id"]


def _post(client, kind: str, pid: int, body: str, who: str = "ava"):
    return client.post(
        f"/api/{kind}/{pid}/comments", json={"body": body}, headers=_strong(client, who)
    )


def _notices(person: str) -> list[str]:
    return [
        r["message"]
        for r in db.query(
            'SELECT message FROM notifications WHERE "user" = ? ORDER BY id', (person,)
        )
    ]


def test_a_crew_comment_never_notifies_a_non_member(client, fresh_db):
    """Without the comment's own tier on the mention, bo was told that a
    crew thread named him, and opening it answered 404."""
    crew = _crew()
    task = _parent("tasks", crew=crew)
    posted = _post(client, "tasks", task, "@bo and @dana, check the rollout")
    assert posted.status_code == 200, posted.text
    assert posted.json()["notified"] == ["dana"]
    assert _notices("bo") == []
    # a second comment is a new row, so naming dana again tells her again
    _post(client, "tasks", task, "@dana ping")
    assert len(_notices("dana")) == 2
    assert _notices("dana")[0] == f"ava mentioned you in a comment on task #{task}."


@pytest.mark.parametrize("kind", PARENTS)
def test_a_non_reader_gets_the_absent_id_answer_everywhere(client, fresh_db, kind):
    crew = _crew()
    pid = _parent(kind, crew=crew)
    cid = _post(client, kind, pid, "crew only").json()["id"]
    bo = _strong(client, "bo")
    for method, path, body in (
        ("get", f"/api/{kind}/{pid}/comments", None),
        ("post", f"/api/{kind}/{pid}/comments", {"body": "hello"}),
    ):
        hidden = client.request(method, path, json=body, headers=bo)
        absent = client.request(method, path.replace(f"/{pid}/", "/999999/"), json=body, headers=bo)
        assert hidden.status_code == absent.status_code == 404, (method, hidden.text)
        assert hidden.text.replace(str(pid), "N") == absent.text.replace("999999", "N")
    for method, body in (("patch", {"body": "x"}), ("delete", None)):
        hidden = client.request(method, f"/api/comments/{cid}", json=body, headers=bo)
        absent = client.request(method, "/api/comments/999999", json=body, headers=bo)
        assert hidden.status_code == absent.status_code == 404, (method, hidden.text)
        assert hidden.text.replace(str(cid), "N") == absent.text.replace("999999", "N")
    assert db.query_one("SELECT body FROM comments WHERE id = ?", (cid,))["body"] == "crew only"


def test_the_ledger_carries_ids_and_never_the_text(client, fresh_db):
    task = _parent("tasks")
    cid = _post(client, "tasks", task, "the vendor is Contoso").json()["id"]
    client.patch(
        f"/api/comments/{cid}",
        json={"body": "the vendor is Fabrikam"},
        headers=_strong(client, "ava"),
    )
    client.delete(f"/api/comments/{cid}", headers=_strong(client, "ava"))
    details = [
        r["detail"]
        for r in db.query(
            "SELECT detail FROM activity WHERE action IN"
            " ('post_comment', 'edit_comment', 'delete_comment') ORDER BY id"
        )
    ]
    assert details == [f"task #{task} comment #{cid}"] * 3


def test_only_the_author_edits_and_a_person_may_delete_an_agents_comment(client, fresh_db):
    users.ensure_user("raj")
    users.ensure_user("scout", kind="agent")
    task = _parent("tasks")
    mine = _post(client, "tasks", task, "ava's comment").json()["id"]
    raj = _strong(client, "raj")
    assert client.patch(f"/api/comments/{mine}", json={"body": "x"}, headers=raj).status_code == 403
    assert client.delete(f"/api/comments/{mine}", headers=raj).status_code == 403
    agents = comments.add_comment("wrong guess", task_id=task, actor="scout", origin="agent")["id"]
    assert client.delete(f"/api/comments/{agents}", headers=raj).status_code == 200
    assert client.delete(f"/api/comments/{mine}", headers=_strong(client, "ava")).status_code == 200
    rows = {
        r["id"]: r
        for r in db.query("SELECT id, body, deleted_by, deleted_at FROM comments ORDER BY id")
    }
    assert (rows[agents]["body"], rows[agents]["deleted_by"]) == ("", "raj")
    assert (rows[mine]["body"], rows[mine]["deleted_by"]) == ("", "ava")
    listed = client.get(f"/api/tasks/{task}/comments", headers=raj).json()
    assert [(c["body"], c["deleted_by"]) for c in listed] == [("", "ava"), ("", "raj")]
    edited = client.patch(
        f"/api/comments/{mine}", json={"body": "back"}, headers=_strong(client, "ava")
    )
    assert edited.status_code == 400
    assert edited.json()["detail"] == "This comment is deleted. Post a new comment."


def test_the_table_refuses_a_tombstone_with_text_and_a_comment_in_two_threads(fresh_db):
    import psycopg

    task, decision = _parent("tasks"), _parent("decisions")
    cid = comments.add_comment("text", task_id=task, actor="ava")["id"]
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("UPDATE comments SET deleted_at = ? WHERE id = ?", (db.now(), cid))
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute(
            "INSERT INTO comments (task_id, decision_id, created_by, origin, body, created_at)"
            " VALUES (?, ?, 'ava', 'human', 'x', ?)",
            (task, decision, db.now()),
        )


def test_an_edit_is_marked_and_tells_only_a_newly_named_person(client, fresh_db):
    for name in ("dana", "raj"):
        users.ensure_user(name)
    task = _parent("tasks")
    cid = _post(client, "tasks", task, "@dana look").json()["id"]
    edited = client.patch(
        f"/api/comments/{cid}",
        json={"body": "@dana look, @raj too"},
        headers=_strong(client, "ava"),
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["notified"] == ["raj"]
    row = client.get(f"/api/tasks/{task}/comments", headers=_strong(client, "ava")).json()[0]
    assert row["edited_at"] and row["body"] == "@dana look, @raj too"
    assert (len(_notices("dana")), len(_notices("raj"))) == (1, 1)


def test_invisible_format_characters_are_refused(client, fresh_db):
    task = _parent("tasks")
    assert _post(client, "tasks", task, "ok\u202eevil").status_code == 400
    cid = _post(client, "tasks", task, "ok").json()["id"]
    edit = client.patch(
        f"/api/comments/{cid}", json={"body": "ok\u200b"}, headers=_strong(client, "ava")
    )
    assert edit.status_code == 400


def test_a_comment_keeps_its_tier_when_the_parent_is_shared(client, fresh_db):
    """sharing.py only widens a row, and a comment written for the crew
    stays with the crew: its author chose that audience."""
    crew = _crew()
    task = _parent("tasks", crew=crew)
    cid = _post(client, "tasks", task, "crew talk").json()["id"]
    assert (
        client.post(f"/api/share/tasks/{task}", headers=_strong(client, "ava")).status_code == 200
    )
    tiers = db.query_one(
        "SELECT c.visibility AS comment, t.visibility AS task FROM comments c"
        " JOIN tasks t ON t.id = c.task_id WHERE c.id = ?",
        (cid,),
    )
    assert (tiers["comment"], tiers["task"]) == ("crew", "workspace")
    listed = client.get(f"/api/tasks/{task}/comments", headers=_strong(client, "bo")).json()
    assert listed == []


def test_deleting_a_private_task_from_your_data_takes_its_comments(client, fresh_db):
    ava = _strong(client, "ava")
    task = work.create_task("Private plan", actor="ava", visibility="private")["id"]
    _post(client, "tasks", task, "note to self")
    assert client.delete(f"/api/my-data/tasks/{task}", headers=ava).status_code == 200
    assert db.query_one("SELECT 1 FROM comments") is None


def test_a_comment_does_not_move_its_task(client, fresh_db):
    """A comment is talk, not work: a fresh-looking task would keep the
    stale-work findings from firing on stalled work."""
    task = _parent("tasks")
    # db.now() has seconds: an old stamp, so a bump in this second shows
    db.execute("UPDATE tasks SET updated_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (task,))
    _post(client, "tasks", task, "still alive?")
    assert db.query_one("SELECT updated_at FROM tasks WHERE id = ?", (task,))["updated_at"] == (
        "2020-01-01T00:00:00+00:00"
    )


def _delegated(crew: int = 0) -> int:
    """A task delegated to scout by ava, as the REST door does it."""
    from app.services import delegation

    users.ensure_user("ava")
    users.ensure_user("scout", kind="agent")
    tier = {"visibility": "crew", "crew_id": crew} if crew else {}
    task = work.create_task("Fix login", actor="ava", **tier)["id"]
    delegation.delegate_task(task, "scout", "ava", actor="ava", origin="human")
    # the delegation's own wake is not what these tests count
    db.execute("DELETE FROM agent_wakeups")
    return task


def _wakes() -> list[tuple[str, int, str]]:
    return [
        (r["agent"], r["trigger_task_id"], r["status"])
        for r in db.query("SELECT agent, trigger_task_id, status FROM agent_wakeups")
    ]


def test_a_person_naming_the_delegate_wakes_it_once(client, fresh_db):
    task = _delegated()
    first = _post(client, "tasks", task, "@scout the target is 17, see decision #41")
    assert first.json()["woke"] == "scout"
    _post(client, "tasks", task, "@scout and please hurry")
    assert _wakes() == [("scout", task, "pending")]


def test_a_crew_tasks_delegate_is_woken_though_no_notice_reaches_it(client, fresh_db):
    """An agent holds no crews, so the mention scan never reaches it on a
    crew task. The wake must not inherit that gap."""
    crew = _crew()
    task = _delegated(crew)
    assert _post(client, "tasks", task, "@scout check this").json()["woke"] == "scout"
    assert _wakes() == [("scout", task, "pending")]


def test_what_never_wakes_the_delegate(client, fresh_db):
    users.ensure_user("helper", kind="agent")
    task = _delegated()
    # an agent's own words never start an unattended chain
    comments.add_comment("@scout loop", task_id=task, actor="helper", origin="agent")
    # an agent that is not the delegate gets the notice and no turn
    other = _post(client, "tasks", task, "@helper can you look?")
    assert (other.json()["woke"], other.json()["notified"]) == ("", ["helper"])
    # a name is not a mention, and neither is one inside code
    assert _post(client, "tasks", task, "the scout run looked fine").json()["woke"] == ""
    assert _post(client, "tasks", task, "run `@scout --dry`").json()["woke"] == ""
    # an edit that adds the delegate wakes nothing
    cid = _post(client, "tasks", task, "a note").json()["id"]
    client.patch(
        f"/api/comments/{cid}", json={"body": "a note @scout"}, headers=_strong(client, "ava")
    )
    assert _wakes() == []
    # a finished task has no turn to give
    work.update_task(task, status="done", actor="ava")
    assert _post(client, "tasks", task, "@scout thanks").json()["woke"] == ""
    assert _wakes() == []


def _thread(person: str) -> list[str]:
    return [m for m in _notices(person) if " commented on " in m]


def test_a_reply_tells_whoever_already_wrote_in_the_thread(client, fresh_db):
    """Raj asks, Dana answers without naming him, and Raj is told once.
    A second reply while his notice is unread tells him nothing more."""
    for name in ("raj", "dana"):
        users.ensure_user(name)
    task = work.create_task("Fix login", actor="ava")["id"]
    _post(client, "tasks", task, "Is the freeze window fixed?", who="raj")
    _post(client, "tasks", task, "Yes, Friday.", who="dana")
    assert _thread("raj") == [f"dana commented on task #{task}."]
    _post(client, "tasks", task, "And Monday is open.", who="dana")
    assert len(_thread("raj")) == 1
    # the task's own creator is not a named party of a task: its assignee and
    # sponsor are, and neither is set here
    assert _thread("ava") == []


def test_a_named_party_hears_once_and_a_mention_is_not_doubled(client, fresh_db):
    for name in ("raj", "dana"):
        users.ensure_user(name)
    task = work.create_task("Fix login", actor="ava", assignee="raj")["id"]
    _post(client, "tasks", task, "@raj is this blocked?", who="dana")
    assert len(_notices("raj")) == 1
    assert _notices("raj")[0].startswith("dana mentioned you")


def test_a_decision_thread_tells_its_decider(client, fresh_db):
    for name in ("ava", "raj", "mira"):
        users.ensure_user(name)
    decision = collab.record_decision("Freeze", "Freeze Friday", decided_by="mira", actor="ava")[
        "id"
    ]
    _post(client, "decisions", decision, "Why does this still hold?", who="raj")
    assert _thread("mira") == [f"raj commented on decision #{decision}."]
    assert _thread("ava") == [f"raj commented on decision #{decision}."]


def test_no_thread_notice_reaches_an_agent_or_someone_who_left_the_crew(client, fresh_db):
    crew = _crew()
    users.ensure_user("scout", kind="agent")
    task = _parent("tasks", crew=crew)
    _post(client, "tasks", task, "first", who="dana")
    crews.remove_member(crew, "dana", actor="ava")
    comments.add_comment("agent note", task_id=task, actor="scout", origin="agent")
    _post(client, "tasks", task, "reply", who="mira")
    assert _thread("dana") == [] and _thread("scout") == []


def test_an_agent_that_wrote_in_a_thread_gets_no_thread_notice(client, fresh_db):
    """Its inbox lists what it has not answered (comments.unanswered_for), and
    a notice on top of that is a second copy of the same ask."""
    users.ensure_user("scout", kind="agent")
    task = _parent("tasks")
    comments.add_comment("agent note", task_id=task, actor="scout", origin="agent")
    _post(client, "tasks", task, "thanks")
    assert _thread("scout") == []


def test_a_blocker_owned_by_the_team_sends_no_notice_to_a_name_nobody_has(client, fresh_db):
    """A CI-red blocker is owned by `team`, which is not a person. A notice
    to it lands on a name every lookup of "who is this" treats as nobody."""
    users.ensure_user("dana")
    blocker = blockers.raise_blocker("CI red", owner="team", actor="ava")["id"]
    _post(client, "blockers", blocker, "looking now", who="dana")
    assert db.query_one("SELECT 1 FROM notifications WHERE \"user\" = 'team'") is None


def test_a_person_cannot_edit_an_agents_comment_or_delete_anothers_tombstone(client, fresh_db):
    users.ensure_user("raj")
    users.ensure_user("scout", kind="agent")
    task = _parent("tasks")
    agents = comments.add_comment("agent words", task_id=task, actor="scout", origin="agent")["id"]
    raj = _strong(client, "raj")
    assert (
        client.patch(f"/api/comments/{agents}", json={"body": "x"}, headers=raj).status_code == 403
    )
    mine = _post(client, "tasks", task, "ava's").json()["id"]
    client.delete(f"/api/comments/{mine}", headers=_strong(client, "ava"))
    assert client.delete(f"/api/comments/{mine}", headers=raj).status_code == 403
    rows = client.get(f"/api/tasks/{task}/comments", headers=raj).json()
    flags = {r["id"]: (r["can_edit"], r["can_delete"]) for r in rows}
    assert flags == {agents: (False, True), mine: (False, False)}


def test_a_comment_keeps_at_most_twenty_references_linked(client, fresh_db):
    """Every named row costs a bound parameter in one resolution query, and a
    page of comments stuffed with references past the driver's limit made
    the thread unreadable for everybody."""
    task = _parent("tasks")
    others = [work.create_task(f"t{n}", actor="ava")["id"] for n in range(25)]
    _post(client, "tasks", task, " ".join(f"task #{t}" for t in others))
    row = client.get(f"/api/tasks/{task}/comments", headers=_strong(client, "ava")).json()[0]
    assert [r["id"] for r in row["refs"]] == others[:20]


def test_a_reference_links_only_what_the_reader_can_open(client, fresh_db):
    crew = _crew()
    task = _parent("tasks")
    hidden = _parent("decisions", crew=crew)
    _post(client, "tasks", task, f"see decision #{hidden}")
    for who, expected in (("mira", [hidden]), ("bo", [])):
        row = client.get(f"/api/tasks/{task}/comments", headers=_strong(client, who)).json()[0]
        assert [r["id"] for r in row["refs"]] == expected, who


def test_the_write_bucket_caps_comments(client, fresh_db, monkeypatch):
    from app import ratelimit

    monkeypatch.setitem(ratelimit.LIMITS, "write", 1)
    ratelimit.reset()
    task = _parent("tasks")
    assert _post(client, "tasks", task, "one").status_code == 200
    capped = _post(client, "tasks", task, "two")
    assert capped.status_code == 429 and capped.headers.get("Retry-After")


def test_a_thread_notice_skips_someone_no_longer_active(client, fresh_db):
    for name in ("raj", "dana"):
        users.ensure_user(name)
    task = work.create_task("Fix login", actor="ava")["id"]
    _post(client, "tasks", task, "question", who="raj")
    users.set_active("raj", False, actor="ava")
    _post(client, "tasks", task, "answer", who="dana")
    assert _thread("raj") == []


def test_a_workplace_rule_on_the_parent_decides_the_thread_routes(fresh_db):
    from fastapi.testclient import TestClient

    from app.extensions import PolicyContribution, PolicyDecision, PolicyEffect, SkeinModule
    from app.main import create_app

    seen = []

    def deny(request):
        # the handler's own decision, typed on the parent: the generic gate
        # decides first on an untyped resource (extensions/fastapi.py)
        if request.action.endswith(".comments") and request.resource.type == "task":
            seen.append((request.action, request.resource.id))
            return PolicyDecision(PolicyEffect.DENY, ("threads are closed",))
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
    task = work.create_task("Fix login", actor="ava")["id"]
    with TestClient(create_app(modules=(module,)), headers={"X-User": "ava"}) as client:
        assert client.get(f"/api/tasks/{task}/comments").status_code == 403
        assert client.post(f"/api/tasks/{task}/comments", json={"body": "x"}).status_code == 403
    assert seen == [
        ("skein.rest.get.tasks.comments", str(task)),
        ("skein.rest.post.tasks.comments", str(task)),
    ]
    assert db.query_one("SELECT 1 FROM comments") is None
