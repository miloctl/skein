"""W1.5: commitment audience, attention regroup, claim_at, onboarding playbook."""


def test_commitment_audience(client, fresh_db):
    client.post(
        "/api/commitments",
        json={"promise": "resolve the platform ownership question", "audience": "team"},
    )
    client.post("/api/commitments", json={"promise": "beta to ops", "to_whom": "ops"})
    team = client.get("/api/commitments?audience=team").json()
    assert len(team) == 1 and team[0]["audience"] == "team"
    assert len(client.get("/api/commitments").json()) == 2
    r = client.post("/api/commitments", json={"promise": "x", "audience": "bogus"})
    assert r.status_code == 400


def test_readout_excludes_team_commitments(client, fresh_db):
    from app.services.readout import exec_readout

    client.post(
        "/api/commitments",
        json={"promise": "team-only promise", "audience": "team", "due_date": "2026-07-30"},
    )
    md = exec_readout(actor="tester")["markdown"]
    assert "team-only promise" not in md


def test_attention_groups_and_reasons(client, fresh_db):
    client.post("/api/questions", json={"question": "who owns infra?", "assigned_to": "tester"})
    client.post("/api/blockers", json={"title": "stuck on vendor", "owner": "tester"})
    client.post("/api/commitments", json={"promise": "beta date", "due_date": "2020-01-01"})
    b = client.get("/api/briefing").json()
    groups = {a["group"] for a in b["attention"]}
    assert {"unblock", "commit"} <= groups
    assert all(a["reason"] for a in b["attention"])
    overdue = [a for a in b["attention"] if a["group"] == "commit"]
    assert "OVERDUE" in overdue[0]["reason"]


def test_claim_at_and_active_review_stats(client, fresh_db):
    from app.services import review

    p = review.propose_change("task", "create", {"title": "t"}, actor="agent")
    review.mark_seen([p["id"]], actor="reviewer")
    row = fresh_db.query_row("SELECT claim_at FROM pending_changes WHERE id = ?", (p["id"],))
    assert row["claim_at"] is not None
    first = row["claim_at"]
    review.mark_seen([p["id"]], actor="reviewer")  # idempotent — first-seen wins
    assert (
        fresh_db.query_row("SELECT claim_at FROM pending_changes WHERE id = ?", (p["id"],))[
            "claim_at"
        ]
        == first
    )
    review.approve_change(p["id"], actor="reviewer")
    stats = review.review_stats()
    assert stats["active_review_minutes"]["n"] == 1


def test_manager_onboarding_playbook_instantiates(client, fresh_db):
    from app.services.playbooks import instantiate, list_playbooks

    assert any(p["slug"] == "manager_onboarding" for p in list_playbooks())
    created = instantiate("manager_onboarding", "My EM ramp", lead="manager", actor="manager")
    assert len(created["milestones"]) == 4
    assert len(created["events"]) == 2
    titles = [m["title"] for m in created["milestones"]]
    assert any("Listening tour" in t for t in titles)
