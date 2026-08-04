"""Decisions: half-life, supersede chains, reconfirm, and the charter category."""

from datetime import UTC, datetime, timedelta

import pytest


def _days_ahead(days: int) -> str:
    return (datetime.now(UTC).date() + timedelta(days=days)).isoformat()


def test_supersede_with_bad_date_leaves_old_decision_intact(client, fresh_db):
    d = client.post("/api/decisions", json={"title": "T", "decision": "D"}).json()
    r = client.post(
        f"/api/decisions/{d['id']}/supersede",
        json={"title": "N", "decision": "X", "review_by": "2026-13-45"},
    )
    assert r.status_code == 400
    row = fresh_db.query_one("SELECT * FROM decisions WHERE id = ?", (d["id"],))
    assert row["status"] == "active" and row["superseded_by"] is None


def test_reconfirm_never_removes_the_half_life(client, fresh_db):
    d = client.post(
        "/api/decisions", json={"title": "T", "decision": "D", "review_by": "2026-01-01"}
    ).json()
    out = client.post(f"/api/decisions/{d['id']}/reconfirm", json={}).json()
    assert out["review_by"] is not None and out["review_by"] > "2026-07-01"


def test_review_by_must_be_a_date(client):
    r = client.post("/api/decisions", json={"title": "T", "decision": "D", "review_by": "soonish"})
    assert r.status_code == 400


def test_decision_half_life_sweep_and_supersede(client, fresh_db):
    from app.services import collab

    d = client.post(
        "/api/decisions",
        json={"title": "Use SQLite", "decision": "keep it simple", "review_by": "2026-01-01"},
    ).json()
    stale = collab.sweep_stale_decisions()
    assert [s["id"] for s in stale] == [d["id"]]
    assert collab.sweep_stale_decisions() == []  # status flip is the claim
    row = fresh_db.query_one("SELECT * FROM decisions WHERE id = ?", (d["id"],))
    assert row["status"] == "stale"

    new = client.post(
        f"/api/decisions/{d['id']}/supersede",
        json={"title": "Use SQLite + Litestream", "decision": "replicate off-box"},
    ).json()
    old = fresh_db.query_one("SELECT * FROM decisions WHERE id = ?", (d["id"],))
    assert old["status"] == "superseded" and old["superseded_by"] == new["id"]
    r = client.post(
        f"/api/decisions/{d['id']}/supersede", json={"title": "again", "decision": "no"}
    )
    assert r.status_code == 400


def test_decision_reconfirm(client, fresh_db):
    d = client.post(
        "/api/decisions", json={"title": "T", "decision": "D", "review_by": "2026-01-01"}
    ).json()
    from app.services import collab

    collab.sweep_stale_decisions()
    out = client.post(
        f"/api/decisions/{d['id']}/reconfirm", json={"review_by": _days_ahead(90)}
    ).json()
    assert out["status"] == "active"


def test_charter_decisions(client, fresh_db):
    client.post(
        "/api/decisions",
        json={
            "title": "Escalation path",
            "decision": "page the lead after 2h",
            "review_by": "2099-01-01",
            "category": "charter",
        },
    )
    client.post("/api/decisions", json={"title": "normal", "decision": "x"})
    charter = client.get("/api/decisions?category=charter").json()
    assert len(charter) == 1 and charter[0]["title"] == "Escalation path"
    assert len(client.get("/api/decisions").json()) == 2
    r = client.post("/api/decisions", json={"title": "x", "decision": "y", "category": "bogus"})
    assert r.status_code == 400


def test_charter_supersede_keeps_category_and_requires_review_by(client, fresh_db):
    from app.services.collab import record_decision, supersede_decision

    with pytest.raises(ValueError, match="review_by"):
        record_decision("no date", "x", category="charter", actor="m")
    old = record_decision(
        "Quality bar", "tests before merge", review_by="2099-01-01", category="charter", actor="m"
    )
    new = supersede_decision(old["id"], "Quality bar v2", "tests + lint before merge", actor="m")
    charter = client.get("/api/decisions?category=charter").json()
    by_id = {d["id"]: d for d in charter}
    assert new["id"] in by_id  # the replacement stays on the charter page
    assert by_id[new["id"]]["review_by"] is not None  # 90d default applied
