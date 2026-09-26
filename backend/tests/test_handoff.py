"""Handoff package generation and its scoping."""


def test_handoff_scoped_blockers(client, fresh_db):
    client.post("/api/engagements", json={"name": "Mine"})
    m = client.post("/api/milestones", json={"title": "m", "project": "Mine"}).json()
    t = client.post("/api/tasks", json={"title": "t", "milestone_id": m["id"]}).json()
    client.post("/api/blockers", json={"title": "mine-blocker", "task_id": t["id"]})
    client.post("/api/blockers", json={"title": "unrelated-blocker"})
    eng = client.get("/api/engagements").json()[0]
    md = client.post(f"/api/engagements/{eng['id']}/handoff").json()["markdown"]
    assert "mine-blocker" in md and "unrelated-blocker" not in md


def test_rewriting_a_private_handoff_keeps_the_name_out_of_the_ledger(client, fresh_db):
    """The ledger is hash-chained: a private engagement name written there
    has no delete and no redaction."""
    from conftest import _strong

    headers = _strong("tester")
    eng = client.post(
        "/api/engagements",
        json={"name": "Secret Acme Buyout", "visibility": "private"},
        headers=headers,
    ).json()
    for _ in range(2):
        assert (
            client.post(f"/api/engagements/{eng['id']}/handoff", headers=headers).status_code == 200
        )
    details = [
        r["detail"]
        for r in fresh_db.query("SELECT detail FROM activity WHERE action = 'generate_handoff'")
    ]
    assert len(details) == 2 and not any("Acme" in d for d in details)


def test_a_lead_naming_a_finding_still_resolves_its_references(fresh_db):
    """findings carry no tier, and the title lookup asked scope for one:
    KeyError, and every read of the artifact answered 500."""
    from app import db
    from app.services import policy_context, refs, scope

    fid = db.execute(
        'INSERT INTO findings (rule_id, subject, severity, message, n, "window", receipt,'
        " week, created_at) VALUES ('stale', 'promise:1', 'low', 'a promise slipped', 1, 7,"
        " 'r', '2026-W39', ?) RETURNING id",
        (db.now(),),
    )
    found = refs.readable_refs(f"see finding #{fid} for the lead", scope.NOBODY)
    assert [(r["entity"], r["id"]) for r in found] == [("finding", fid)]
    assert found[0]["title"] == "a promise slipped"
    for entity, (table, _column) in refs._TITLE_SOURCE.items():
        assert table in scope.CLASSIFIED or policy_context._UNSCOPED_RESOURCES.get(entity) == table
