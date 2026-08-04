"""Portfolio: engagement health with receipts, slip forecast, and the exec readout artifact."""

from datetime import UTC, datetime, timedelta


def _days_ahead(days: int) -> str:
    return (datetime.now(UTC).date() + timedelta(days=days)).isoformat()


def _engagement_with_milestone(client, name="Apollo", due=""):
    client.post("/api/engagements", json={"name": name})
    m = client.post(
        "/api/milestones", json={"title": f"{name} m1", "project": name, "due_date": due}
    ).json()
    return m


def test_forecast_snapshot_idempotent_per_day(client, fresh_db):
    from app.services import adoption

    client.post("/api/engagements", json={"name": "Fx"})
    client.post("/api/milestones", json={"title": "m", "project": "Fx", "due_date": "2030-01-01"})
    adoption.snapshot_forecasts()
    adoption.snapshot_forecasts()
    rows = fresh_db.query("SELECT * FROM forecast_snapshots")
    assert len(rows) == 1
    assert rows[0]["due_date"] == "2030-01-01"


def test_engagement_health_receipts(client, fresh_db):
    m = _engagement_with_milestone(client, "Apollo", due="2026-01-01")
    health = client.get("/api/portfolio/health").json()
    apollo = next(h for h in health if h["name"] == "Apollo")
    assert apollo["health"] == "yellow"
    assert any("overdue" in r for r in apollo["receipts"])

    # escalated linked blocker turns it red
    t = client.post("/api/tasks", json={"title": "t", "milestone_id": m["id"]}).json()
    b = client.post("/api/blockers", json={"title": "vendor", "task_id": t["id"]}).json()
    fresh_db.execute("UPDATE blockers SET status = 'escalated' WHERE id = ?", (b["id"],))
    health = client.get("/api/portfolio/health").json()
    apollo = next(h for h in health if h["name"] == "Apollo")
    assert apollo["health"] == "red"
    assert any("escalated" in r for r in apollo["receipts"])


def test_health_green_when_clean(client):
    _engagement_with_milestone(client, "Zen", due=_days_ahead(30))
    health = client.get("/api/portfolio/health").json()
    zen = next(h for h in health if h["name"] == "Zen")
    assert zen["health"] == "green" and zen["receipts"] == []


def test_slip_forecast_uses_history(client, fresh_db):
    _engagement_with_milestone(client, "Hist", due=_days_ahead(10))
    done = client.post(
        "/api/milestones", json={"title": "old", "project": "Hist", "due_date": "2026-06-01"}
    ).json()
    client.patch(f"/api/milestones/{done['id']}", json={"status": "done"})
    fresh_db.execute(
        "UPDATE milestones SET completed_at = '2026-06-08T00:00:00' WHERE id = ?", (done["id"],)
    )
    out = client.get("/api/portfolio/forecast").json()
    assert out["basis"]["milestones_measured"] == 1
    assert out["basis"]["median_slip_days"] == 7.0
    f = out["forecasts"][0]
    assert f["forecast_date"] > f["due_date"]


def test_exec_readout_artifact(client):
    _engagement_with_milestone(client, "Read Me", due="2026-01-01")
    client.post(
        "/api/commitments",
        json={"promise": "demo to CEO", "to_whom": "CEO", "due_date": _days_ahead(3)},
    )
    out = client.post("/api/portfolio/readout").json()
    assert "Exec readout" in out["markdown"]
    assert "Read Me" in out["markdown"]
    assert "demo to CEO" in out["markdown"]
    assert any(a["kind"] == "readout" for a in client.get("/api/artifacts").json())


def test_exec_readout_same_day_upserts_artifact(client):
    client.post("/api/portfolio/readout")
    client.post("/api/portfolio/readout")
    readouts = [a for a in client.get("/api/artifacts").json() if a["kind"] == "readout"]
    assert len(readouts) == 1


def test_readout_excludes_team_commitments(client, fresh_db):
    from app.services.readout import exec_readout

    client.post(
        "/api/commitments",
        json={"promise": "team-only promise", "audience": "team", "due_date": "2026-07-30"},
    )
    md = exec_readout(actor="tester")["markdown"]
    assert "team-only promise" not in md


def test_health_red_on_two_overdue_with_stale_and_silence_receipts(client, fresh_db):
    from datetime import datetime, timedelta

    from app.services import portfolio
    from app.services.slas import SILENCE_DAYS, STALE_WIP_DAYS

    client.post("/api/engagements", json={"name": "Slow"})
    m = None
    for title in ("m1", "m2"):
        m = client.post(
            "/api/milestones", json={"title": title, "project": "Slow", "due_date": "2020-01-01"}
        ).json()
    t = client.post("/api/tasks", json={"title": "old wip", "milestone_id": m["id"]}).json()
    client.patch(f"/api/tasks/{t['id']}", json={"status": "in_progress"})
    ancient = (datetime.now(UTC) - timedelta(days=max(STALE_WIP_DAYS, SILENCE_DAYS) + 1)).isoformat(
        timespec="seconds"
    )
    fresh_db.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (ancient, t["id"]))

    [h] = [e for e in portfolio.engagement_health() if e["name"] == "Slow"]
    assert h["health"] == "red"  # two overdue milestones make red without any blocker
    receipts = " ".join(h["receipts"])
    assert "overdue since 2020-01-01" in receipts
    assert f"in progress >{STALE_WIP_DAYS}d" in receipts
    assert "no task activity since" in receipts


def test_wait_satisfied_queries_cover_every_waiting_on_type():
    from app.services import portfolio, work

    # a type added to work.WAITING_ON_TYPES without a satisfied-query in
    # portfolio._WAIT_SATISFIED KeyErrors _satisfied_targets and 500s /portfolio
    assert set(portfolio._WAIT_SATISFIED) == set(work.WAITING_ON_TYPES)
