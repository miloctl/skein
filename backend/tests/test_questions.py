"""Questions: assignment, reassignment, and the notifications each one fires."""

from conftest import _unread_for


def test_question_reassign_and_notify(client):
    client.post("/api/users/growth-interests", json={"interests": "x"}, headers={"X-User": "dana"})
    qid = client.post("/api/questions", json={"question": "who owns the roadmap?"}).json()["id"]
    r = client.patch(f"/api/questions/{qid}", json={"assigned_to": "dana"})
    assert r.status_code == 200
    assert client.get("/api/questions").json()[0]["assigned_to"] == "dana"


def test_assign_question_rejects_unknown_user(client):
    qid = client.post("/api/questions", json={"question": "who?"}).json()["id"]
    r = client.patch(f"/api/questions/{qid}", json={"assigned_to": "mria"})
    assert r.status_code == 400


def test_ingest_question_line_assigns(client):
    client.post("/api/users/growth-interests", json={"interests": "x"}, headers={"X-User": "mira"})
    r = client.post("/api/ingest", json={"text": "q: mira - did the export finish?"})
    pid = r.json()["proposals"][0]["id"]
    client.post(f"/api/review/{pid}/approve", json={})
    q = client.get("/api/questions").json()[0]
    assert q["assigned_to"] == "mira"
    assert q["question"] == "did the export finish?"


def test_answer_notifies_asker(fresh_db):
    from app.services import collab

    q = collab.ask_question("who owns DNS?", asked_by="mira", actor="mira")
    collab.answer_question(q["id"], "tomas does", actor="claude")
    assert _unread_for(fresh_db, "mira", "%was answered%")


def test_ask_question_resolves_the_assignee_against_the_roster(client):
    """assign_question refused a typo'd assignee and ask_question stored the
    literal, so a question created with `assigned_to: "mria"` looked assigned
    and notified nobody - and `Dana` notified nobody either, because the
    notice matches the roster name exactly."""
    client.post("/api/users/growth-interests", json={"interests": "x"}, headers={"X-User": "dana"})
    r = client.post("/api/questions", json={"question": "who?", "assigned_to": "mria"})
    assert r.status_code == 400
    assert "assignee" in r.json()["detail"]
    r = client.post("/api/questions", json={"question": "who?", "assigned_to": "team"})
    assert r.status_code == 400
    body = {"question": "who?", "assigned_to": "Dana"}
    qid = client.post("/api/questions", json=body).json()["id"]
    row = next(q for q in client.get("/api/questions").json() if q["id"] == qid)
    assert row["assigned_to"] == "dana"


def test_an_empty_answer_leaves_the_question_open(client):
    """A whitespace answer flipped the status to answered with nothing to
    read, and the question left every open list."""
    qid = client.post("/api/questions", json={"question": "who owns DNS?"}).json()["id"]
    r = client.post(f"/api/questions/{qid}/answer", json={"answer": "   "})
    assert r.status_code == 400
    row = next(q for q in client.get("/api/questions").json() if q["id"] == qid)
    assert row["status"] == "open"
