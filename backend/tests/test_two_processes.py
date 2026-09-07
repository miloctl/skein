"""Two real processes on one database: the fences that single-process tests
cannot exercise. The other process is a fresh interpreter with this worker's
SKEIN_DATABASE_URL, so it has its own PROCESS_ID and its own memory."""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
PRELUDE = "import json, sys\nfrom app import db\nfrom app.services import leases\n"


def other_process(code: str, *, wait: bool = True, timeout: float = 90):
    proc = subprocess.Popen(  # noqa: S603 — fixed interpreter, literal test program
        [sys.executable, "-c", PRELUDE + code],
        cwd=BACKEND,
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if not wait:
        return proc
    out, err = proc.communicate(timeout=timeout)
    assert proc.returncode == 0, err
    return json.loads(out.strip().splitlines()[-1]) if out.strip() else None


def _mint(db, name, kind="human"):
    db.execute(
        "INSERT INTO users (name, kind, active, created_at) VALUES (?, ?, 1, ?)",
        (name, kind, db.now()),
    )


def test_concurrent_boots_share_one_migration_lock(fresh_db):
    before = fresh_db.query("SELECT * FROM schema_version ORDER BY 1")
    boots = [other_process("db.init_db(); print(json.dumps('up'))", wait=False) for _ in range(2)]
    for boot in boots:
        out, err = boot.communicate(timeout=120)
        assert boot.returncode == 0, err
        assert out.strip().splitlines()[-1] == '"up"'
    assert fresh_db.query("SELECT * FROM schema_version ORDER BY 1") == before


def test_another_process_sweep_leaves_a_live_lease_and_takes_a_lapsed_one(fresh_db):
    from app.services import agent_wakeups, leases

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "backend-architect", "agent")
    with fresh_db.transaction():
        agent_wakeups.enqueue("backend-architect", 80, requested_by="sponsor")
    claim = agent_wakeups.claim_next()
    assert claim and claim["lease_owner"] == leases.PROCESS_ID

    swept = other_process("print(json.dumps(leases.sweep()))")
    assert swept["wakes"]["recovered"] == 0 and swept["renewed"] == 0
    row = fresh_db.query_row("SELECT status, lease_owner FROM agent_wakeups")
    assert row == {"status": "running", "lease_owner": leases.PROCESS_ID}

    fresh_db.execute("UPDATE agent_wakeups SET lease_until = ''")
    swept = other_process("print(json.dumps(leases.sweep()))")
    assert swept["wakes"]["recovered"] == 1
    row = fresh_db.query_row("SELECT status, reason FROM agent_wakeups")
    assert row == {"status": "completion_unknown", "reason": "lease_expired"}


def test_a_turn_claimed_by_a_process_that_died_is_reclaimed_after_its_lease(fresh_db):
    from app.services import agent_wakeups, leases

    _mint(fresh_db, "sponsor")
    _mint(fresh_db, "backend-architect", "agent")
    with fresh_db.transaction():
        agent_wakeups.enqueue("backend-architect", 80, requested_by="sponsor")
    owner = other_process(
        "from app.services import agent_wakeups\n"
        "print(json.dumps(agent_wakeups.claim_next()['lease_owner']))"
    )
    assert owner and owner != leases.PROCESS_ID
    # the claim is live: this process neither reclaims nor renews it
    assert agent_wakeups.reclaim_expired() == 0
    assert leases.renew() == 0
    assert agent_wakeups.finish is not None
    fresh_db.execute("UPDATE agent_wakeups SET lease_until = '2000-01-01T00:00:00+00:00'")
    assert agent_wakeups.reclaim_expired() == 1
    assert fresh_db.query_row("SELECT status FROM agent_wakeups") == {
        "status": "completion_unknown"
    }


def test_a_firing_and_a_delivery_each_apply_once_across_processes(fresh_db):
    from app.services.jobs import JobSpec, run_job

    other_process(
        "from app.services.jobs import JobSpec, run_job\n"
        "run_job(JobSpec('two-proc', lambda: {'n': 1}, {'trigger': 'cron', 'hour': 3}, 24))\n"
        "print(json.dumps(db.claim_job('forge-delivery', 'd-9')))"
    )
    run_job(JobSpec("two-proc", lambda: {"n": 1}, {"trigger": "cron", "hour": 3}, 24))
    outcomes = fresh_db.query_one("SELECT COUNT(*) AS n FROM job_outcomes WHERE job = 'two-proc'")
    assert outcomes["n"] == 1
    assert not fresh_db.claim_job("forge-delivery", "d-9")


def test_a_deployment_wide_cap_spent_elsewhere_refuses_here(fresh_db):
    from app import ratelimit

    other_process(
        "from app import ratelimit\n"
        "for _ in range(ratelimit.LIMITS['signin']):\n"
        "    ratelimit.check('signin', '198.51.100.4')\n"
        "print(json.dumps('spent'))"
    )
    with pytest.raises(ratelimit.RateLimited, match="per address"):
        ratelimit.check("signin", "198.51.100.4")
    ratelimit.check("signin", "198.51.100.5")


def test_a_sign_in_callback_on_another_process_completes_the_waiting_connect(fresh_db):
    import asyncio

    from app.agents import mcp_oauth

    flow = mcp_oauth._Flow("personal:ava:jira")
    provider = mcp_oauth.provider(
        {
            "id": 1,
            "server_id": "personal:ava:jira",
            "url": "https://jira.example/mcp",
            "oauth_redirect_uri": "https://skein.example/cb",
            "flow": flow,
        }
    )
    asyncio.run(provider.context.redirect_handler("https://idp.example/a?state=remote"))
    box: dict = {}
    waiter = threading.Thread(
        target=lambda: box.update(got=asyncio.run(provider.context.callback_handler()))
    )
    waiter.start()
    landed = other_process(
        "from app.agents import mcp_oauth\nprint(json.dumps(mcp_oauth.complete('remote', 'code-r')))"
    )
    assert landed is True
    waiter.join(10)
    assert box["got"] == ("code-r", "remote")
