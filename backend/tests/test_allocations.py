"""Capacity: allocations, absences, window awareness, and the what-if projection."""

from datetime import UTC, datetime, timedelta

import pytest


def _utc_today():
    return datetime.now(UTC).date()


def test_capacity_and_conflicts_ignore_out_of_window_allocations(fresh_db):
    from app.services import engagements, portfolio, users

    users.ensure_user("alice")
    a = engagements.create_engagement("Alpha")
    b = engagements.create_engagement("Beta")
    yesterday = (_utc_today() - timedelta(days=1)).isoformat()
    engagements.allocate("alice", a["id"], 80)
    engagements.allocate("alice", b["id"], 40, ends_on=yesterday)  # window closed

    cap = engagements.capacity()
    assert cap[0]["person"] == "alice" and cap[0]["total_percent"] == 80
    assert portfolio.allocation_conflicts() == []  # capacity and conflicts agree

    engagements.allocate("alice", b["id"], 40, starts_on=yesterday)  # covers today
    assert engagements.capacity()[0]["total_percent"] == 120
    assert portfolio.allocation_conflicts()[0]["person"] == "alice"


def test_deallocate_removes_row_and_missing_id_404s(client):
    from app.services import engagements, users

    users.ensure_user("bo")
    users.ensure_user("cy")
    e = engagements.create_engagement("Gamma")
    aid = engagements.allocate("bo", e["id"], 50)["id"]
    engagements.allocate("cy", e["id"], 30)
    assert len(engagements.list_allocations(e["id"])) == 2

    out = client.delete(f"/api/allocations/{aid}").json()
    assert out["deleted"] is True
    left = engagements.list_allocations(e["id"])
    assert [r["person"] for r in left] == ["cy"]
    assert client.delete(f"/api/allocations/{aid}").status_code == 404

    with pytest.raises(ValueError, match="no allocation"):
        engagements.deallocate(9999)


def test_absences_shape_capacity_and_week_draft(client, fresh_db):
    from datetime import datetime, timedelta

    from app.services import absences, engagements, users, weekly, work

    users.ensure_user("dana")
    e = engagements.create_engagement("Staffed", actor="mira")
    engagements.allocate("dana", e["id"], percent=80, actor="mira")
    # UTC, to match capacity()/draft_plan — local date.today() drifts a day at
    # the UTC boundary and the absence window then misses the service's today
    today = datetime.now(UTC).date()
    # anchor to the week's Monday: run on a Friday, today-1..today+7 covers
    # too few weekdays of THIS week to trip the >= 3 skip threshold
    monday_anchor = today - timedelta(days=today.weekday())
    absences.add_absence(
        "dana",
        monday_anchor.isoformat(),
        (monday_anchor + timedelta(days=6)).isoformat(),
        actor="mira",
    )
    cap = client.get("/api/capacity").json()
    row = next(c for c in cap if c["person"] == "dana")
    assert row["away"] == "pto"
    # week draft skips someone away most of the week
    work.create_task(title="never plan me", assignee="dana", actor="mira")
    monday = today - timedelta(days=today.weekday())
    week = f"{monday.isocalendar().year}-W{monday.isocalendar().week:02d}"
    draft = weekly.draft_plan(week)
    assert all(i["assignee"] != "dana" for i in draft["items"])
    assert any(s["person"] == "dana" for s in draft["skipped_absent"])


def test_absence_validation_and_delete(client, fresh_db):
    from app.services import users

    users.ensure_user("dana")
    r = client.post(
        "/api/absences",
        json={"person": "dana", "starts_on": "2026-08-10", "ends_on": "2026-08-01"},
    )
    assert r.status_code == 400
    ok = client.post(
        "/api/absences",
        json={"person": "dana", "starts_on": "2026-08-01", "ends_on": "2026-08-10"},
    ).json()
    assert client.delete(f"/api/absences/{ok['id']}").json()["deleted"] is True
    assert client.delete(f"/api/absences/{ok['id']}").status_code == 404


def test_agent_absence_is_always_a_proposal(fresh_db, monkeypatch):
    import json as j

    from app import config
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import users
    from app.tools.portfolio import add_absence as absence_tool

    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    users.ensure_user("scout", kind="agent")
    token = set_agent_identity("scout")
    try:
        out = j.loads(absence_tool(person="mira", starts_on="2026-08-10", ends_on="2026-08-12"))
    finally:
        reset_agent_identity(token)
    assert out.get("note") == "queued for human review"
    assert not fresh_db.query_one("SELECT id FROM absences")


def test_allocation_and_absence_refuse_team_and_ghosts(fresh_db):
    from app.services import absences, engagements, users

    users.ensure_user("mira")
    e = engagements.create_engagement("Ghosts")
    with pytest.raises(ValueError, match="not an active teammate"):
        engagements.allocate("team", e["id"], 50, actor="mira")
    with pytest.raises(ValueError, match="not an active teammate"):
        absences.add_absence("gohst", "2026-08-10", "2026-08-11", actor="mira")
    absences.add_absence("MIRA", "2026-08-10", "2026-08-11", actor="tester")
    row = fresh_db.query_one("SELECT person FROM absences")
    assert row["person"] == "mira"  # canonicalized


def test_allocation_conflicts(client):
    from app.services import users

    users.ensure_user("dana")
    e1 = client.post("/api/engagements", json={"name": "One"}).json()
    e2 = client.post("/api/engagements", json={"name": "Two"}).json()
    client.post(f"/api/engagements/{e1['id']}/allocate", json={"person": "dana", "percent": 80})
    client.post(f"/api/engagements/{e2['id']}/allocate", json={"person": "dana", "percent": 50})
    conflicts = client.get("/api/portfolio/conflicts").json()
    assert conflicts[0]["person"] == "dana"
    assert conflicts[0]["total_percent"] == 130


def test_what_if_projection(client):
    from app.services import users

    users.ensure_user("dana")
    users.ensure_user("lee")
    e = client.post("/api/engagements", json={"name": "Busy"}).json()
    client.post(f"/api/engagements/{e['id']}/allocate", json={"person": "dana", "percent": 80})
    req = client.post("/api/intake", json={"title": "new ask"}).json()
    out = client.post(
        f"/api/intake/{req['id']}/what-if", json={"people": ["dana", "lee"], "percent": 50}
    ).json()
    dana = next(p for p in out["projection"] if p["person"] == "dana")
    assert dana["projected_percent"] == 130 and dana["overcommitted"]
    lee = next(p for p in out["projection"] if p["person"] == "lee")
    assert lee["projected_percent"] == 50 and not lee["overcommitted"]
    assert client.post("/api/intake/999999/what-if", json={"people": ["x"]}).status_code == 404


def test_what_if_ignores_expired_allocations(client):
    e = client.post("/api/engagements", json={"name": "Old"}).json()
    client.post(
        f"/api/engagements/{e['id']}/allocate",
        json={"person": "zoe", "percent": 80, "starts_on": "2025-01-01", "ends_on": "2025-06-30"},
    )
    req = client.post("/api/intake", json={"title": "new"}).json()
    out = client.post(
        f"/api/intake/{req['id']}/what-if", json={"people": ["zoe"], "percent": 50}
    ).json()
    zoe = out["projection"][0]
    assert zoe["current_percent"] == 0 and not zoe["overcommitted"]
