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
    assert client.get("/api/context-pack?engagement=999").status_code == 400
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
