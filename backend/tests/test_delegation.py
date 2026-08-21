"""The delegation work loop: claim, worklog, submit for acceptance, and the walls that stop an agent closing its own task."""

import pytest
from conftest import _delegated_task, _strong


def test_the_contract_travels_with_the_delegation(fresh_db, monkeypatch):
    """What done means and when to check in are stored at delegation, read in
    the agent inbox and the acceptance evidence, and cleared when a
    reassignment ends the delegation — the next delegate must not inherit a
    done-definition written for a different party. A malformed date is
    refused, not stored."""
    from app import config
    from app.services import delegation, review, scope, users, work

    monkeypatch.setattr(config, "AGENT_REVIEW", False)
    users.ensure_user("mira")
    t = work.create_task(title="probe the API", actor="mira")
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        delegation.delegate_task(t["id"], "scout", "mira", check_in_at="Wednesday", actor="mira")
    delegation.delegate_task(
        t["id"],
        "scout",
        "mira",
        acceptance_criteria="a runnable repro script",
        check_in_at="2026-09-01",
        actor="mira",
    )

    inbox = delegation.agent_inbox("scout")
    row = next(r for r in inbox["delegated_tasks"] if r["id"] == t["id"])
    assert row["acceptance_criteria"] == "a runnable repro script"
    assert str(row["check_in_at"]) == "2026-09-01"

    delegation.claim_task(t["id"], actor="scout")
    delegation.report_progress(t["id"], "repro written", actor="scout")
    proposal = delegation.submit_completion(t["id"], "done, see worklog", actor="scout")
    changes = review.list_changes(viewer=scope.Viewer("mira", True))
    mine = next(c for c in changes if c["id"] == proposal["proposal_id"])
    assert mine["evidence"]["acceptance_criteria"] == "a runnable repro script"

    # reassignment ends the delegation and takes the contract with it
    work.update_task(t["id"], assignee="mira", actor="mira")
    after = fresh_db.query_one(
        "SELECT delegated_agent, acceptance_criteria, check_in_at FROM tasks WHERE id = ?",
        (t["id"],),
    )
    assert after["delegated_agent"] == ""
    assert after["acceptance_criteria"] == ""
    assert after["check_in_at"] is None


def test_delegation_work_loop_end_to_end(client, fresh_db, monkeypatch):
    from app import config
    from app.services import delegation, users, work

    monkeypatch.setattr(config, "AGENT_REVIEW", False)  # loop must gate regardless
    users.ensure_user("mira")
    t = work.create_task(title="build the probe", actor="mira")
    delegation.delegate_task(t["id"], "scout", "mira", actor="mira")
    delegation.claim_task(t["id"], actor="scout")
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (t["id"],))["status"] == (
        "in_progress"
    )
    delegation.report_progress(t["id"], "probe scaffolded, tests next", actor="scout")
    assert client.get(f"/api/tasks/{t['id']}/worklog").json()[0]["note"].startswith("probe")
    out = delegation.submit_completion(t["id"], "probe built and green", actor="scout")
    # still not done — the sponsor's verdict is the acceptance
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (t["id"],))["status"] == (
        "in_progress"
    )
    r = client.post(
        f"/api/review/{out['proposal_id']}/approve", json={}, headers=_strong(client, "mira")
    )
    assert r.json()["status"] == "approved"
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (t["id"],))["status"] == (
        "done"
    )
    task_events = fresh_db.query("SELECT event_type, payload FROM extension_outbox ORDER BY seq")
    assert [row["event_type"] for row in task_events] == [
        "skein.task.created",
        "skein.task.updated",
        "skein.task.updated",
        "skein.task.updated",
    ]
    assert '"delegated_agent"' in task_events[1]["payload"]
    assert '"status"' in task_events[2]["payload"]
    assert '"completed_at"' in task_events[3]["payload"]
    notes = [w["note"] for w in delegation.list_worklog(t["id"])]
    assert any(n.startswith("[accepted]") for n in notes)


def test_claim_requires_the_delegated_agent(fresh_db):
    from app.services import delegation, users, work

    users.ensure_user("mira")
    t = work.create_task(title="x", actor="mira")
    delegation.delegate_task(t["id"], "scout", "mira", actor="mira")
    try:
        delegation.claim_task(t["id"], actor="other-agent")
        raise AssertionError("claim by a non-delegate succeeded")
    except ValueError:
        pass


def test_worklog_writes_bound_to_the_loop(fresh_db):
    from app.services import delegation, users

    tid = _delegated_task(fresh_db)
    users.ensure_user("intruder", kind="agent")
    with pytest.raises(ValueError, match="delegate or sponsor"):
        delegation.report_progress(tid, "[accepted] looks done to me", actor="intruder")
    with pytest.raises(ValueError, match="2000"):
        delegation.report_progress(tid, "x" * 2001, actor="scout")
    # the sponsor may annotate; a done task's worklog is frozen
    delegation.report_progress(tid, "sponsor context", actor="mira")
    delegation.accept_completion(tid, "fine", actor="scout")
    with pytest.raises(ValueError, match="history"):
        delegation.report_progress(tid, "late addendum", actor="scout")


def test_only_the_sponsor_closes_delegated_work(client, fresh_db):
    """Both halves of the guard, on the one transition that ends the loop.

    The agent half was complete and the human half did not exist, so any
    teammate who could reach PATCH /api/tasks/{id} closed delegated work with
    one field — no sponsor verdict, no reason on record, no override marking,
    and no trust signal for the agent that did the work.
    """
    from app.services import work

    tid = _delegated_task(fresh_db)
    with pytest.raises(ValueError, match="sponsor's verdict"):
        work.update_task(tid, status="done", actor="scout", origin="agent")

    # `tester` is the client fixture's identity and is NOT the sponsor: 403,
    # naming the sponsor and the path that puts a reason on record
    r = client.patch(f"/api/tasks/{tid}", json={"status": "done"})
    assert r.status_code == 403
    assert "sponsored by mira" in r.json()["detail"]

    # the sponsor's own hand is not blocked — the verdict is theirs either way,
    # and refusing them here would make the proposal the only way to close
    # work they already own
    ok = client.patch(f"/api/tasks/{tid}", json={"status": "done"}, headers={"X-User": "mira"})
    assert ok.status_code == 200


def test_the_sponsors_own_close_settles_the_acceptance_proposal(client, fresh_db):
    """The sponsor may close delegated work by hand, and an agent may have a
    completion proposal already waiting. Both are legal at once, and the
    proposal then asks a question that has been answered.

    Left pending, its apply raises on a task that is already done, and
    approve_change resets a failed apply to pending — so the verdict boomerangs
    on every click and the only exit is a rejection that lands on the agent's
    demotion streak for work the sponsor accepted.
    """
    from app import db
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    out = delegation.submit_completion(tid, "shipped it", actor="scout")

    ok = client.patch(f"/api/tasks/{tid}", json={"status": "done"}, headers={"X-User": "mira"})
    assert ok.status_code == 200

    row = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (out["proposal_id"],))
    # approved, not rejected: closing the task IS the acceptance, and a
    # rejection would be a false record of the verdict as well as a demotion
    assert row["status"] == "approved"
    assert row["reviewed_by"] == "mira"
    assert row["result_id"] == tid
    # and it does not sit in the queue asking again
    assert not db.query("SELECT id FROM pending_changes WHERE status = 'pending'")


def test_a_direct_close_records_how_well_the_sponsor_was_identified(client, fresh_db):
    """The settle stands in for a verdict, so it must record the verdict's
    STRENGTH rather than assume the weaker one.

    provenance.lineage reads `reviewed_strong` on approved rows and the panel
    turns a 0 into "Nobody used a personal API key for that verdict. This
    deployment identifies people by a self-asserted name." Hardcoded, that
    sentence is printed at a sponsor who used their key, about a deployment
    that requires one — a security surface stating the opposite of the truth.
    """
    from app import db
    from app.services import delegation, provenance, scope

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    delegation.submit_completion(tid, "shipped it", actor="scout")

    # closed with a personal key, which is what _strong builds
    ok = client.patch(f"/api/tasks/{tid}", json={"status": "done"}, headers=_strong(client, "mira"))
    assert ok.status_code == 200

    row = db.query_one(
        "SELECT reviewed_strong FROM pending_changes WHERE entity = 'task_completion'"
        " AND entity_id = ?",
        (tid,),
    )
    assert row["reviewed_strong"] == 1
    chain = provenance.lineage("task", tid, scope.Viewer("mira", True))
    assert chain["verdict_is_weak"] is False


def test_an_acceptance_that_can_never_apply_settles_instead_of_boomeranging(client, fresh_db):
    """The close that did not come through the sponsor guard — a reassignment
    voids the delegation, so the proposal's apply can never succeed again.

    A plain ValueError there resets the row to pending, which puts it back in
    the queue with an "apply failed" note, and the next verdict does the same.
    """
    from app import db
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    out = delegation.submit_completion(tid, "shipped it", actor="scout")
    # the sponsor reassigns, which clears the delegation the proposal names
    client.patch(f"/api/tasks/{tid}", json={"assignee": "mira"}, headers={"X-User": "mira"})

    # a note is required first: the reassignment orphaned the proposal, so
    # nobody sponsors it and review._sponsor_override demands a reason. That
    # refusal is not the boomerang — it leaves the row pending on purpose.
    refused = client.post(
        f"/api/review/{out['proposal_id']}/approve", json={}, headers=_strong(client, "mira")
    )
    assert refused.status_code == 400
    assert "needs a note" in refused.json()["detail"]

    r = client.post(
        f"/api/review/{out['proposal_id']}/approve",
        json={"note": "reassigned to a person, closing the agent's ask"},
        headers=_strong(client, "mira"),
    )
    assert r.status_code == 400
    row = db.query_one("SELECT * FROM pending_changes WHERE id = ?", (out["proposal_id"],))
    assert row["status"] == "rejected", "a pending reset here boomerangs forever"
    # and the settle must not read as a human judging the agent's work. Two
    # independent guards say so, because either alone would let it through:
    # the override marking (an orphaned delegation is nobody's verdict) and the
    # cleared strength (nobody judged this at all).
    assert row["reviewed_override"] == 1
    assert row["reviewed_strong"] == 0


def test_submit_completion_dedupes_pending(fresh_db):
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    delegation.submit_completion(tid, "round one", actor="scout")
    with pytest.raises(ValueError, match="already awaits acceptance"):
        delegation.submit_completion(tid, "round one again", actor="scout")


def test_rejection_keeps_task_open_and_feeds_trust(client, fresh_db):
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    out = delegation.submit_completion(tid, "done, trust me", actor="scout")
    r = client.post(
        f"/api/review/{out['proposal_id']}/reject",
        json={"note": "tests are red"},
        headers=_strong(client, "mira"),
    )
    assert r.json()["status"] == "rejected"
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (tid,))["status"] == (
        "in_progress"
    )
    row = next(
        s
        for s in delegation.trust_scores()
        if s["agent"] == "scout" and s["entity"] == "task_completion"
    )
    assert row["rejection_streak"] == 1
    # resubmission after the fix is open
    delegation.submit_completion(tid, "fixed and green", actor="scout")


def test_approved_rework_leaves_history_but_not_an_active_agent_correction(client, fresh_db):
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    rejected = delegation.submit_completion(tid, "first attempt", actor="scout")["proposal_id"]
    client.post(
        f"/api/review/{rejected}/reject",
        json={"note": "the acceptance check failed"},
        headers=_strong(client, "mira"),
    )
    approved = delegation.submit_completion(tid, "corrected attempt", actor="scout")["proposal_id"]
    client.post(
        f"/api/review/{approved}/approve",
        json={},
        headers=_strong(client, "mira"),
    )

    assert delegation.agent_inbox("scout")["rejected_proposals"] == []
    history = fresh_db.query(
        "SELECT id, status FROM pending_changes WHERE id IN (?, ?) ORDER BY id",
        (rejected, approved),
    )
    assert history == [
        {"id": rejected, "status": "rejected"},
        {"id": approved, "status": "approved"},
    ]
    score = next(
        row
        for row in delegation.trust_scores()
        if row["agent"] == "scout" and row["entity"] == "task_completion"
    )
    assert score["approved"] == 1
    assert score["rejected"] == 1


def test_replacement_agent_completion_resolves_the_original_correction(client, fresh_db):
    from app.services import delegation

    tid = _delegated_task(fresh_db)
    delegation.claim_task(tid, actor="scout")
    rejected = delegation.submit_completion(tid, "first attempt", actor="scout")["proposal_id"]
    client.post(
        f"/api/review/{rejected}/reject",
        json={"note": "give this to a replacement"},
        headers=_strong(client, "mira"),
    )
    delegation.delegate_task(tid, "fixer", "mira", actor="mira")
    replacement = delegation.submit_completion(tid, "replacement attempt", actor="fixer")[
        "proposal_id"
    ]
    client.post(
        f"/api/review/{replacement}/approve",
        json={},
        headers=_strong(client, "mira"),
    )

    assert delegation.agent_inbox("scout")["rejected_proposals"] == []


def test_agent_cannot_delegate_to_itself(fresh_db):
    from app.services import users, work

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    t = work.create_task(title="mine now", actor="mira")
    from app.services import delegation

    with pytest.raises(ValueError, match="itself"):
        delegation.delegate_task(t["id"], "scout", "mira", actor="scout", origin="agent")


def test_delegate_task_and_inbox(client, fresh_db):
    t = client.post(
        "/api/tasks",
        json={
            "title": "agent work",
            "description": "Read the evidence and write the result.",
            "due_date": "2026-08-31",
        },
    ).json()
    # a key: 'scribe' does not exist yet, and MINTING an agent identity takes
    # the scarce credential (routes/api.py::post_delegate)
    out = client.post(
        f"/api/tasks/{t['id']}/delegate",
        json={"agent": "scribe", "sponsor": "tester"},
        headers=_strong(),
    ).json()
    assert out["delegated_agent"] == "scribe"
    users = {u["name"]: u for u in client.get("/api/users").json()}
    assert users["scribe"]["kind"] == "agent"

    inbox = client.get("/api/agents/scribe/inbox").json()
    assert [x["id"] for x in inbox["delegated_tasks"]] == [t["id"]]
    assert inbox["delegated_tasks"][0]["description"] == "Read the evidence and write the result."
    assert inbox["delegated_tasks"][0]["due_date"] == "2026-08-31"

    mc = client.get("/api/agents").json()
    scribe = next(a for a in mc if a["agent"] == "scribe")
    assert scribe["open_tasks"] == 1
    assert scribe["identity_owner"] == "generic-agent" and scribe["delegatable"] is True


def test_mission_control_marks_machine_owned_identities_not_delegatable(client):
    from app.services import users

    users.ensure_agent_identity("mcp-reader")
    users.claim_machine_identity("mcp-reader", "mcp")

    row = next(a for a in client.get("/api/agents").json() if a["agent"] == "mcp-reader")
    assert row["identity_owner"] == "mcp" and row["delegatable"] is False


def test_reassign_ends_delegation(client, fresh_db):
    t = client.post("/api/tasks", json={"title": "work"}).json()
    client.post(
        f"/api/tasks/{t['id']}/delegate",
        json={"agent": "helper", "sponsor": "tester"},
        headers=_strong(),
    )
    client.patch(f"/api/tasks/{t['id']}", json={"assignee": "zoe"})
    row = fresh_db.query_one("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["delegated_agent"] == "" and row["sponsor"] == ""
    mc = client.get("/api/agents").json()
    helper = next(a for a in mc if a["agent"] == "helper")
    assert helper["open_tasks"] == 0


def test_agent_inbox_unknown_agent_is_an_error(client):
    assert client.get("/api/agents/definitely-a-typo/inbox").status_code == 404


def test_agent_delegated_done_proposal_auto_rejects_not_wedges(client, fresh_db):
    """A generic task/done proposal on an agent's OWN delegated task can never
    be approved into success. It must settle as rejected, not reset to pending
    where it would clutter /review until a human rejects it by hand."""
    from app.services import review

    tid = _delegated_task(fresh_db)
    p = review.propose_change("task", "update", {"status": "done"}, entity_id=tid, actor="scout")
    with pytest.raises(ValueError, match="auto-rejected"):
        review.approve_change(p["id"], actor="mira")
    row = fresh_db.query_one("SELECT status FROM pending_changes WHERE id = ?", (p["id"],))
    assert row["status"] == "rejected"  # settled, not boomeranged to pending
    # and the task itself stayed open — the escape is still closed
    assert fresh_db.query_one("SELECT status FROM tasks WHERE id = ?", (tid,))["status"] != "done"


def test_the_trust_read_never_reports_a_human(client):
    """Humans are in `pending_changes` too — services/ingest.py files every
    pasted line under the person who pasted it — so an unfiltered read put one
    teammate's approval rate and rejection streak in front of the whole
    roster. The filter belongs in the service, not in a caller."""
    from app.services import delegation, review, users

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    client.post(
        "/api/ingest",
        json={"text": "todo: ship it\ntodo: write it"},
        headers={"X-User": "mira"},
    )
    p = review.propose_change("task", "create", {"title": "from an agent"}, actor="scout")
    for row in review.list_changes("pending"):
        review.approve_change(row["id"], actor="ava")
    assert p

    rows = delegation.trust_scores()
    assert {r["agent"] for r in rows} == {"scout"}
    # and the route that renders it agrees
    served = client.get("/api/agents/trust").json()
    assert "mira" not in {r["agent"] for r in served}


def test_the_trust_read_scans_the_authority_proposals_once(client):
    """`promotion_blocked` reads every authority proposal, and that table is
    unindexed for this query (the index is on (proposed_by, entity)). Called
    per row it turned the Approvals page from 122 queries into 202 the moment
    agents started earning streaks — the N+1 this module removed from
    trust_scores, reintroduced one function over. The short-circuit hides it
    exactly while the trust program is not working.
    """
    from app import db
    from app.services import delegation, users

    for i in range(20):
        users.ensure_user(f"agent{i}", kind="agent")
        for entity in ("note", "task"):
            for _ in range(delegation.TRUST_STREAK):
                db.execute(
                    "INSERT INTO pending_changes (entity, entity_id, action, payload, summary,"
                    " proposed_by, origin, status, reviewed_by, reviewed_at, created_at,"
                    " reviewed_strong, reviewed_override) VALUES (?, 1, 'create', '{}', 's', ?,"
                    " 'agent', 'approved', 'ava', ?, ?, 1, 0)",
                    (entity, f"agent{i}", db.now(), db.now()),
                )

    seen = []
    real = db.query

    def counting(sql, params=()):
        seen.append(sql)
        return real(sql, params)

    db.query = counting
    try:
        rows = delegation.trust_scores()
    finally:
        db.query = real

    assert len(rows) == 40
    assert sum(1 for r in rows if r["suggestion"]) == 40, "every pair must be promotable here"
    scans = [s for s in seen if "entity = 'authority'" in s]
    assert len(scans) == 1, f"{len(scans)} scans for {len(rows)} pairs — the N+1 is back"


def test_reassignment_cannot_be_used_to_close_delegated_work(client, fresh_db):
    """Two writes end one delegation: closing the task, and reassigning it away
    from its agent (which clears delegated_agent and sponsor). Guarding only
    the first left the second as a two-call bypass of the whole loop."""
    from app.services import work

    tid = _delegated_task(fresh_db)
    r = client.patch(f"/api/tasks/{tid}", json={"assignee": "tester"})
    assert r.status_code == 403
    assert "sponsored by mira" in r.json()["detail"]
    # the delegation survived the refusal, so the acceptance path still exists
    assert fresh_db.query_one("SELECT sponsor FROM tasks WHERE id = ?", (tid,))["sponsor"] == "mira"

    # the sponsor may still end it — the verdict is theirs on either path
    work.update_task(tid, assignee="mira", actor="mira")
    assert fresh_db.query_one("SELECT sponsor FROM tasks WHERE id = ?", (tid,))["sponsor"] == ""


def test_an_agent_proposed_reassignment_auto_rejects_instead_of_boomeranging(client, fresh_db):
    """approve_change applies as the PROPOSER, so an agent-filed reassignment
    of delegated work can never be approved into success. A plain refusal there
    resets the proposal to pending and returns on every future verdict."""
    from app.services import review

    tid = _delegated_task(fresh_db)
    p = review.propose_change("task", "update", {"assignee": "mira"}, entity_id=tid, actor="scout")
    with pytest.raises(ValueError, match="auto-rejected"):
        review.approve_change(p["id"], actor="mira", strong=True)
    settled = fresh_db.query_one("SELECT status FROM pending_changes WHERE id = ?", (p["id"],))
    assert settled["status"] == "rejected"
