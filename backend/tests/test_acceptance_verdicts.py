"""Acceptance verdicts bind to the task sponsor. Anyone else needs a reason on the record, and overrides never feed streaks."""


def _strong(client, name="tester"):
    from app.services.api_keys import create_key

    return {"Authorization": f"Bearer {create_key(name, 'r')['key']}"}


def _delegated_task(fresh_db, title="probe"):
    from app.services import delegation, users, work

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    t = work.create_task(title=title, actor="mira")
    delegation.delegate_task(t["id"], "scout", "mira", actor="mira")
    return t["id"]


def test_acceptance_verdicts_bind_to_the_sponsor(client, fresh_db):
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    pid = delegation.submit_completion(tid, "ready", actor="scout")["proposal_id"]
    row = next(p for p in client.get("/api/review?status=pending").json() if p["id"] == pid)
    assert row["sponsor"] == "mira"
    # a non-sponsor without a reason is refused
    bare = client.post(f"/api/review/{pid}/approve", json={}, headers=_strong(client))
    assert bare.status_code == 400 and "sponsored by mira" in bare.json()["detail"]
    # with a reason it lands — marked override, reason on the record
    ok = client.post(
        f"/api/review/{pid}/approve",
        json={"note": "mira is on PTO and asked me to close it"},
        headers=_strong(client),
    )
    assert ok.json()["status"] == "approved"
    ch = fresh_db.query_one("SELECT reviewed_override FROM pending_changes WHERE id = ?", (pid,))
    assert ch["reviewed_override"] == 1
    acts = fresh_db.query("SELECT detail FROM activity WHERE action = 'approve_change'")
    assert any("accepted for mira" in a["detail"] for a in acts)
    # override verdicts are provenance, not trust — the streak ignores them
    score = next(
        s
        for s in delegation.trust_scores()
        if s["agent"] == "scout" and s["entity"] == "task_completion"
    )
    assert score["recent_streak"] == 0


def test_sponsor_verdict_needs_no_note_and_feeds_trust(client, fresh_db):
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    pid = delegation.submit_completion(tid, "ready", actor="scout")["proposal_id"]
    r = client.post(f"/api/review/{pid}/approve", json={}, headers=_strong(client, "mira"))
    assert r.json()["status"] == "approved"
    ch = fresh_db.query_one(
        "SELECT reviewed_override, reviewed_strong FROM pending_changes WHERE id = ?", (pid,)
    )
    assert ch["reviewed_override"] == 0 and ch["reviewed_strong"] == 1
    score = next(
        s
        for s in delegation.trust_scores()
        if s["agent"] == "scout" and s["entity"] == "task_completion"
    )
    assert score["recent_streak"] == 1


def test_non_sponsor_reject_needs_a_reason_too(client, fresh_db):
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    pid = delegation.submit_completion(tid, "ready", actor="scout")["proposal_id"]
    bare = client.post(f"/api/review/{pid}/reject", json={}, headers=_strong(client))
    assert bare.status_code == 400 and "sponsored by mira" in bare.json()["detail"]
    ok = client.post(
        f"/api/review/{pid}/reject",
        json={"note": "covering for mira — the output is wrong"},
        headers=_strong(client),
    )
    assert ok.json()["status"] == "rejected"
    # an override rejection must not push the agent toward demotion
    score = next(
        s
        for s in delegation.trust_scores()
        if s["agent"] == "scout" and s["entity"] == "task_completion"
    )
    assert score["rejection_streak"] == 0


def test_verdict_follows_the_sponsor_after_re_delegation(client, fresh_db):
    from app.services import delegation, users

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    pid = delegation.submit_completion(tid, "ready", actor="scout")["proposal_id"]
    users.ensure_user("jon")
    delegation.delegate_task(tid, "scout", "jon", actor="jon")
    # the OLD sponsor is now an override like anyone else
    bare = client.post(f"/api/review/{pid}/approve", json={}, headers=_strong(client, "mira"))
    assert bare.status_code == 400 and "sponsored by jon" in bare.json()["detail"]
    ok = client.post(
        f"/api/review/{pid}/approve",
        json={"note": "I commissioned this before the handover"},
        headers=_strong(client, "mira"),
    )
    assert ok.json()["status"] == "approved"
    assert (
        fresh_db.query_one("SELECT reviewed_override FROM pending_changes WHERE id = ?", (pid,))[
            "reviewed_override"
        ]
        == 1
    )


def test_orphaned_acceptance_requires_a_reason_and_feeds_no_streak(client, fresh_db):
    from app.services import delegation, work

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    pid = delegation.submit_completion(tid, "ready", actor="scout")["proposal_id"]
    # reassigning to a human clears the delegation AND the sponsor
    work.update_task(tid, assignee="tester", actor="mira")
    bare = client.post(f"/api/review/{pid}/reject", json={}, headers=_strong(client))
    assert bare.status_code == 400 and "orphaned" in bare.json()["detail"]
    ok = client.post(
        f"/api/review/{pid}/reject",
        json={"note": "task was reassigned — closing the stale submission"},
        headers=_strong(client),
    )
    assert ok.json()["status"] == "rejected"
    score = next(
        s
        for s in delegation.trust_scores()
        if s["agent"] == "scout" and s["entity"] == "task_completion"
    )
    assert score["rejection_streak"] == 0


def test_override_verdicts_are_invisible_to_streaks_by_design(client, fresh_db):
    """Overrides are provenance, not trust: they neither count toward nor
    interrupt a streak. A promotion needs 5 SPONSOR approvals; a non-sponsor
    rejection in the middle doesn't reset that count (and symmetrically a
    buddy's override approval can't shield a demotion streak)."""
    from app.services import delegation, users, work

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    verdicts = []
    for i in range(3):
        t = work.create_task(title=f"loop {i}", actor="mira")
        delegation.delegate_task(t["id"], "scout", "mira", actor="mira")
        delegation.claim_task(t["id"], actor="scout")
        verdicts.append(delegation.submit_completion(t["id"], "done", actor="scout"))
    a, b, c = (v["proposal_id"] for v in verdicts)
    client.post(f"/api/review/{a}/approve", json={}, headers=_strong(client, "mira"))
    # a non-sponsor override rejection lands between two sponsor approvals
    client.post(
        f"/api/review/{b}/reject",
        json={"note": "covering — looked off to me"},
        headers=_strong(client),
    )
    client.post(f"/api/review/{c}/approve", json={}, headers=_strong(client, "mira"))
    score = next(
        s
        for s in delegation.trust_scores()
        if s["agent"] == "scout" and s["entity"] == "task_completion"
    )
    assert score["recent_streak"] == 2  # both sponsor approvals, unbroken
    assert score["rejection_streak"] == 0
