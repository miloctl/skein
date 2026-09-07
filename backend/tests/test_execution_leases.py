"""Execution leases: renewal is per process, reclaim is by lapse, and the
lifespan owns the heartbeat."""


def _mint(db, name, kind="human"):
    db.execute(
        "INSERT INTO users (name, kind, active, created_at) VALUES (?, ?, 1, ?)",
        (name, kind, db.now()),
    )


def test_renew_extends_only_this_process_and_the_sweep_reclaims_the_lapsed(fresh_db):
    from app.services import agent_wakeups, leases

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "mine", "agent")
    _mint(fresh_db, "theirs", "agent")
    with fresh_db.transaction():
        agent_wakeups.enqueue("mine", 80, requested_by="sponsor")
        agent_wakeups.enqueue("theirs", 81, requested_by="sponsor")
    assert agent_wakeups.claim_next() and agent_wakeups.claim_next()
    stale = "2000-01-01T00:00:00+00:00"
    fresh_db.execute("UPDATE agent_wakeups SET lease_until = ?", (stale,))
    fresh_db.execute("UPDATE agent_wakeups SET lease_owner = 'other' WHERE agent = 'theirs'")

    assert leases.renew() == 1
    until = {
        row["agent"]: row["lease_until"]
        for row in fresh_db.query("SELECT agent, lease_until FROM agent_wakeups")
    }
    assert until["mine"] > fresh_db.now()
    assert until["theirs"] == stale

    result = leases.sweep()
    assert result["wakes"]["recovered"] == 1
    status = {
        row["agent"]: (row["status"], row["reason"])
        for row in fresh_db.query("SELECT agent, status, reason FROM agent_wakeups")
    }
    assert status == {"mine": ("running", ""), "theirs": ("completion_unknown", "lease_expired")}


def test_lifespan_owns_the_heartbeat(fresh_db):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import leases

    with TestClient(app):
        assert leases._thread is not None and leases._thread.is_alive()
    assert leases._thread is None
