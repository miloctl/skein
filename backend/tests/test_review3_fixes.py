"""Regression tests for the 5-agent round-3 review findings."""

import json


def test_apply_plan_skips_missing_tasks(client, fresh_db):
    from app.services import weekly

    t1 = client.post("/api/tasks", json={"title": "real"}).json()
    out = weekly.apply_plan("2026-W31", [t1["id"], 999999], actor="tester")
    assert out["committed"] == 1 and out["skipped"] == [999999]
    row = fresh_db.query_one("SELECT committed_week FROM tasks WHERE id = ?", (t1["id"],))
    assert row["committed_week"] == "2026-W31"

    try:
        weekly.apply_plan("2026-W31", [999999])
        assert False, "all-missing plan should raise"
    except ValueError:
        pass


def test_week_validation_everywhere(client):
    assert client.get("/api/week/draft?week=banana").status_code == 400
    assert client.get("/api/week?week=2026-W99").status_code == 400  # no such ISO week
    t = client.post("/api/tasks", json={"title": "x"}).json()
    assert client.patch(f"/api/tasks/{t['id']}",
                        json={"committed_week": "2026-W00"}).status_code == 400


def test_committed_week_clearable(client, fresh_db):
    t = client.post("/api/tasks", json={"title": "x"}).json()
    client.patch(f"/api/tasks/{t['id']}", json={"committed_week": "2026-W31"})
    client.patch(f"/api/tasks/{t['id']}", json={"committed_week": "-"})
    row = fresh_db.query_one("SELECT committed_week FROM tasks WHERE id = ?", (t["id"],))
    assert row["committed_week"] is None


def test_set_authority_rejects_blank_agent(client, fresh_db):
    assert client.post("/api/agents/authority",
                       json={"agent": "", "entity": "task",
                             "level": "notify"}).status_code == 400
    assert client.post("/api/agents/authority",
                       json={"agent": "   ", "entity": "task",
                             "level": "notify"}).status_code == 400
    # no phantom agent was minted
    assert not fresh_db.query_one(
        "SELECT * FROM users WHERE name = 'anonymous' AND kind = 'agent'")


def test_agent_inbox_unknown_agent_is_an_error(client):
    assert client.get("/api/agents/definitely-a-typo/inbox").status_code == 400


def test_supersede_with_bad_date_leaves_old_decision_intact(client, fresh_db):
    d = client.post("/api/decisions", json={"title": "T", "decision": "D"}).json()
    r = client.post(f"/api/decisions/{d['id']}/supersede",
                    json={"title": "N", "decision": "X", "review_by": "next quarter"})
    assert r.status_code == 400
    row = fresh_db.query_one("SELECT * FROM decisions WHERE id = ?", (d["id"],))
    assert row["status"] == "active" and row["superseded_by"] is None


def test_reconfirm_never_removes_the_half_life(client, fresh_db):
    d = client.post("/api/decisions",
                    json={"title": "T", "decision": "D",
                          "review_by": "2026-01-01"}).json()
    out = client.post(f"/api/decisions/{d['id']}/reconfirm", json={}).json()
    assert out["review_by"] is not None and out["review_by"] > "2026-07-01"


def test_review_by_must_be_a_date(client):
    r = client.post("/api/decisions",
                    json={"title": "T", "decision": "D", "review_by": "soonish"})
    assert r.status_code == 400


def test_what_if_ignores_expired_allocations(client):
    e = client.post("/api/engagements", json={"name": "Old"}).json()
    client.post(f"/api/engagements/{e['id']}/allocate",
                json={"person": "zoe", "percent": 80,
                      "starts_on": "2025-01-01", "ends_on": "2025-06-30"})
    req = client.post("/api/intake", json={"title": "new"}).json()
    out = client.post(f"/api/intake/{req['id']}/what-if",
                      json={"people": ["zoe"], "percent": 50}).json()
    zoe = out["projection"][0]
    assert zoe["current_percent"] == 0 and not zoe["overcommitted"]


def test_reassign_ends_delegation(client, fresh_db):
    t = client.post("/api/tasks", json={"title": "work"}).json()
    client.post(f"/api/tasks/{t['id']}/delegate",
                json={"agent": "helper", "sponsor": "tester"})
    client.patch(f"/api/tasks/{t['id']}", json={"assignee": "zoe"})
    row = fresh_db.query_one("SELECT * FROM tasks WHERE id = ?", (t["id"],))
    assert row["delegated_agent"] == "" and row["sponsor"] == ""
    mc = client.get("/api/agents").json()
    helper = next(a for a in mc if a["agent"] == "helper")
    assert helper["open_tasks"] == 0


def test_exec_readout_same_day_upserts_artifact(client):
    client.post("/api/portfolio/readout")
    client.post("/api/portfolio/readout")
    readouts = [a for a in client.get("/api/artifacts").json() if a["kind"] == "readout"]
    assert len(readouts) == 1


def test_weekly_claim_not_burned_by_empty_draft(fresh_db):
    from app.services import users, weekly, work

    assert weekly.propose_weekly_plan()["skipped"] == "nothing to commit"
    users.ensure_user("ann")
    fresh_db.execute("UPDATE users SET kind = 'human' WHERE name = 'ann'")
    from app.services.collab import post_standup

    post_standup("ann", today="x")
    work.create_task("late task", assignee="ann", actor="ann")
    out = weekly.propose_weekly_plan()
    assert out.get("status") == "pending"  # the empty run did not consume the week


def test_sweep_returns_post_flip_state(client):
    from app.services import collab

    client.post("/api/decisions", json={"title": "T", "decision": "D",
                                        "review_by": "2026-01-01"})
    swept = collab.sweep_stale_decisions()
    assert swept and all(d["status"] == "stale" for d in swept)


def test_mcp_forbidden_authority_holds(client, fresh_db, monkeypatch):
    from app import mcp_server
    from app.services import delegation

    monkeypatch.setattr(mcp_server, "ACTOR", "mcp-agent")
    delegation.set_authority("mcp-agent", "task", "forbidden", actor="tester")
    try:
        mcp_server._check_authority("task")
        assert False, "forbidden must raise"
    except ValueError as exc:
        assert "forbidden" in str(exc)
    mcp_server._check_authority("decision")  # default review level passes


def test_extra_tools_security_cuts(monkeypatch):
    from app import config
    from app.agents import extra_tools as mod

    monkeypatch.setattr(config, "EXTRA_TOOLS",
                        ("http_request", "use_agent", "use_llm", "workflow", "diagram"))
    mod.extra_tools.cache_clear()
    assert mod.extra_tools() == ()
    mod.extra_tools.cache_clear()


def test_authority_not_self_serviceable_by_agents(client, fresh_db):
    from app.services import delegation, users

    users.ensure_user("sneaky", kind="agent")
    try:
        delegation.set_authority("task-bot", "task", "autonomous", actor="sneaky")
        assert False, "agent actor must not set authority"
    except ValueError as exc:
        assert "humans" in str(exc)
    try:
        delegation.set_authority("planner", "task", "autonomous", actor="planner")
        assert False, "self-target must be refused"
    except ValueError:
        pass
    out = delegation.set_authority("task-bot", "task", "notify", actor="tester")
    assert out["level"] == "notify"  # humans still can
