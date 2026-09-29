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
    assert _post(client, "tasks", task, "ok‮evil").status_code == 400
    cid = _post(client, "tasks", task, "ok").json()["id"]
    edit = client.patch(f"/api/comments/{cid}", json={"body": "ok​"}, headers=_strong(client, "ava"))
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
    before = db.query_one("SELECT updated_at FROM tasks WHERE id = ?", (task,))["updated_at"]
    _post(client, "tasks", task, "still alive?")
    assert (
        db.query_one("SELECT updated_at FROM tasks WHERE id = ?", (task,))["updated_at"] == before
    )
