"""Wave 2: waiting_on edges, charter category, growth interests, /ask,
authority half-life, private-note delete/audit, rate caps."""

import pytest


def test_waiting_on_validation_and_clear(client, fresh_db):
    from app.services import blockers, work

    t = work.create_task(title="build the thing", actor="m")
    b = blockers.raise_blocker("vendor key", actor="m")
    with pytest.raises(ValueError, match="waiting_on must look like"):
        work.update_task(t["id"], waiting_on="nonsense", actor="m")
    with pytest.raises(ValueError, match="not found"):
        work.update_task(t["id"], waiting_on="blocker:999", actor="m")
    with pytest.raises(ValueError, match="wait on itself"):
        work.update_task(t["id"], waiting_on=f"task:{t['id']}", actor="m")
    work.update_task(t["id"], waiting_on=f"blocker:{b['id']}", actor="m")
    row = fresh_db.query_row("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["waiting_on_type"] == "blocker" and row["waiting_on_id"] == b["id"]
    work.update_task(t["id"], waiting_on="-", actor="m")
    row = fresh_db.query_row("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["waiting_on_type"] is None


def test_waiting_on_surfaces_in_health_and_forecast(client, fresh_db):
    from app.services import blockers, engagements, work

    engagements.create_engagement("Comet", actor="m")
    m = work.create_milestone(title="M1", project="Comet", due_date="2099-01-01", actor="m")
    t = work.create_task(title="stuck task", milestone_id=m["id"], actor="m")
    b = blockers.raise_blocker("upstream", actor="m")
    work.update_task(t["id"], waiting_on=f"blocker:{b['id']}", actor="m")
    health = client.get("/api/portfolio/health").json()
    receipts = health[0]["receipts"]
    assert any("waiting on blocker" in r for r in receipts)
    forecast = client.get("/api/portfolio/forecast").json()
    assert forecast["forecasts"][0]["waiting_on"] == [f"blocker #{b['id']} (task #{t['id']})"]


def test_charter_decisions(client, fresh_db):
    client.post(
        "/api/decisions",
        json={
            "title": "Escalation path",
            "decision": "page the lead after 2h",
            "review_by": "2099-01-01",
            "category": "charter",
        },
    )
    client.post("/api/decisions", json={"title": "normal", "decision": "x"})
    charter = client.get("/api/decisions?category=charter").json()
    assert len(charter) == 1 and charter[0]["title"] == "Escalation path"
    assert len(client.get("/api/decisions").json()) == 2
    r = client.post("/api/decisions", json={"title": "x", "decision": "y", "category": "bogus"})
    assert r.status_code == 400


def test_growth_interests_self_declared_and_in_what_if(client, fresh_db):
    client.post(
        "/api/users/growth-interests",
        json={"interests": "RAG evaluation, incident command"},
        headers={"X-User": "chen"},
    )
    req = client.post("/api/intake", json={"title": "RAG revamp"}).json()
    out = client.post(
        f"/api/intake/{req['id']}/what-if", json={"people": ["chen", "dana"], "percent": 40}
    ).json()
    by_person = {p["person"]: p for p in out["projection"]}
    assert "RAG" in by_person["chen"]["growth_interests"]
    assert by_person["dana"]["growth_interests"] == ""


def test_ask_cites_rows(client, fresh_db):
    client.post("/api/decisions", json={"title": "Ship on Fridays", "decision": "we ship fridays"})
    out = client.get("/api/ask?q=fridays").json()
    assert out["citations"]
    assert out["citations"][0]["ref"].startswith("decision #")
    empty = client.get("/api/ask?q=zzzznothing").json()
    assert empty["citations"] == [] and "nothing indexed" in empty["note"]


def test_authority_half_life(client, fresh_db):
    from app.services.delegation import set_authority
    from app.services.insights import run_findings

    set_authority("planner-agent", "task", "autonomous", actor="manager")
    row = fresh_db.query_row("SELECT * FROM agent_authority WHERE agent = 'planner-agent'")
    assert row["review_by"] is not None
    # not stale yet
    assert not any(f["rule_id"] == "authority_stale" for f in run_findings(actor="t")["findings"])
    fresh_db.execute(
        "UPDATE agent_authority SET review_by = '2020-01-01' WHERE agent = 'planner-agent'"
    )
    hits = [f for f in run_findings(actor="t")["findings"] if f["rule_id"] == "authority_stale"]
    assert len(hits) == 1 and "planner-agent" in hits[0]["message"]
    # forbidden/review grants carry no review_by — the kill switch never expires
    set_authority("planner-agent", "note", "forbidden", actor="manager")
    row = fresh_db.query_row(
        "SELECT review_by FROM agent_authority WHERE agent = 'planner-agent' AND entity = 'note'"
    )
    assert row["review_by"] is None


def test_private_note_delete_and_audit(client, fresh_db):
    from app.services.api_keys import create_key

    headers = {"Authorization": f"Bearer {create_key('manager', 't')['key']}"}
    note = client.post(
        "/api/private/notes", json={"person": "dana", "body": "note"}, headers=headers
    ).json()
    client.get("/api/private/notes?person=dana", headers=headers)
    r = client.delete(f"/api/private/notes/{note['id']}", headers=headers)
    assert r.json()["deleted"] is True
    assert client.get("/api/private/notes?person=dana", headers=headers).json() == []
    audit = client.get("/api/private/audit", headers=headers).json()
    actions = [a["action"] for a in audit]
    assert (
        "add_note" in actions and "delete" in actions and any(a.startswith("list") for a in actions)
    )
    # someone else can't delete or read the audit
    other = {"Authorization": f"Bearer {create_key('other', 't')['key']}"}
    note2 = client.post(
        "/api/private/notes", json={"person": "x", "body": "mine"}, headers=headers
    ).json()
    assert client.delete(f"/api/private/notes/{note2['id']}", headers=other).status_code == 400
    assert client.get("/api/private/audit", headers=other).json() == []


def test_charter_supersede_keeps_category_and_requires_review_by(client, fresh_db):
    from app.services.collab import record_decision, supersede_decision

    with pytest.raises(ValueError, match="review_by"):
        record_decision("no date", "x", category="charter", actor="m")
    old = record_decision(
        "Quality bar", "tests before merge", review_by="2099-01-01", category="charter", actor="m"
    )
    new = supersede_decision(old["id"], "Quality bar v2", "tests + lint before merge", actor="m")
    charter = client.get("/api/decisions?category=charter").json()
    by_id = {d["id"]: d for d in charter}
    assert new["id"] in by_id  # the replacement stays on the charter page
    assert by_id[new["id"]]["review_by"] is not None  # 90d default applied


def test_satisfied_waits_stop_yellowing(client, fresh_db):
    from app.services import blockers, engagements, work

    engagements.create_engagement("Nimbus", actor="m")
    m = work.create_milestone(title="M1", project="Nimbus", actor="m")
    t = work.create_task(title="stuck", milestone_id=m["id"], actor="m")
    b = blockers.raise_blocker("upstream", actor="m")
    work.update_task(t["id"], waiting_on=f"blocker:{b['id']}", actor="m")
    assert client.get("/api/portfolio/health").json()[0]["health"] == "yellow"
    blockers.resolve_blocker(b["id"], actor="m")
    health = client.get("/api/portfolio/health").json()[0]
    assert health["health"] == "green"  # dependency cleared → receipt gone
    assert not any("waiting" in r for r in health["receipts"])


def test_agent_tools_cover_wave2_fields(fresh_db, monkeypatch):
    from app import config
    from app.services import blockers as blockers_svc
    from app.services import work as work_svc
    from app.tools import collab as tc
    from app.tools import work as tw

    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    t = work_svc.create_task(title="agent task", actor="m")
    b = blockers_svc.raise_blocker("dep", actor="m")
    tw.update_task(t["id"], waiting_on=f"blocker:{b['id']}")
    row = fresh_db.query_row("SELECT waiting_on_type FROM tasks WHERE id = ?", (t["id"],))
    assert row["waiting_on_type"] == "blocker"
    tc.record_decision(
        "Charter via agent",
        "agents can draft charter entries",
        review_by="2099-01-01",
        category="charter",
    )
    assert len(fresh_db.query("SELECT * FROM decisions WHERE category = 'charter'")) == 1


def test_authority_stale_null_review_by_falls_back(fresh_db):
    from app.services.insights import run_findings

    fresh_db.execute(
        "INSERT INTO agent_authority (agent, entity, level, updated_by, updated_at)"
        " VALUES ('old-agent', 'task', 'autonomous', 'm', '2020-01-01T00:00:00')"
    )
    hits = [f for f in run_findings(actor="t")["findings"] if f["rule_id"] == "authority_stale"]
    assert len(hits) == 1  # pre-017-style row (NULL review_by) still expires


def test_rate_caps(client, fresh_db):
    from app import ratelimit

    ratelimit.reset()
    for i in range(30):
        client.post("/api/capture", json={"text": f"note: filler {i}"})
    r = client.post("/api/capture", json={"text": "note: one too many"})
    assert r.status_code == 400 and "slow down" in r.json()["detail"]
    ratelimit.reset()
    assert client.post("/api/capture", json={"text": "note: fine again"}).status_code == 200
