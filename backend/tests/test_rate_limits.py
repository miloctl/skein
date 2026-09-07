"""Rate caps and body caps on the write paths."""

import asyncio
import threading

import pytest


def test_unsigned_shared_cap_runs_off_the_event_loop(fresh_db, monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request

    from app import config, ratelimit
    from app.routes.webhooks import forge_webhook

    monkeypatch.setattr(config, "FORGE_WEBHOOK_SECRET", "test-secret")
    loop_thread = threading.get_ident()
    workers = []
    check = ratelimit.check

    def checked(*args, **kwargs):
        workers.append(threading.get_ident())
        return check(*args, **kwargs)

    monkeypatch.setattr(ratelimit, "check", checked)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"content-length", b"999999999")],
            "client": ("198.51.100.12", 1234),
        }
    )
    with pytest.raises(HTTPException) as refused:
        asyncio.run(forge_webhook(request))
    assert refused.value.status_code == 400
    assert workers and all(worker != loop_thread for worker in workers)
    assert fresh_db.query_row("SELECT surface, count FROM rate_hits") == {
        "surface": "forge_addr",
        "count": 1,
    }


def test_shared_counter_lock_wait_is_bounded(fresh_db, monkeypatch):
    from psycopg.errors import LockNotAvailable

    from app import db, ratelimit

    monkeypatch.setattr(db, "TRANSACTION_LOCK_TIMEOUT", "50ms")
    monkeypatch.setattr(ratelimit, "_window", lambda: 1)
    ratelimit.check("signin", "198.51.100.13")
    holding, release = threading.Event(), threading.Event()

    def hold_counter():
        with db.transaction():
            db.execute("UPDATE rate_hits SET count = count WHERE surface = 'signin'")
            holding.set()
            release.wait(5)

    holder = threading.Thread(target=hold_counter)
    holder.start()
    assert holding.wait(5)
    timer = threading.Timer(0.5, release.set)
    timer.start()
    try:
        with pytest.raises(LockNotAvailable):
            ratelimit.check("signin", "198.51.100.13")
    finally:
        release.set()
        holder.join(5)
        timer.cancel()
    assert fresh_db.query_row("SELECT count FROM rate_hits")["count"] == 1


def test_create_bodies_are_capped(client, fresh_db):
    r = client.post("/api/notes", json={"topic": "big", "content": "x" * 50_000})
    assert r.status_code == 422
    r = client.post("/api/chat", json={"message": "x" * 50_000})
    assert r.status_code == 422


def test_write_rate_cap_enforced_on_create_routes(client, fresh_db):
    for i in range(30):
        assert client.post("/api/tasks", json={"title": f"t{i}"}).status_code == 200
    r = client.post("/api/tasks", json={"title": "t31"})
    assert r.status_code == 429 and "The limit for write" in r.json()["detail"]


def test_rate_caps(client, fresh_db):
    from app import ratelimit

    ratelimit.reset()
    for i in range(30):
        client.post("/api/capture", json={"text": f"note: filler {i}"})
    r = client.post("/api/capture", json={"text": "note: one too many"})
    assert r.status_code == 429 and "The limit for capture" in r.json()["detail"]
    ratelimit.reset()
    assert client.post("/api/capture", json={"text": "note: fine again"}).status_code == 200


def test_a_refusal_is_429_with_a_computed_retry(client, fresh_db):
    """As a bare ValueError the cap answered 400 — wire-identical to a
    malformed request, so no client could tell throttling from a typo, and
    with no Retry-After nothing knew when to come back. The wait is computed
    from the window, not quoted from WINDOW_SECONDS: the caller can act on an
    exact number and cannot act on a guess."""
    for i in range(30):
        client.post("/api/tasks", json={"title": f"t{i}"})
    r = client.post("/api/tasks", json={"title": "one too many"})
    assert r.status_code == 429
    retry = int(r.headers["Retry-After"])
    assert 1 <= retry <= 60
    assert f"Wait {retry} second" in r.json()["detail"]
    # the refusal names the fix as an imperative and never says "slow down"
    assert "then send the request again" in r.json()["detail"]


def test_rate_limited_stays_catchable_as_valueerror():
    """tools/_gate.py and the mock agent catch ValueError to hand the refusal
    string to the model. RateLimited leaving that hierarchy would turn every
    in-turn cap into an unhandled exception mid-stream."""
    import pytest

    from app import ratelimit

    ratelimit.reset()
    with pytest.raises(ValueError):
        for _ in range(31):
            ratelimit.check("write", "someone")
    ratelimit.reset()


def test_a_multi_slot_charge_names_its_cost(client, fresh_db):
    """Measured live: six flock turns in a minute, and the seventh refusal
    said the cap was twenty per minute — a number the caller's own experience
    contradicts, because a flock turn charges member-count slots. The refusal
    must name the request's cost or the arithmetic reads as a lie."""
    import pytest

    from app import ratelimit

    ratelimit.reset()
    with pytest.raises(ratelimit.RateLimited, match="This request uses 5 slots"):
        for _ in range(5):
            ratelimit.check("chat", "someone", cost=5)
    ratelimit.reset()


class _Req:
    """The two attributes client_addr reads off a Request."""

    def __init__(self, peer, xff=None):
        self.headers = {"x-forwarded-for": xff} if xff else {}
        self.client = type("C", (), {"host": peer})() if peer else None


def test_client_addr_ignores_the_header_at_zero_hops(monkeypatch):
    """Trusting X-Forwarded-For on a direct connection lets any caller pick
    their own bucket key, which unmakes the cap."""
    from app import config, ratelimit

    monkeypatch.setattr(config, "TRUST_PROXY_HOPS", 0)
    assert ratelimit.client_addr(_Req("10.0.0.9", xff="6.6.6.6")) == "10.0.0.9"


def test_client_addr_reads_the_declared_proxy_depth(monkeypatch):
    """Behind the OpenShift router (1 hop), the socket peer is the router —
    one signin bucket for the whole team. Entry -N is the client as the
    outermost TRUSTED proxy saw it; entries left of that are caller-typed."""
    from app import config, ratelimit

    monkeypatch.setattr(config, "TRUST_PROXY_HOPS", 1)
    req = _Req("10.128.0.1", xff="203.0.113.7")
    assert ratelimit.client_addr(req) == "203.0.113.7"
    # a caller-crafted prefix does not move the trusted entry
    spoofed = _Req("10.128.0.1", xff="6.6.6.6, 203.0.113.7")
    assert ratelimit.client_addr(spoofed) == "203.0.113.7"


def test_client_addr_falls_back_when_the_header_is_short(monkeypatch):
    """An in-cluster caller that bypasses the router sends no header — the
    socket peer is then the honest answer, not a crash or an empty key."""
    from app import config, ratelimit

    monkeypatch.setattr(config, "TRUST_PROXY_HOPS", 1)
    assert ratelimit.client_addr(_Req("10.0.0.9")) == "10.0.0.9"
    assert ratelimit.client_addr(_Req(None)) == "unknown"


def test_signin_buckets_follow_the_forwarded_client(client, monkeypatch):
    """The end-to-end consequence: with hops declared, two browsers behind
    one router get two signin buckets, not one shared throttle."""
    from app import config

    monkeypatch.setattr(config, "TRUST_PROXY_HOPS", 1)
    monkeypatch.setattr(config, "CORS_ORIGINS", ["https://ui.test"])
    monkeypatch.setattr(config, "AUTH_MODE", "oidc")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", "skein-web")
    for _ in range(10):  # exhaust one client's bucket
        client.post(
            "/api/auth/token",
            json={"code": "x", "code_verifier": "v", "redirect_uri": "u"},
            headers={"Origin": "https://ui.test", "X-Forwarded-For": "203.0.113.7"},
        )
    r = client.post(
        "/api/auth/token",
        json={"code": "x", "code_verifier": "v", "redirect_uri": "u"},
        headers={"Origin": "https://ui.test", "X-Forwarded-For": "203.0.113.7"},
    )
    assert r.status_code == 429 and "per address" in r.json()["detail"]
    r = client.post(
        "/api/auth/token",
        json={"code": "x", "code_verifier": "v", "redirect_uri": "u"},
        headers={"Origin": "https://ui.test", "X-Forwarded-For": "203.0.113.8"},
    )
    assert r.status_code != 429


def test_the_write_bucket_is_per_person_even_on_the_shared_agent(fresh_db, monkeypatch):
    """The default chat identity is one name ("agent") for the whole team.
    Keyed on the actor alone, the gate made it one team-wide 30/minute
    bucket — person B's write refused because person A was mid-turn, under a
    message claiming the cap was per person. The gate keys on the
    (agent, requester) pair now, so each person spends only their own."""
    import json

    from app import ratelimit
    from app.agents.identity import (
        reset_agent_identity,
        reset_requester_identity,
        set_agent_identity,
        set_requester_identity,
    )
    from app.tools._gate import gated_write

    monkeypatch.setitem(ratelimit.LIMITS, "write", 2)
    ratelimit.reset()
    t1 = set_agent_identity("agent")
    r1 = set_requester_identity("ava")
    try:
        for _ in range(2):
            gated_write("task", "create", {"title": "a"}, direct=lambda: {"id": 0})
        out = json.loads(gated_write("task", "create", {"title": "a"}, direct=lambda: {"id": 0}))
        assert "The limit for write" in out["error"]
    finally:
        reset_requester_identity(r1)
        reset_agent_identity(t1)
    # ava spent HER budget against the agent; marcus still holds his own
    t2 = set_agent_identity("agent")
    r2 = set_requester_identity("marcus")
    try:
        out = json.loads(gated_write("task", "create", {"title": "b"}, direct=lambda: {"id": 0}))
        assert "error" not in out
    finally:
        reset_requester_identity(r2)
        reset_agent_identity(t2)
    ratelimit.reset()


def test_deployment_wide_caps_count_across_processes(fresh_db):
    """A per-process count would let each process hand out the whole cap."""
    import pytest

    from app import db, ratelimit

    # what other processes already counted in this window
    db.execute(
        "INSERT INTO rate_hits (surface, key, window_start, count) VALUES (?, ?, ?, ?)",
        ("signin", "203.0.113.7", ratelimit._window(), ratelimit.LIMITS["signin"] - 1),
    )
    ratelimit.check("signin", "203.0.113.7")
    with pytest.raises(ratelimit.RateLimited, match="per address") as refused:
        ratelimit.check("signin", "203.0.113.7")
    assert 1 <= refused.value.retry_after <= 60
    ratelimit.check("signin", "203.0.113.8")
    # a per-person cap writes no row
    ratelimit.check("write", "someone")
    surfaces = {row["surface"] for row in db.query("SELECT surface FROM rate_hits")}
    assert surfaces == {"signin"}
    # only windows no request can still count against are pruned
    db.execute(
        "INSERT INTO rate_hits (surface, key, window_start, count) VALUES ('export', 'x', ?, 1)",
        (ratelimit._window() - 2,),
    )
    assert ratelimit.prune() == 1
    assert db.query_one("SELECT 1 FROM rate_hits WHERE surface = 'signin'")
