"""Intake: scoring, disposition, the fields an accept carries, and the edit window."""


def _unread_for(fresh_db, user, like):
    return fresh_db.query_one(
        "SELECT * FROM notifications WHERE user = ? AND message LIKE ? AND read_at IS NULL",
        (user, like),
    )


def test_dispositioned_intake_cannot_be_rescored(client, fresh_db):
    from app.services import intake

    req = intake.submit_request("old idea", requester="mira", actor="mira")
    intake.score_request(req["id"], 2, 2, 2, 2, actor="mira")
    intake.disposition_request(req["id"], "declined", "not now", actor="mira")
    r = client.post(
        f"/api/intake/{req['id']}/score",
        json={"reach": 5, "impact": 5, "confidence": 5, "effort": 1},
    )
    assert r.status_code == 400 and "stay put" in r.json()["detail"]
    row = fresh_db.query_one("SELECT status FROM intake_requests WHERE id = ?", (req["id"],))
    assert row["status"] == "declined"


def test_intake_accept_carries_lead_and_kill_criteria(client):
    rid = client.post(
        "/api/intake",
        json={"title": "Shadow alerts", "requester": "pm"},
    ).json()["id"]
    r = client.post(
        f"/api/intake/{rid}/disposition",
        json={
            "disposition": "accepted",
            "reason": "cheap probe",
            "kind": "experiment",
            "timebox_end": "2026-08-08",
            "lead": "tester",
            "kill_criteria": "FP rate >20% after a week",
        },
    )
    assert r.status_code == 200
    eng = next(e for e in client.get("/api/engagements").json() if e["name"] == "Shadow alerts")
    assert eng["lead"] == "tester"
    assert eng["kill_criteria"] == "FP rate >20% after a week"


def test_intake_accept_name_collision_is_loud(client):
    client.post("/api/engagements", json={"name": "Taken"})
    req = client.post("/api/intake", json={"title": "Taken"}).json()
    client.post(
        f"/api/intake/{req['id']}/score",
        json={"reach": 3, "impact": 3, "confidence": 3, "effort": 3},
    )
    out = client.post(
        f"/api/intake/{req['id']}/disposition", json={"disposition": "accepted", "reason": "yes"}
    ).json()
    assert out["engagement_created"] is False
    assert "already exists" in out["note"]


def test_intake_edit_only_before_disposition(client):
    from app.services import intake

    r = intake.submit_request("typo titel", actor="ava")
    assert intake.edit_request(r["id"], title="typo title fixed", actor="ava")
    intake.score_request(r["id"], 3, 3, 3, 3, actor="ava")
    intake.edit_request(r["id"], detail="still editable while scored", actor="ava")
    intake.disposition_request(r["id"], "declined", reason="no", actor="ava")
    try:
        intake.edit_request(r["id"], title="after the fact", actor="ava")
        raise AssertionError("dispositioned request was editable")
    except ValueError:
        pass


def test_intake_disposition_notifies_requester(fresh_db):
    from app.services import intake

    r = intake.submit_request("try skein for docs", requester="mira", actor="mira")
    intake.disposition_request(r["id"], "declined", "out of scope this season", actor="claude")
    assert _unread_for(fresh_db, "mira", "%was declined%")
