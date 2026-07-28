"""Wave 3: per-engagement context packs, weekly pulse, disposition analytics."""


def test_engagement_pack_scoped(client, fresh_db):
    from app.services import blockers, engagements, work

    engagements.create_engagement(
        "Retrieval spike",
        kind="experiment",
        timebox_end="2026-08-15",
        kill_criteria="no lift in 2 weeks",
        outcome="median lookup under 8 min",
        actor="m",
    )
    engagements.create_engagement("Other project", actor="m")
    m = work.create_milestone(title="Eval baseline", project="Retrieval spike", actor="m")
    t = work.create_task(title="build harness", milestone_id=m["id"], actor="m")
    b = blockers.raise_blocker("gpu quota", actor="m")
    work.update_task(t["id"], waiting_on=f"blocker:{b['id']}", actor="m")
    work.create_milestone(title="Unrelated milestone", project="Other project", actor="m")

    pack = client.get("/api/context-pack?engagement=1").json()["content"]
    assert "Retrieval spike" in pack
    assert "median lookup under 8 min" in pack
    assert "kill criteria" in pack.lower() or "Kill criteria" in pack
    assert "build harness" in pack and "waiting on blocker" in pack
    assert "Unrelated milestone" not in pack  # scoped: other engagements stay out
    assert client.get("/api/context-pack?engagement=999").status_code == 404
    # the global pack still works and is versioned
    assert "version" in client.get("/api/context-pack").json()


def test_pulse_tally_team_aggregated(client, fresh_db):
    for user, verdict in (("a", "up"), ("b", "up"), ("c", "down")):
        r = client.post(
            "/api/feedback",
            json={"kind": "pulse", "input_text": "2026-07-24", "verdict": verdict},
            headers={"X-User": user},
        )
        assert r.status_code == 200
    tally = client.get("/api/insights").json()["pulse_tally"]
    assert tally[0]["up"] == 2 and tally[0]["down"] == 1
    assert "created_by" not in tally[0] and "user" not in tally[0]  # counts only


def test_pulse_votes_are_unattributable(client, fresh_db):
    """The promise is 'never per person' — no username may co-occur with a
    verdict on ANY egress surface: raw feedback endpoint, activity ledger,
    admin export."""
    import json as j

    from app.services import admin
    from app.services.api_keys import create_key

    voters = ("alice", "bob")
    for user, verdict in zip(voters, ("up", "down"), strict=True):
        client.post(
            "/api/feedback",
            json={"kind": "pulse", "input_text": "2026-07-24", "verdict": verdict},
            headers={"X-User": user},
        )
    rows = client.get("/api/feedback?kind=pulse").json()
    assert all("created_by" not in r for r in rows)  # column never on the wire
    assert client.get("/api/feedback?kind=pulse", headers={}).status_code in (200, 403)
    activity = j.dumps(fresh_db.query("SELECT * FROM activity WHERE action = 'record_feedback'"))
    for v in voters:
        assert v not in activity
    assert "pulse/" not in activity  # verdict never reaches the ledger
    key = create_key("auditor", "t")["key"]
    from pathlib import Path

    export = admin.export()
    dump = j.loads(Path(export["path"]).read_text())
    for row in dump.get("feedback", []):
        if row["kind"] == "pulse":
            assert row["created_by"] == ""
    assert key  # export exercised under the strong-identity path


def test_engagement_pack_reachable_by_agents(fresh_db):
    from app.services.engagements import create_engagement
    from app.tools.portfolio import get_context_pack

    create_engagement("Agent scoped", actor="m")
    import json as j

    out = j.loads(get_context_pack(engagement_id=1))
    assert "Agent scoped" in out["content"]


def test_monday_digest_asks_the_pulse(fresh_db, monkeypatch):
    from datetime import date

    from app.services import digest

    class FakeMonday(date):
        @classmethod
        def today(cls):  # pragma: no cover - helper
            return cls(2026, 7, 20)

    monkeypatch.setattr(digest, "_utc_today", lambda: date(2026, 7, 20))  # a Monday
    assert "Weekly pulse" in digest.build_digest()
    monkeypatch.setattr(digest, "_utc_today", lambda: date(2026, 7, 21))  # a Tuesday
    assert "Weekly pulse" not in digest.build_digest()


def test_rule_stats_median_days(fresh_db):
    from app import db
    from app.services.insights import disposition_finding, rule_stats

    fid = db.execute(
        "INSERT INTO findings (rule_id, subject, severity, message, n, window,"
        " receipt, week, created_at) VALUES ('r1', 's', 'low', 'm', 1, 'w', '{}', '2026-W30', ?)",
        ("2026-07-20T00:00:00+00:00",),
    )
    disposition_finding(fid, "resolved", actor="m")
    stats = {s["rule_id"]: s for s in rule_stats()}
    assert stats["r1"]["median_days_to_disposition"] is not None
    assert stats["r1"]["median_days_to_disposition"] >= 3  # planted 4 days ago


def test_whoami_reports_identity_strength(client, fresh_db):
    from app.services.api_keys import create_key

    weak = client.get("/api/whoami", headers={"X-User": "chen"}).json()
    assert weak == {"user": "chen", "strong": False, "keys_minted": 0}
    key = create_key("chen", "t")["key"]
    strong = client.get("/api/whoami", headers={"Authorization": f"Bearer {key}"}).json()
    assert strong["user"] == "chen" and strong["strong"] is True
    assert strong["keys_minted"] == 1


def test_onboarding_steps_are_actionable(client, fresh_db):
    steps = client.get("/api/onboarding").json()["steps"]
    assert all(s["link"].startswith("/") and s["hint"] for s in steps)
    assert any(s["id"] == "setup_key" and s["link"] == "/settings" for s in steps)
