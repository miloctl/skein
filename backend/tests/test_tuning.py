"""Admin-tunable runtime limits: bounds, invariants, and whether a change
actually reaches the code that enforces it.

The last part is the point. A settings surface that reports a number the
enforcement path never reads is worse than no surface at all — it tells an
administrator the deployment is configured one way while it runs another.
"""

import pytest

from app import db, ratelimit
from app.services import tuning


def test_every_knob_reports_its_default_before_anyone_sets_one(fresh_db):
    rows = {t["name"]: t for t in tuning.list_tunables()}
    assert rows["chat_limit"]["value"] == ratelimit.LIMITS["chat"]
    assert rows["chat_limit"]["override"] is None
    assert all(r["floor"] < r["ceiling"] for r in rows.values())
    # every knob says whether a change takes effect now or at the next boot,
    # because the UI must not imply a restart-only change already applied
    assert {r["live"] for r in rows.values()} == {True, False}


def test_a_change_reaches_the_rate_limiter(fresh_db):
    """The whole point of the surface. Before the read-through, LIMITS was
    read once at import and an administrator's change reported 200 and
    enforced nothing."""
    ratelimit.reset()
    tuning.set_tunable("capture_limit", 2, actor="admin")
    for _ in range(2):
        ratelimit.check("capture", "ava")
    with pytest.raises(ratelimit.RateLimited, match="The limit for capture is 2"):
        ratelimit.check("capture", "ava")
    ratelimit.reset()


def test_clearing_returns_to_the_env_default_not_a_guess(fresh_db):
    original = ratelimit.LIMITS["capture"]
    tuning.set_tunable("capture_limit", 5, actor="admin")
    assert tuning.effective("capture_limit") == 5
    tuning.set_tunable("capture_limit", None, actor="admin")
    assert tuning.effective("capture_limit") == original
    assert tuning.override_of("capture_limit") is None


def test_a_change_reaches_the_flock_deadline(fresh_db):
    from app.routes import chat as chat_route

    assert chat_route._member_deadline() == chat_route.MEMBER_TIMEOUT_S
    tuning.set_tunable("member_timeout_s", 45, actor="admin")
    assert chat_route._member_deadline() == 45.0


def test_out_of_range_is_refused_with_the_bounds_named(fresh_db):
    with pytest.raises(ValueError, match="between 1 and 500"):
        tuning.set_tunable("chat_limit", 0, actor="admin")
    with pytest.raises(ValueError, match="between 1 and 500"):
        tuning.set_tunable("chat_limit", 10_000, actor="admin")
    # a zero cap refuses everyone and a 1-thread pool deadlocks the first
    # tool that waits on the pool, so the FLOOR is load-bearing, not decor
    with pytest.raises(ValueError):
        tuning.set_tunable("thread_pool", 1, actor="admin")


def test_the_socket_timeout_cannot_be_inverted_from_a_form(fresh_db):
    """tests/test_model_providers.py pins READ_TIMEOUT_S > MEMBER_TIMEOUT_S.
    Without a cross-knob check an administrator inverts it from the UI, and
    every cold model load starts dying as a failed member instead of
    finishing — the exact failure the ordering exists to prevent."""
    with pytest.raises(ValueError, match="must be greater than"):
        tuning.set_tunable("read_timeout_s", 60, actor="admin")
    # and from the other side: lowering the member deadline first is allowed,
    # then the socket timeout has room
    tuning.set_tunable("member_timeout_s", 30, actor="admin")
    tuning.set_tunable("read_timeout_s", 60, actor="admin")
    assert tuning.effective("read_timeout_s") == 60
    # raising the member deadline back through the socket timeout is refused
    with pytest.raises(ValueError, match="must be greater than"):
        tuning.set_tunable("member_timeout_s", 90, actor="admin")


def test_an_unknown_name_is_refused_without_echoing_it(fresh_db):
    with pytest.raises(ValueError) as exc:
        tuning.set_tunable("'; DROP TABLE app_settings--", 5, actor="admin")
    assert "DROP TABLE" not in str(exc.value)
    assert "chat_limit" in str(exc.value)  # names what IS accepted


def test_a_stored_value_outside_new_bounds_is_ignored_and_reported(fresh_db):
    """Bounds move between releases. Clamping would silently run at a number
    nobody chose; ignoring it runs the default and says so."""
    db.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
        (f"{tuning.PREFIX}chat_limit", "99999", db.now()),
    )
    assert tuning.effective("chat_limit") == ratelimit.LIMITS["chat"]
    row = next(t for t in tuning.list_tunables() if t["name"] == "chat_limit")
    assert row["ignored"] is True


def test_the_limiter_survives_a_database_that_cannot_answer(fresh_db, monkeypatch):
    """A settings lookup must never 500 a write. The cap degrades to the code
    default, which keeps the guard ON rather than off."""

    def boom(*a, **k):
        raise RuntimeError("no database")

    monkeypatch.setattr(tuning, "_overrides", boom)
    ratelimit.reset()
    ratelimit.check("capture", "ava")  # does not raise
    ratelimit.reset()


def test_every_write_lands_in_the_ledger(fresh_db):
    tuning.set_tunable("chat_limit", 25, actor="ava")
    row = db.query_row("SELECT actor, action, detail FROM activity ORDER BY id DESC")
    assert row["actor"] == "ava" and row["action"] == "set_tuning"
    assert "25" in row["detail"]


def test_identity_and_provider_knobs_are_absent_on_purpose(fresh_db):
    """Letting a web surface move these is privilege escalation with extra
    steps: an administrator who can lower the bar can let themselves through
    it. They stay env-only. This test is the record of that decision, so a
    later reader does not read the absence as an oversight."""
    names = {t.name for t in tuning.TUNABLES}
    for forbidden in ("auth_mode", "admins", "trust_proxy_hops", "model_provider", "api_key"):
        assert not any(forbidden in n for n in names)


def _key(name: str = "operator") -> dict:
    from app.services.api_keys import create_key

    return {"Authorization": f"Bearer {create_key(name, 'test')['key']}"}


def test_the_route_needs_strong_identity_not_just_a_header(client, fresh_db):
    """AdminUser is StrongUser + admin: a self-asserted X-User cannot reach
    it even when that name IS an administrator. These knobs are the
    deployment's capacity limits, so the bar is a personal key."""
    assert client.get("/api/settings/tuning", headers={"X-User": "boss"}).status_code == 403
    r = client.get("/api/settings/tuning", headers=_key())
    assert r.status_code == 200
    assert any(k["name"] == "chat_limit" for k in r.json())


def test_the_route_refuses_a_bad_value_as_4xx(client, fresh_db):
    r = client.post("/api/settings/tuning", json={"name": "chat_limit", "value": 0}, headers=_key())
    assert r.status_code == 400 and "between 1 and 500" in r.json()["detail"]
    # a mistyped field must 422 rather than fall through to the clear sentinel
    r = client.post(
        "/api/settings/tuning", json={"name": "chat_limit", "valu": 25}, headers=_key("op2")
    )
    assert r.status_code == 422
