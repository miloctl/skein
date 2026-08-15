"""Intake: scoring, disposition, the fields an accept carries, and the edit window."""

from conftest import _unread_for


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


def test_stall_rule_windows_on_disposition_time_not_creation(fresh_db):
    """The stall rule's 6-week sample. A request created 50 days ago (outside
    6 weeks) but dispositioned TODAY (inside it) is the slowest kind — exactly
    what the rule watches for. created_at windowing dropped every one of them;
    updated_at (which intake rows freeze at disposition) keeps them."""
    from app.services import insights, intake

    for i in range(5):
        r = intake.submit_request(f"slow {i}", requester="mira", actor="mira")
        fresh_db.execute(
            "UPDATE intake_requests SET created_at = (now() - interval '50 days')::text WHERE id = ?",
            (r["id"],),
        )
        intake.disposition_request(r["id"], "declined", "too late", actor="mira")

    findings = insights._r_intake_stall()
    # windowing on created_at saw an empty 6-week sample and stayed silent;
    # windowing on updated_at puts all five ~50-day dispositions in, so the
    # rule fires
    assert findings, "the stall rule missed dispositions that took ~50 days"
    assert findings[0]["n"] == 5
    assert findings[0]["receipt"]["median_days"] > 7


def test_accept_degrades_when_the_engagement_name_collides_in_a_race(fresh_db, monkeypatch):
    """create_engagement pre-checks the name NOCASE, so the normal
    collision is a ValueError. Two accepts landing together both pass that
    read and the loser hits ux_engagements_name_nocase instead — uncaught
    that is a 500 for a caller-supplied name. Accept must degrade the same
    way either route."""
    from app import db
    from app.services import engagements, intake

    r = intake.submit_request("Ship the audit tool", requester="dana", actor="dana")
    intake.score_request(r["id"], 3, 3, 3, 3, actor="mgr")

    def racing_create(*_a, **_k):
        raise db.UniqueViolation("duplicate key value violates unique constraint")

    monkeypatch.setattr(engagements, "create_engagement", racing_create)
    out = intake.disposition_request(r["id"], "accepted", "worth doing", actor="mgr")
    assert out["engagement_created"] is False
    assert "no new engagement" in out["note"]
    # the request itself still settled — the verdict is not lost
    assert intake.list_requests()[0]["status"] == "accepted"
