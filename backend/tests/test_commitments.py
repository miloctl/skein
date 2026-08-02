"""The commitment ledger: lifecycle, audience, and the history guards on an edit."""


def test_commitment_lifecycle_and_capture(client):
    c = client.post("/api/commitments", json={"promise": "ship v1 to ops", "to_whom": "ops"}).json()
    assert c["status"] == "open"
    client.post(f"/api/commitments/{c['id']}/status", json={"status": "kept"})
    r = client.post(f"/api/commitments/{c['id']}/status", json={"status": "missed"})
    assert r.status_code == 400  # terminal

    cap = client.post(
        "/api/capture", json={"text": "promised: security review to legal by Friday"}
    ).json()
    assert cap["kind"] == "commitment"
    assert any("security review" in x["promise"] for x in client.get("/api/commitments").json())


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


def test_team_commitments_dont_fire_external_rule(fresh_db):
    from app.services.commitments import add_commitment
    from app.services.insights import run_findings

    add_commitment("promise to the team", due_date="2020-01-01", audience="team", actor="m")
    result = run_findings(actor="t")
    assert not any(f["rule_id"] == "commitment_due" for f in result["findings"])
    add_commitment("promise to ops", due_date="2020-01-01", audience="external", actor="m")
    result = run_findings(actor="t")
    assert any(f["rule_id"] == "commitment_due" for f in result["findings"])


def test_blocker_and_commitment_edits_guard_history(client):
    from app.services import blockers, commitments

    b = blockers.raise_blocker(title="typo'd", owner="ava", actor="ava")
    assert blockers.edit_blocker(b["id"], title="fixed title", actor="ava")["updated"] == ["title"]
    blockers.resolve_blocker(b["id"], actor="ava")
    try:
        blockers.edit_blocker(b["id"], title="nope", actor="ava")
        raise AssertionError("resolved blocker was editable")
    except ValueError:
        pass

    c = commitments.add_commitment("shipp the thing", actor="ava")
    commitments.edit_commitment(c["id"], promise="ship the thing", actor="ava")
    commitments.update_commitment(c["id"], "kept", actor="ava")
    try:
        commitments.edit_commitment(c["id"], promise="rewrite history", actor="ava")
        raise AssertionError("settled commitment was editable")
    except ValueError:
        pass
