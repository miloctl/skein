"""Questions: assignment, reassignment, and the notifications each one fires."""


def _unread_for(fresh_db, user, like):
    return fresh_db.query_one(
        "SELECT * FROM notifications WHERE user = ? AND message LIKE ? AND read_at IS NULL",
        (user, like),
    )


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
    r = client.post("/api/ingest", json={"text": "q: mira — did the export finish?"})
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
