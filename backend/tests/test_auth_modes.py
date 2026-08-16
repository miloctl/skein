"""SKEIN_AUTH_MODE: which identity doors exist, and who administers.

Every other test file runs in trusted-header mode (the default) and pins that
behavior; this file pins the api-key and oidc modes, the fail-closed handling
of a broken auth config, and the StrongUser/AdminUser split.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from threading import Event, Thread, current_thread
from time import monotonic, sleep

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def _boot_config(**env):
    """config.py values as a FRESH process reads them. conftest imports config
    once at session start, so monkeypatching an attribute never exercises the
    parse — and the parse is what refuses a typo'd mode."""
    code = (
        "import json; from app import config; print(json.dumps({"
        "'mode': config.AUTH_MODE, 'error': config.AUTH_ERROR,"
        "'admins': sorted(config.ADMINS)}))"
    )
    out = subprocess.run(  # noqa: S603 — fixed argv, this interpreter, literal source
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=BACKEND,
        env={**os.environ, "SKEIN_SCHEDULER": "0", **env},
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _key(owner="tester"):
    from app.services.api_keys import create_key

    return {"Authorization": f"Bearer {create_key(owner, 'test')['key']}"}


def _oidc(monkeypatch, tokens):
    from app import config, oidc

    monkeypatch.setattr(config, "AUTH_MODE", "oidc")

    def fake_validate(token):
        if token in tokens:
            return tokens[token]
        raise oidc.OIDCError("the sign-in token was refused (Fake). Sign in again.")

    monkeypatch.setattr(oidc, "validate", fake_validate)


def test_config_refuses_an_unknown_mode_at_boot():
    # the parse itself, not a monkeypatched AUTH_ERROR: this is the code that
    # stands between a typo and a silently open deployment
    bad = _boot_config(SKEIN_AUTH_MODE="oidk")
    assert bad["error"]
    assert "SKEIN_AUTH_MODE" in bad["error"]
    assert "oidk" not in bad["error"]  # the 503 body never echoes the value
    for mode in ("trusted-header", "api-key"):
        assert _boot_config(SKEIN_AUTH_MODE=mode)["error"] == ""


def test_config_requires_issuer_and_audience_for_oidc():
    assert "SKEIN_OIDC_ISSUER" in _boot_config(SKEIN_AUTH_MODE="oidc")["error"]
    only_issuer = _boot_config(SKEIN_AUTH_MODE="oidc", SKEIN_OIDC_ISSUER="https://idp.test")
    assert "SKEIN_OIDC_AUDIENCE" in only_issuer["error"]
    full = _boot_config(
        SKEIN_AUTH_MODE="oidc",
        SKEIN_OIDC_ISSUER="https://idp.test",
        SKEIN_OIDC_AUDIENCE="skein",
    )
    assert full["error"] == ""


def test_config_normalizes_mode_and_admin_names():
    assert _boot_config(SKEIN_AUTH_MODE="  API-KEY  ")["mode"] == "api-key"
    assert _boot_config(SKEIN_ADMINS=" ada , grace ,, ")["admins"] == ["ada", "grace"]


def test_broken_auth_config_fails_closed(client, monkeypatch):
    from app import config

    headers = _key()
    monkeypatch.setattr(config, "AUTH_ERROR", "unknown SKEIN_AUTH_MODE 'oidk'")
    # everything is refused — even a valid key. A typo'd mode gets fixed,
    # never guessed around.
    assert client.get("/api/tasks").status_code == 503
    assert client.get("/api/tasks", headers=headers).status_code == 503
    h = client.get("/health")
    assert h.status_code == 200
    assert "SKEIN_AUTH_MODE" in h.json()["auth_error"]


def test_health_reports_auth_mode(client):
    h = client.get("/health").json()
    assert h["auth_mode"] == "trusted-header"
    assert h["auth_error"] == ""


def test_full_health_needs_a_credential_in_api_key_mode(client, monkeypatch):
    """The probe endpoint stays open; the topology endpoint closes.

    /api/health carries provider, model, job schedule and chain state — on a
    public route those must cost a credential in the modes that exist because
    the header is self-asserted. /health stays answerable for the container
    checks that can send nothing."""
    from app import config

    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    assert client.get("/health").status_code == 200
    assert client.get("/api/health").status_code == 401
    assert client.get("/api/health", headers=_key()).status_code == 200


def test_api_key_mode_never_trusts_the_header(client, monkeypatch):
    from app import config

    headers = _key()
    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    # the client fixture sends X-User: tester on every request — not enough
    assert client.get("/api/tasks").status_code == 401
    r = client.post("/api/capture", json={"text": "todo: x"}, headers={"X-User": "mallory"})
    assert r.status_code == 401
    assert client.get("/api/tasks", headers=headers).status_code == 200
    # a presented-but-bogus key keeps its specific refusal
    r = client.get("/api/tasks", headers={"Authorization": "Bearer sk-skein-bogus"})
    assert r.status_code == 401
    assert "invalid or revoked" in r.json()["detail"]


def test_api_key_mode_refuses_the_header_at_the_route_layer_too(monkeypatch, fresh_db):
    # the middleware is the perimeter; the dependency must hold on its own
    from fastapi import HTTPException

    from app import config
    from app.routes.deps import _resolve

    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    with pytest.raises(HTTPException) as e:
        _resolve("tester", "", "GET")
    assert e.value.status_code == 401


def test_deps_refuse_a_broken_config_on_their_own(monkeypatch, fresh_db):
    # twin of the api-key test below: the middleware is the perimeter, but
    # every route dependency must hold even if a path joins open_paths
    from fastapi import HTTPException

    from app import config
    from app.routes.deps import _resolve

    monkeypatch.setattr(config, "AUTH_ERROR", "SKEIN_AUTH_MODE is not a known mode.")
    with pytest.raises(HTTPException) as e:
        _resolve("tester", "", "GET")
    assert e.value.status_code == 503


def test_a_key_wins_in_oidc_mode_both_ways(client, monkeypatch, fresh_db):
    # rule 1: a personal key authenticates in EVERY mode, or every CLI, MCP
    # and hook automation breaks the day a team turns on oidc
    headers = _key("automation")
    _oidc(monkeypatch, {"good": {"preferred_username": "casey"}})
    assert client.get("/api/tasks", headers=headers).status_code == 200
    # a bogus key is refused AS A KEY — never handed to the token validator
    r = client.get("/api/tasks", headers={"Authorization": "Bearer sk-skein-nope"})
    assert r.status_code == 401
    assert "invalid or revoked" in r.json()["detail"]
    # and it is strong identity, so self-scoped surfaces open
    assert client.post("/api/keys", json={"label": "x"}, headers=headers).status_code == 200


def test_an_agent_key_is_refused_at_the_perimeter(client, monkeypatch):
    """The catalog reads that resolve no caller have the middleware as their
    only gate in the locked modes, so the agent wall has to live there too.
    Asserted on /api/playbooks: every content read gained a CurrentUser
    (tests/test_route_identity.py), and against one of those the dependency
    would refuse the key too — the perimeter could be deleted and this test
    would stay green."""
    from app import config
    from app.services.api_keys import create_key
    from app.services.users import ensure_user

    ensure_user("scout", kind="agent")
    hdr = {"Authorization": f"Bearer {create_key('scout', 'k')['key']}"}
    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    r = client.get("/api/playbooks", headers=hdr)  # resolves no caller of its own
    assert r.status_code == 403
    assert "agent identity" in r.json()["detail"]


def test_a_credential_is_verified_once_per_request(client, monkeypatch):
    """The middleware and the dependency both used to verify. verify_key
    writes last_used_at on every call, so a double check cost two SQLite
    writes per request."""
    from app import config
    from app.routes import deps

    headers = _key("counted")
    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    calls = []
    real = deps.verify_key
    monkeypatch.setattr(deps, "verify_key", lambda k: (calls.append(k), real(k))[1])
    # /api/keys carries CurrentUser, so both layers run for this request
    assert client.get("/api/keys", headers=headers).status_code == 200
    assert calls == []  # deps reused what the middleware already proved


def test_an_oidc_sign_in_naming_an_agent_is_refused_at_the_perimeter(client, monkeypatch, fresh_db):
    """The twin of the API-key wall above, for the other locked mode. A token
    whose username claim names an agent row reaches the same catalog reads,
    so the wall has to stand in both branches of the middleware. Same reason
    as above for asserting on a route that resolves no caller."""
    from app.services.users import ensure_user

    ensure_user("scout", kind="agent")
    _oidc(monkeypatch, {"tok": {"preferred_username": "scout"}})
    r = client.get("/api/playbooks", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 403
    assert "agent identity" in r.json()["detail"]


def test_an_oidc_sign_in_cannot_claim_the_synthetic_anonymous_subject(
    client, monkeypatch, fresh_db
):
    from fastapi import HTTPException

    from app.routes.deps import _resolve

    _oidc(monkeypatch, {"tok": {"preferred_username": "anonymous"}})
    catalog = client.get("/api/playbooks", headers={"Authorization": "Bearer tok"})
    assert catalog.status_code == 403
    assert "reserved for the system" in catalog.json()["detail"]
    private = client.post(
        "/api/private/notes",
        json={"person": "mira", "body": "must not land"},
        headers={"Authorization": "Bearer tok"},
    )
    assert private.status_code == 403
    with pytest.raises(HTTPException) as raised:
        _resolve("", "Bearer tok", "POST")
    assert raised.value.status_code == 403
    assert fresh_db.query_one("SELECT 1 FROM users WHERE name = 'anonymous'") is None


def test_a_legacy_anonymous_api_key_cannot_become_strong_identity(client, monkeypatch, fresh_db):
    from app import config
    from app.services import users
    from app.services.api_keys import create_key

    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    users.ensure_user("anonymous")
    token = create_key("anonymous", "legacy")["key"]
    response = client.get(
        "/api/private/notes",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "reserved for the system" in response.json()["detail"]


def test_absent_weak_header_keeps_the_synthetic_compatibility_subject(fresh_db):
    from app.routes.deps import _resolve

    assert _resolve("", "", "POST") == ("anonymous", False, [])
    assert fresh_db.query_one("SELECT name, kind FROM users WHERE name = 'anonymous'") == {
        "name": "anonymous",
        "kind": "human",
    }


@pytest.mark.parametrize(
    "claim",
    [
        "ALICE",  # case
        # the confusables are the test: each one folds onto "alice" and would
        # resolve as her without the guard. noqa, or the linter deletes the case.
        "ａlice",  # noqa: RUF001 — NFKC fullwidth
        "al‍ice",
    ],
)
def test_an_oidc_claim_folding_onto_a_roster_name_is_refused_on_reads(
    client, monkeypatch, fresh_db, claim
):
    """The read door skips ensure_user, so it must take the fold wall itself.
    Resolving the claim onto the roster row would hand one IdP principal every
    row another person owns — private notes included — and _is_admin reads the
    resolved name, so it escalates to the admin surfaces too."""
    from app.services.users import ensure_user

    ensure_user("alice")
    _oidc(monkeypatch, {"tok": {"preferred_username": claim}})
    r = client.get("/api/private/notes", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 403
    assert "one name must mean one identity" in r.json()["detail"]
    # the write door already refused this claim; the two must agree, or one
    # credential is "not this person" for writes and "is this person" for reads
    w = client.post(
        "/api/lessons",
        json={"text": "x"},
        headers={"Authorization": "Bearer tok"},
    )
    assert w.status_code == 403


def test_an_oidc_claim_folding_onto_an_admin_name_does_not_reach_admin_surfaces(
    client, monkeypatch, fresh_db
):
    """_is_admin matches case-insensitively by design, so the fold wall on the
    read door is the only thing between a lookalike claim and a full export."""
    from app import config
    from app.services.users import ensure_user

    ensure_user("casey")
    monkeypatch.setattr(config, "ADMINS", ["casey"])
    _oidc(monkeypatch, {"tok": {"preferred_username": "ｃasey"}})  # noqa: RUF001 — fullwidth c
    r = client.get("/api/admin/export", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 403


def test_an_oidc_first_sign_in_reserves_human_ownership(client, monkeypatch, fresh_db):
    """A validated read owns its name before a machine can reserve it."""
    _oidc(monkeypatch, {"tok": {"preferred_username": "newcomer"}})
    assert client.get("/api/tasks", headers={"Authorization": "Bearer tok"}).status_code == 200
    assert fresh_db.query_one("SELECT kind FROM users WHERE name = 'newcomer'") == {"kind": "human"}


def test_established_oidc_read_is_not_blocked_by_a_concurrent_writer(client, monkeypatch, fresh_db):
    """The steady-state OIDC path is a reader, and a reader waits for nobody.

    Under SQLite this was a claim about WAL versus the single global writer.
    Under MVCC it is a claim about row locks: another transaction holding the
    caller's roster row must not delay a read that only needs to see it."""
    from app import db
    from app.services.users import ensure_human_identity

    ensure_human_identity("established")
    _oidc(monkeypatch, {"tok": {"preferred_username": "established"}})
    holding = Event()
    release = Event()

    def hold_writer() -> None:
        with db.transaction():
            db.query("SELECT name FROM users WHERE name = ? FOR UPDATE", ("established",))
            holding.set()
            release.wait(timeout=5)

    holder = Thread(target=hold_writer)
    holder.start()
    try:
        assert holding.wait(timeout=2)
        # TIMED. Without the bound this passes even when the read blocks: the
        # holder releases after 5s and the request then succeeds, so the
        # assertion below cannot tell "never waited" from "waited five
        # seconds". The bound is what makes it about concurrency.
        started = monotonic()
        response = client.get("/api/tasks", headers={"Authorization": "Bearer tok"})
        waited = monotonic() - started
        assert response.status_code == 200
        assert waited < 1, f"the read waited {waited:.1f}s for a concurrent writer"
    finally:
        release.set()
        holder.join(timeout=3)


def test_first_oidc_read_returns_retryable_503_when_identity_storage_is_busy(
    client, monkeypatch, fresh_db
):
    """A first ownership claim reports load instead of an opaque 500.

    The claim INSERTs a roster row. A concurrent uncommitted INSERT of the
    same name holds the unique index entry, so the second one waits — and with
    lock_timeout set it raises LockNotAvailable, which db.BUSY_ERRORS classes
    as load. Without that classification the caller gets a 500 telling it not
    to retry, which is the opposite of the truth."""
    from app import db

    _oidc(monkeypatch, {"tok": {"preferred_username": "first-reader"}})
    dbname = db.query_row("SELECT current_database() AS d")["d"]
    # on the DATABASE, so every connection the pool opens next inherits it —
    # the request runs on a connection this test never touches
    db.execute(f"ALTER DATABASE \"{dbname}\" SET lock_timeout = '100ms'")
    db.close_pool()
    holding = Event()
    release = Event()

    def hold_writer() -> None:
        with db.transaction():
            db.execute(
                "INSERT INTO users (name, kind, created_at) VALUES (?, 'human', ?)",
                ("first-reader", db.now()),
            )
            holding.set()
            release.wait(timeout=5)
            raise RuntimeError("roll back the holder")

    holder = Thread(target=hold_writer, daemon=True)
    holder.start()
    try:
        assert holding.wait(timeout=2)
        response = client.get("/api/tasks", headers={"Authorization": "Bearer tok"})
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "5"
        assert response.json() == {
            "detail": "The database is busy. Wait 5 seconds, then send the request again."
        }
    finally:
        release.set()
        holder.join(timeout=3)
        db.execute(f'ALTER DATABASE "{dbname}" RESET lock_timeout')
        db.close_pool()


@pytest.mark.parametrize("oidc_name", ["race-owner", "RACE-OWNER"])
def test_oidc_read_is_refused_when_machine_reservation_wins(fresh_db, monkeypatch, oidc_name):
    """A strong read cannot outlive an exact or folded machine reservation."""
    from fastapi import HTTPException

    from app.routes.deps import _resolve
    from app.services import users

    _oidc(monkeypatch, {"tok": {"preferred_username": oidc_name}})
    machine_checked = Event()
    oidc_attempted = Event()
    oidc_finished = Event()
    original_refuse_fold_collision = users.refuse_fold_collision
    results: dict[str, dict] = {}
    errors: dict[str, BaseException] = {}

    def pause_machine_after_collision_check(name: str, *, ignore: str = "") -> None:
        original_refuse_fold_collision(name, ignore=ignore)
        if current_thread().name == "machine-reservation":
            machine_checked.set()
            assert oidc_attempted.wait(timeout=2)
            sleep(0.05)
            assert not oidc_finished.is_set()

    monkeypatch.setattr(users, "refuse_fold_collision", pause_machine_after_collision_check)

    def reserve_machine() -> None:
        try:
            results["machine"] = users.ensure_agent_identity("race-owner")
        except BaseException as exc:  # pragma: no cover - reported by the assertions below
            errors["machine"] = exc

    def authenticate_oidc() -> None:
        try:
            assert machine_checked.wait(timeout=2)
            oidc_attempted.set()
            _resolve("", "Bearer tok", "GET")
        except HTTPException as exc:
            errors["oidc"] = exc
        except BaseException as exc:  # pragma: no cover - reported by the assertions below
            errors["unexpected"] = exc
        finally:
            oidc_finished.set()

    machine = Thread(target=reserve_machine, name="machine-reservation")
    oidc = Thread(target=authenticate_oidc, name="oidc-read")
    machine.start()
    oidc.start()
    machine.join(timeout=3)
    oidc.join(timeout=3)

    assert not machine.is_alive() and not oidc.is_alive()
    assert set(errors) == {"oidc"}
    assert getattr(errors["oidc"], "status_code", None) == 403
    assert results["machine"]["kind"] == "agent"
    rows = [
        row
        for row in fresh_db.query("SELECT name, kind FROM users")
        if users.fold(row["name"]) == "race-owner"
    ]
    assert rows == [{"name": "race-owner", "kind": "agent"}]


def test_oidc_read_reservation_blocks_a_concurrent_folded_machine_claim(fresh_db, monkeypatch):
    """The OIDC winner keeps one human row and the machine claim fails."""
    from app.routes.deps import _resolve
    from app.services import users

    _oidc(monkeypatch, {"tok": {"preferred_username": "RACE-OWNER"}})
    oidc_checked = Event()
    machine_attempted = Event()
    machine_finished = Event()
    original_refuse_fold_collision = users.refuse_fold_collision
    results: dict[str, tuple[str, bool, list[str]]] = {}
    errors: dict[str, BaseException] = {}

    def pause_oidc_after_collision_check(name: str, *, ignore: str = "") -> None:
        original_refuse_fold_collision(name, ignore=ignore)
        if current_thread().name == "oidc-read":
            oidc_checked.set()
            assert machine_attempted.wait(timeout=2)
            sleep(0.05)
            assert not machine_finished.is_set()

    monkeypatch.setattr(users, "refuse_fold_collision", pause_oidc_after_collision_check)

    def authenticate_oidc() -> None:
        try:
            results["oidc"] = _resolve("", "Bearer tok", "GET")
        except BaseException as exc:  # pragma: no cover - reported by the assertions below
            errors["oidc"] = exc

    def claim_machine() -> None:
        try:
            assert oidc_checked.wait(timeout=2)
            machine_attempted.set()
            users.ensure_agent_identity("race-owner")
        except ValueError as exc:
            errors["machine"] = exc
        except BaseException as exc:  # pragma: no cover - reported by the assertions below
            errors["unexpected"] = exc
        finally:
            machine_finished.set()

    oidc = Thread(target=authenticate_oidc, name="oidc-read")
    machine = Thread(target=claim_machine, name="machine-claim")
    oidc.start()
    machine.start()
    oidc.join(timeout=3)
    machine.join(timeout=3)

    assert not oidc.is_alive() and not machine.is_alive()
    assert set(errors) == {"machine"}
    assert results["oidc"] == ("RACE-OWNER", True, [])
    rows = [
        row
        for row in fresh_db.query("SELECT name, kind FROM users")
        if users.fold(row["name"]) == "race-owner"
    ]
    assert rows == [{"name": "RACE-OWNER", "kind": "human"}]


def test_deactivation_closes_every_door_not_only_the_key(client, monkeypatch, fresh_db):
    """set_active calls itself the offboarding switch for strong identity. It
    revoked API keys and nothing else, so an offboarded teammate kept strong
    read AND write through the OIDC door, and full access through the header
    door, until someone separately disabled the IdP account."""
    from app.services.users import ensure_user, set_active

    ensure_user("bob")
    _oidc(monkeypatch, {"tok": {"preferred_username": "bob"}})
    assert client.get("/api/tasks", headers={"Authorization": "Bearer tok"}).status_code == 200

    set_active("bob", False, actor="admin")

    refused = client.get("/api/tasks", headers={"Authorization": "Bearer tok"})
    assert refused.status_code == 403
    assert "not active" in refused.json()["detail"]
    assert "bob" not in refused.json()["detail"]  # never echoes the identity back
    # writes too, not only reads
    assert (
        client.post(
            "/api/lessons", json={"text": "x"}, headers={"Authorization": "Bearer tok"}
        ).status_code
        == 403
    )
    # and reactivation reopens it, or the switch would be one-way
    set_active("bob", True, actor="admin")
    assert client.get("/api/tasks", headers={"Authorization": "Bearer tok"}).status_code == 200


def test_deactivation_closes_the_header_door(client, fresh_db):
    """trusted-header is the dev default, and a bare X-User is a full identity
    there — deactivation has to mean something on this door too.

    Asserted on a route that RESOLVES a user. In this mode the perimeter
    short-circuits, and a caller refused under one name reaches every read
    by picking another — a documented property of a mode whose whole premise
    is a trusted network, not a gap this check introduces.
    """
    from app.services.users import ensure_user, set_active

    ensure_user("carol")
    assert client.get("/api/whoami", headers={"X-User": "carol"}).status_code == 200
    set_active("carol", False, actor="admin")
    assert client.get("/api/whoami", headers={"X-User": "carol"}).status_code == 403
    # a case variant must not walk past the check the exact name fails
    assert client.get("/api/whoami", headers={"X-User": "CAROL"}).status_code == 403
    # somebody with no roster row at all is not "inactive" — first sign-in works
    assert client.get("/api/whoami", headers={"X-User": "newcomer"}).status_code == 200


def test_an_oidc_token_is_validated_once_per_request(client, monkeypatch, fresh_db):
    """The twin of the key test above. A signature check is the expensive half
    of oidc mode, and the dependency must reuse what the middleware proved."""
    from app import oidc

    _oidc(monkeypatch, {"tok": {"preferred_username": "casey"}})
    calls = []
    real = oidc.validate
    monkeypatch.setattr(oidc, "validate", lambda t: (calls.append(t), real(t))[1])
    # /api/keys carries a user dependency, so both layers run for this request
    assert client.get("/api/keys", headers={"Authorization": "Bearer tok"}).status_code == 200
    assert len(calls) == 1


def test_an_unreachable_identity_provider_is_a_503_not_a_sign_in_again(
    client, monkeypatch, fresh_db
):
    """During an IdP outage, 401 sends every signed-in person to a sign-in that
    is also down. 503 says the truth: the token was never judged."""
    from app import config, oidc

    monkeypatch.setattr(config, "AUTH_MODE", "oidc")

    def down(token):
        raise oidc.OIDCUnavailable("the identity provider cannot be reached.")

    monkeypatch.setattr(oidc, "validate", down)
    r = client.get("/api/tasks", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 503
    assert "cannot be reached" in r.json()["detail"]


def test_slack_endpoint_keeps_its_own_gate(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    # Slack verifies its own signature — unconfigured is a 404, never a 401
    assert client.post("/api/slack/command").status_code == 404


def test_calendar_feed_fails_closed_outside_trusted_header_mode(client, monkeypatch):
    from app import config

    assert client.get("/api/calendar.ics").status_code == 200
    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    assert client.get("/api/calendar.ics").status_code == 403
    monkeypatch.setattr(config, "ICS_TOKEN", "feed-secret")
    assert client.get("/api/calendar.ics?token=feed-secret").status_code == 200
    assert client.get("/api/calendar.ics?token=wrong").status_code == 401


def test_a_shared_token_shaped_like_a_key_still_works(client, monkeypatch):
    # an operator whose SKEIN_API_TOKEN happens to start with the key prefix
    # must not be locked out of their own deployment
    from app import config

    monkeypatch.setattr(config, "API_TOKEN", "sk-skein-operator-chose-this")
    assert client.get("/api/tasks").status_code == 401
    ok = client.get("/api/tasks", headers={"Authorization": f"Bearer {config.API_TOKEN}"})
    assert ok.status_code == 200


def test_oidc_mode_sign_in_is_strong_identity(client, monkeypatch, fresh_db):
    _oidc(monkeypatch, {"good": {"preferred_username": "casey", "groups": ["eng"]}})
    hdr = {"Authorization": "Bearer good"}
    assert client.get("/api/tasks").status_code == 401
    assert client.get("/api/tasks", headers={"Authorization": "Bearer bad"}).status_code == 401
    assert (
        client.post("/api/capture", json={"text": "todo: from sso"}, headers=hdr).status_code == 200
    )
    tasks = client.get("/api/tasks", headers=hdr).json()
    assert tasks[0]["created_by"] == "casey"
    # a validated sign-in is STRONG: private records and feedback need no browser key
    note = client.post(
        "/api/private/notes",
        json={"person": "casey", "body": "private from sso", "kind": "note"},
        headers=hdr,
    )
    assert note.status_code == 200
    feedback = client.post("/api/capture", json={"text": "fb: casey — clear feedback"}, headers=hdr)
    assert feedback.status_code == 200 and feedback.json()["kind"] == "feedback"
    # minting a first key for the CLI also needs no prior key
    assert client.post("/api/keys", json={"label": "cli"}, headers=hdr).status_code == 200


def test_oidc_reads_and_writes_reserve_human_roster_rows(client, monkeypatch, fresh_db):
    def roster(name):
        return fresh_db.query_one("SELECT * FROM users WHERE name = ?", (name,))

    _oidc(
        monkeypatch,
        {"w": {"preferred_username": "writer"}, "r": {"preferred_username": "reader"}},
    )
    client.get("/api/notifications", headers={"Authorization": "Bearer r"})
    assert roster("reader")["kind"] == "human"
    client.post("/api/capture", json={"text": "todo: x"}, headers={"Authorization": "Bearer w"})
    row = roster("writer")
    assert row is not None and row["kind"] == "human"


def test_oidc_username_colliding_with_a_reserved_name_says_what_to_change(
    client, monkeypatch, fresh_db
):
    """ensure_user refuses bench-persona slugs. An OIDC caller cannot pick a
    different name the way the picker can, so a bare 400 would be a permanent
    lockout with no stated cause."""
    from app.services import personas

    slug = sorted(personas.bench_slugs())[0]
    _oidc(monkeypatch, {"tok": {"preferred_username": slug}})
    r = client.post(
        "/api/capture", json={"text": "todo: x"}, headers={"Authorization": "Bearer tok"}
    )
    assert r.status_code == 403
    assert "SKEIN_OIDC_USERNAME_CLAIM" in r.json()["detail"]


def test_oidc_admin_by_name_not_only_by_group(client, monkeypatch, fresh_db):
    from app import config

    _oidc(monkeypatch, {"tok": {"preferred_username": "Casey"}})
    monkeypatch.setattr(config, "ADMINS", frozenset({"casey"}))
    # names match the way the roster matches them — case must not lock an admin out
    assert client.get("/api/admin/keys", headers={"Authorization": "Bearer tok"}).status_code == 200


def test_oidc_sign_in_cannot_claim_an_agent_identity(client, monkeypatch, fresh_db):
    from app.services.users import ensure_user

    ensure_user("scout", kind="agent")
    _oidc(monkeypatch, {"tok": {"preferred_username": "scout"}})
    r = client.post(
        "/api/capture", json={"text": "todo: x"}, headers={"Authorization": "Bearer tok"}
    )
    assert r.status_code == 403
    assert "agent identity" in r.json()["detail"]


def test_admin_surfaces_stay_open_to_key_holders_by_default(client):
    # trusted-header + empty SKEIN_ADMINS = the historical scarcity model,
    # where holding a hand-minted key IS the admin bar
    assert client.get("/api/admin/keys", headers=_key()).status_code == 200


def test_admins_list_closes_admin_surfaces_to_other_key_holders(client, monkeypatch):
    from app import config

    other = _key("pat")
    monkeypatch.setattr(config, "ADMINS", frozenset({"root"}))
    r = client.get("/api/admin/keys", headers=other)
    assert r.status_code == 403
    assert "not an administrator" in r.json()["detail"]
    # self-scoped strong surfaces stay open: pat still mints their own keys
    assert client.post("/api/keys", json={"label": "cli"}, headers=other).status_code == 200
    assert client.get("/api/admin/keys", headers=_key("root")).status_code == 200


def test_api_key_mode_locks_admin_surfaces_until_admins_is_set(client, monkeypatch):
    from app import config

    hdr = _key("pat")
    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    who = client.get("/api/whoami", headers=hdr).json()
    assert who["admin"] is False
    assert who["can_administer"] is False
    assert client.get("/api/admin/keys", headers=hdr).status_code == 403
    monkeypatch.setattr(config, "ADMINS", frozenset({"pat"}))
    who = client.get("/api/whoami", headers=hdr).json()
    assert who["admin"] is True
    assert who["can_administer"] is True
    assert client.get("/api/admin/keys", headers=hdr).status_code == 200


def test_oidc_admin_group_grants_admin(client, monkeypatch, fresh_db):
    from app import config

    _oidc(
        monkeypatch,
        {
            "lead": {"preferred_username": "lead", "groups": ["skein-admins"]},
            "dev": {"preferred_username": "dev", "groups": ["eng"]},
        },
    )
    monkeypatch.setattr(config, "OIDC_ADMIN_GROUP", "skein-admins")
    assert client.get("/api/admin/keys", headers={"Authorization": "Bearer dev"}).status_code == 403
    assert (
        client.get("/api/admin/keys", headers={"Authorization": "Bearer lead"}).status_code == 200
    )


def test_every_door_stashes_the_group_claims(client, monkeypatch, fresh_db):
    """A route that re-derives admin-ness in its own body reads these off
    request.state. Stashed by one dependency and not the others, whether an
    OIDC administrator is refused depends on which one the route picked."""
    from app import config
    from app.services.users import ensure_user

    _oidc(monkeypatch, {"tok": {"preferred_username": "casey", "groups": ["skein-admins"]}})
    monkeypatch.setattr(config, "OIDC_ADMIN_GROUP", "skein-admins")
    ensure_user("casey")
    hdr = {"Authorization": "Bearer tok"}

    # CurrentUser (whoami), StrongUser (keys), AdminUser (admin keys) — all
    # three must agree that this caller is an administrator
    who = client.get("/api/whoami", headers=hdr).json()
    assert who["admin"] is True
    assert who["can_administer"] is True
    assert client.get("/api/keys", headers=hdr).status_code == 200
    assert client.get("/api/admin/keys", headers=hdr).status_code == 200
