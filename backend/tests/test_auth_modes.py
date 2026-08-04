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
    """41 GET routes carry no user dependency, so in the locked modes the
    middleware is their only gate. The agent wall has to live there too."""
    from app import config
    from app.services.api_keys import create_key
    from app.services.users import ensure_user

    ensure_user("scout", kind="agent")
    hdr = {"Authorization": f"Bearer {create_key('scout', 'k')['key']}"}
    monkeypatch.setattr(config, "AUTH_MODE", "api-key")
    r = client.get("/api/tasks", headers=hdr)  # no CurrentUser dependency
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
    whose username claim names an agent row reaches the same 41 dependency-less
    GET routes, so the wall has to stand in both branches of the middleware."""
    from app.services.users import ensure_user

    ensure_user("scout", kind="agent")
    _oidc(monkeypatch, {"tok": {"preferred_username": "scout"}})
    r = client.get("/api/tasks", headers={"Authorization": "Bearer tok"})
    assert r.status_code == 403
    assert "agent identity" in r.json()["detail"]


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


def test_an_oidc_first_sign_in_still_reads_before_any_roster_row_exists(
    client, monkeypatch, fresh_db
):
    """The fold wall must not close the first-ever read: a name that collides
    with nobody is not a collision, and a read never grows the roster."""
    _oidc(monkeypatch, {"tok": {"preferred_username": "newcomer"}})
    assert client.get("/api/tasks", headers={"Authorization": "Bearer tok"}).status_code == 200
    assert fresh_db.query_one("SELECT * FROM users WHERE name = 'newcomer'") is None


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
    short-circuits and the ~45 dependency-less reads (GET /api/tasks among
    them) carry no identity check at all — a documented property of a mode
    whose whole premise is a trusted network, not a gap this check introduces.
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
    # a validated sign-in is STRONG: minting a first key needs no prior key
    assert client.post("/api/keys", json={"label": "cli"}, headers=hdr).status_code == 200


def test_oidc_write_mints_the_roster_row_but_a_read_does_not(client, monkeypatch, fresh_db):
    def roster(name):
        return fresh_db.query_one("SELECT * FROM users WHERE name = ?", (name,))

    _oidc(
        monkeypatch,
        {"w": {"preferred_username": "writer"}, "r": {"preferred_username": "reader"}},
    )
    client.get("/api/notifications", headers={"Authorization": "Bearer r"})
    # same rule as the name picker: a polling service account never grows the roster
    assert roster("reader") is None
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
    assert client.get("/api/admin/keys", headers=hdr).status_code == 403
    monkeypatch.setattr(config, "ADMINS", frozenset({"pat"}))
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
