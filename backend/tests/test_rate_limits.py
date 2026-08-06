"""Rate caps and body caps on the write paths."""


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
    with pytest.raises(ratelimit.RateLimited, match="This request uses 5 of them"):
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
    monkeypatch.setattr(config, "AUTH_MODE", "oidc")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", "skein-web")
    for _ in range(10):  # exhaust one client's bucket
        client.post(
            "/api/auth/token",
            json={"code": "x", "code_verifier": "v", "redirect_uri": "u"},
            headers={"X-Forwarded-For": "203.0.113.7"},
        )
    r = client.post(
        "/api/auth/token",
        json={"code": "x", "code_verifier": "v", "redirect_uri": "u"},
        headers={"X-Forwarded-For": "203.0.113.7"},
    )
    assert r.status_code == 429 and "per address" in r.json()["detail"]
    r = client.post(
        "/api/auth/token",
        json={"code": "x", "code_verifier": "v", "redirect_uri": "u"},
        headers={"X-Forwarded-For": "203.0.113.8"},
    )
    assert r.status_code != 429

