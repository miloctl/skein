"""Adoption telemetry: per-surface attribution, the anonymous exclusion, and the daily upsert."""


def test_adoption_excludes_deactivated_users(client, fresh_db):
    from app.services import adoption, users

    users.ensure_user("ghost")
    adoption.record_use("ghost", "api")
    adoption.record_use("tester", "api")
    users.ensure_user("tester")
    users.set_active("ghost", False, actor="tester")
    d = adoption.adoption()
    # no per-person list to inspect (it was the anti-surveillance leak); the
    # team COUNT proves the exclusion — two users touched the tool, the
    # deactivated one does not count
    assert "active_users" not in d
    assert d["weekly_active_users"] == 1


def test_adoption_records_by_surface(client, fresh_db):
    client.post("/api/capture", json={"text": "todo: from web"}, headers={"X-Client": "web"})
    client.post("/api/capture", json={"text": "todo: from cli"}, headers={"X-Client": "cli"})
    client.post("/api/capture", json={"text": "todo: no client header"})

    out = client.get("/api/adoption").json()
    surfaces = {r["surface"]: r["actions"] for r in out["by_surface"]}
    assert surfaces.get("web") == 1 and surfaces.get("cli") == 1
    assert surfaces.get("api") >= 1
    assert out["weekly_active_users"] == 1
    assert out["captures_in_window"] == 3
    assert out["non_web_share"] is not None


def test_adoption_ignores_anonymous(client, fresh_db):
    client.get("/api/briefing", headers={"X-User": ""})
    rows = fresh_db.query("SELECT * FROM tool_usage")
    assert all(r["user"] != "anonymous" for r in rows)


def test_adoption_upserts_daily_row(client, fresh_db):
    for _ in range(3):
        client.get("/api/briefing", headers={"X-Client": "web"})
    rows = fresh_db.query("SELECT * FROM tool_usage WHERE user = 'tester' AND surface = 'web'")
    assert len(rows) == 1 and rows[0]["actions"] == 3


def test_record_use_buffers_between_flushes(fresh_db, monkeypatch):
    from app.services import adoption

    monkeypatch.setattr(adoption, "FLUSH_SECONDS", 3600.0)
    adoption.record_use("ava", "web")  # first call after reset flushes and arms the clock
    adoption.record_use("ava", "web")  # inside the window: buffered, not written
    assert fresh_db.query_row("SELECT SUM(actions) AS n FROM tool_usage")["n"] == 1
    adoption.flush()
    assert fresh_db.query_row("SELECT SUM(actions) AS n FROM tool_usage")["n"] == 2
