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
