import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def sessions(fresh_db):
    from app.services import browser_sessions

    return browser_sessions


def _key_session(sessions, name="mira", mode="trusted-header"):
    from app.services import api_keys, users

    users.ensure_human_identity(name)
    key = api_keys.create_key(name)
    return sessions.create_key_session(key["key"], mode=mode), key


@pytest.fixture
def provider(monkeypatch):
    from app import config, oidc

    monkeypatch.setattr(config, "CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(config, "AUTH_MODE", "oidc")
    monkeypatch.setattr(config, "OIDC_ISSUER", "https://idp.example.test")
    monkeypatch.setattr(config, "OIDC_CLIENT_ID", "skein-browser")
    monkeypatch.setattr(config, "OIDC_AUDIENCE", "skein-api")
    monkeypatch.setattr(config, "OIDC_USERNAME_CLAIM", "preferred_username")
    monkeypatch.setattr(config, "OIDC_GROUPS_CLAIM", "groups")
    claims = {
        "iss": config.OIDC_ISSUER,
        "sub": "subject-mira",
        "aud": config.OIDC_AUDIENCE,
        "azp": config.OIDC_CLIENT_ID,
        "exp": datetime.now(UTC).timestamp() + 120,
        "preferred_username": "mira",
        "groups": ["operators"],
    }
    tokens = {"access_token": "initial-access-secret", "refresh_token": "refresh-secret"}
    verified = {tokens["access_token"]: claims}
    monkeypatch.setattr(oidc, "validate", lambda token: dict(verified[token]))
    monkeypatch.setattr(oidc, "exchange", lambda form: pytest.fail("unexpected provider refresh"))
    return tokens, claims, verified


def _expire_access(fresh_db):
    fresh_db.execute(
        "UPDATE browser_sessions SET access_expires_at = ?",
        (datetime.now(UTC).timestamp() - 1,),
    )


def test_key_session_stores_hash_and_key_id_only(sessions, fresh_db):
    issued, key = _key_session(sessions)
    row = fresh_db.query_row("SELECT * FROM browser_sessions")
    assert row["token_hash"] == hashlib.sha256(issued.cookie.encode()).hexdigest()
    assert row["key_id"] == key["id"]
    assert issued.cookie not in repr(row)
    assert key["key"] not in repr(row)
    assert row["sealed_tokens"] is None
    assert issued.metadata == {
        "authenticated": True,
        "user": "mira",
        "strong": True,
        "auth_method": "api-key",
        "csrf_token": sessions.csrf_token(issued.cookie),
    }
    identity = sessions.authenticate(issued.cookie, mode="trusted-header")
    assert (identity.user, identity.source, identity.groups) == ("mira", "api-key", ())
    assert sessions.validate_binding(issued.cookie, issued.metadata["csrf_token"])
    assert not sessions.validate_binding(issued.cookie, sessions.csrf_token("another-cookie"))
    assert not sessions.validate_binding("", "")
    assert (
        len(fresh_db.query("SELECT * FROM activity WHERE action = 'create_browser_session'")) == 1
    )


def test_key_revocation_mode_expiry_and_local_logout(sessions, fresh_db):
    from app.services import api_keys

    issued, key = _key_session(sessions)
    with pytest.raises(sessions.SessionInvalid):
        sessions.authenticate(issued.cookie, mode="oidc")
    api_keys.revoke_key(key["id"], "mira")
    assert not sessions.metadata(issued.cookie, mode="trusted-header")["authenticated"]
    with pytest.raises(sessions.SessionInvalid):
        sessions.authenticate(issued.cookie, mode="trusted-header")
    expired, _ = _key_session(sessions)
    fresh_db.execute("UPDATE browser_sessions SET expires_at = ?", ("2000-01-01T00:00:00+00:00",))
    meta = sessions.metadata(expired.cookie, mode="trusted-header")
    assert meta == {
        "authenticated": False,
        "user": "anonymous",
        "strong": False,
        "auth_method": None,
        "csrf_token": sessions.csrf_token(expired.cookie),
    }
    sessions.revoke(expired.cookie)
    sessions.revoke(expired.cookie)
    sessions.revoke(issued.cookie)
    assert fresh_db.query("SELECT * FROM browser_sessions") == []


@pytest.mark.parametrize("name", ["anonymous", "system", "scout", "inactive", "ambiguous"])
def test_key_creation_refuses_nonhuman_and_ambiguous_roster(sessions, fresh_db, name):
    from app.services import api_keys, users

    if name == "scout":
        users.ensure_agent_identity(name)
    elif name in {"inactive", "ambiguous"}:
        users.ensure_human_identity(name)
        if name == "ambiguous":
            fresh_db.execute(
                "INSERT INTO users (name, kind, created_at) VALUES (?, 'human', ?)",
                (name.upper(), fresh_db.now()),
            )
    key = api_keys.create_key(name)
    if name == "inactive":
        # api_keys.create_key refuses an inactive account, so the key exists
        # first and deactivation follows
        users.set_active(name, False)
    with pytest.raises(sessions.SessionInvalid):
        sessions.create_key_session(key["key"], mode="trusted-header")
    assert fresh_db.query("SELECT * FROM browser_sessions") == []


def test_deactivation_and_merge_delete_sessions_but_rename_follows_id(sessions, fresh_db):
    from app.services import users

    issued, _ = _key_session(sessions)
    users.rename_user("mira", "renamed", actor="mira")
    assert sessions.authenticate(issued.cookie, mode="trusted-header").user == "renamed"
    users.ensure_human_identity("destination")
    users.rename_user("renamed", "destination", actor="ops")
    assert not sessions.metadata(issued.cookie, mode="trusted-header")["authenticated"]
    issued, _ = _key_session(sessions, name="destination")
    users.set_active("destination", False)
    users.set_active("destination", True)
    assert fresh_db.query("SELECT * FROM browser_sessions") == []
    assert not sessions.metadata(issued.cookie, mode="trusted-header")["authenticated"]


def test_oidc_storage_is_sealed_and_metadata_and_logout_are_local(
    sessions, provider, monkeypatch, fresh_db
):
    from app import oidc
    from app.services import credentials

    tokens, claims, _ = provider
    issued = sessions.create_oidc_session(tokens, claims, mode="oidc")
    row = fresh_db.query_row("SELECT * FROM browser_sessions")
    assert tokens["access_token"] not in repr(row)
    assert tokens["refresh_token"] not in repr(row)
    # no id_token in this provider's answer: stored empty, sign-out stays local
    assert json.loads(credentials.unseal(row["sealed_tokens"])) == {**tokens, "id_token": ""}
    assert (datetime.fromisoformat(row["expires_at"]) - datetime.now(UTC)).total_seconds() > 28_790
    monkeypatch.setattr(oidc, "validate", lambda token: pytest.fail("metadata contacted provider"))
    monkeypatch.setattr(credentials, "unseal", lambda blob: pytest.fail("logout decrypted tokens"))
    assert sessions.metadata(issued.cookie, mode="oidc")["authenticated"]
    sessions.revoke(issued.cookie)
    assert fresh_db.query("SELECT * FROM browser_sessions") == []


@pytest.mark.parametrize("credential_key", ["", "invalid"])
def test_missing_sealing_key_blocks_oidc_only(sessions, provider, monkeypatch, credential_key):
    from app import config

    tokens, claims, _ = provider
    monkeypatch.setattr(config, "CREDENTIAL_KEY", credential_key)
    key_session, _ = _key_session(sessions)
    assert sessions.authenticate(key_session.cookie, mode="trusted-header").user == "mira"
    with pytest.raises(sessions.SessionUnavailable) as caught:
        sessions.create_oidc_session(tokens, claims, mode="oidc")
    assert "add the server" not in str(caught.value)


def test_refresh_preserves_omitted_refresh_token_and_replaces_groups(
    sessions, provider, monkeypatch, fresh_db
):
    from app import oidc
    from app.services import credentials

    tokens, claims, verified = provider
    issued = sessions.create_oidc_session(tokens, claims, mode="oidc")
    _expire_access(fresh_db)
    replacement = {**claims, "groups": ["readers"], "exp": claims["exp"] + 60}
    verified["replacement-access"] = replacement

    def exchange(form):
        assert not fresh_db.in_transaction()
        assert form == {
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": "skein-browser",
        }
        return {"access_token": "replacement-access"}

    monkeypatch.setattr(oidc, "exchange", exchange)
    identity = sessions.authenticate(issued.cookie, mode="oidc")
    assert identity.groups == ("readers",)
    row = fresh_db.query_row("SELECT * FROM browser_sessions")
    assert (
        json.loads(credentials.unseal(row["sealed_tokens"]))["refresh_token"]
        == tokens["refresh_token"]
    )
    assert row["refresh_nonce"] == ""


def test_local_refresh_sealing_failure_preserves_rotation_and_retries(
    sessions, provider, monkeypatch, fresh_db
):
    from app import oidc
    from app.services import credentials

    tokens, claims, verified = provider
    issued = sessions.create_oidc_session(tokens, claims, mode="oidc")
    _expire_access(fresh_db)
    verified["candidate-access"] = {**claims, "groups": ["readers"]}
    exchanges = []

    def exchange(form):
        exchanges.append(form["refresh_token"])
        return {"access_token": "candidate-access", "refresh_token": "rotated-refresh"}

    seal = credentials.seal

    def fail_publication(value):
        if not json.loads(value).get("pending_validation"):
            raise ValueError("local sealing secret")
        return seal(value)

    monkeypatch.setattr(oidc, "exchange", exchange)
    monkeypatch.setattr(credentials, "seal", fail_publication)
    with pytest.raises(sessions.SessionUnavailable) as caught:
        sessions.authenticate(issued.cookie, mode="oidc")
    assert "local sealing secret" not in str(caught.value)
    pending = fresh_db.query_one("SELECT * FROM browser_sessions")
    assert pending is not None
    assert pending["refresh_nonce"] == ""
    assert pending["refresh_until"] == 0
    assert pending["access_expires_at"] <= datetime.now(UTC).timestamp()
    assert json.loads(credentials.unseal(pending["sealed_tokens"])) == {
        "access_token": "candidate-access",
        "refresh_token": "rotated-refresh",
        "id_token": "",
        "pending_validation": True,
    }
    assert sessions.metadata(issued.cookie, mode="oidc")["authenticated"]

    monkeypatch.setattr(credentials, "seal", seal)
    assert sessions.authenticate(issued.cookie, mode="oidc").groups == ("readers",)
    recovered = fresh_db.query_row("SELECT * FROM browser_sessions")
    assert recovered["refresh_nonce"] == ""
    assert recovered["refresh_until"] == 0
    assert recovered["access_expires_at"] == verified["candidate-access"]["exp"]
    assert json.loads(credentials.unseal(recovered["sealed_tokens"])) == {
        "access_token": "candidate-access",
        "refresh_token": "rotated-refresh",
        "id_token": "",
    }
    assert exchanges == [tokens["refresh_token"]]


@pytest.mark.parametrize("candidate", ["valid", "expired", "wrong-subject"])
def test_rotated_refresh_survives_transient_signing_key_failure(
    sessions, provider, monkeypatch, fresh_db, candidate
):
    from jwt import ExpiredSignatureError

    from app import oidc
    from app.services import credentials

    tokens, claims, _ = provider
    issued = sessions.create_oidc_session(tokens, claims, mode="oidc")
    _expire_access(fresh_db)
    exchanges = []
    validations = []

    def exchange(form):
        exchanges.append(form["refresh_token"])
        if len(exchanges) == 1:
            return {"access_token": "candidate-access", "refresh_token": "rotated-refresh"}
        if form["refresh_token"] != "rotated-refresh":
            raise oidc.OIDCRefused("The refresh credential was already consumed.")
        return {"access_token": "fresh-access", "refresh_token": "fresh-refresh"}

    def validate(token):
        validations.append(token)
        if len(validations) == 1:
            raise oidc.OIDCUnavailable("The signing keys are temporarily unavailable.")
        if token == "candidate-access" and candidate == "expired":
            raise oidc.OIDCError("The access token expired.") from ExpiredSignatureError()
        return {
            **claims,
            "groups": ["readers"],
            "sub": "different-subject" if candidate == "wrong-subject" else claims["sub"],
        }

    monkeypatch.setattr(oidc, "exchange", exchange)
    monkeypatch.setattr(oidc, "validate", validate)
    with pytest.raises(sessions.SessionUnavailable):
        sessions.authenticate(issued.cookie, mode="oidc")
    pending = fresh_db.query_row("SELECT * FROM browser_sessions")
    assert (
        json.loads(credentials.unseal(pending["sealed_tokens"]))["refresh_token"]
        == "rotated-refresh"
    )
    assert pending["access_expires_at"] <= datetime.now(UTC).timestamp()
    assert "rotated-refresh" not in repr(pending)
    if candidate == "wrong-subject":
        with pytest.raises(sessions.SessionInvalid):
            sessions.authenticate(issued.cookie, mode="oidc")
        assert fresh_db.query("SELECT * FROM browser_sessions") == []
    else:
        assert sessions.authenticate(issued.cookie, mode="oidc").groups == ("readers",)
    assert validations[:2] == ["candidate-access", "candidate-access"]
    assert exchanges == [tokens["refresh_token"]] + (
        ["rotated-refresh"] if candidate == "expired" else []
    )


def test_rotated_refresh_survives_a_publication_lock_failure(
    sessions, provider, monkeypatch, fresh_db
):
    from psycopg.errors import LockNotAvailable

    from app import oidc
    from app.services import credentials

    tokens, claims, verified = provider
    issued = sessions.create_oidc_session(tokens, claims, mode="oidc")
    _expire_access(fresh_db)
    verified["candidate-access"] = {**claims, "groups": ["readers"]}
    exchanges = []

    def exchange(form):
        exchanges.append(form["refresh_token"])
        return {"access_token": "candidate-access", "refresh_token": "rotated-refresh"}

    lock_human = sessions._lock_human

    def unavailable(_human):
        raise LockNotAvailable("The identity write lock is held.")

    monkeypatch.setattr(oidc, "exchange", exchange)
    monkeypatch.setattr(sessions, "_lock_human", unavailable)
    with pytest.raises(LockNotAvailable):
        sessions.authenticate(issued.cookie, mode="oidc")
    row = fresh_db.query_row("SELECT * FROM browser_sessions")
    assert (
        json.loads(credentials.unseal(row["sealed_tokens"]))["refresh_token"] == "rotated-refresh"
    )
    fresh_db.execute("UPDATE browser_sessions SET refresh_until = 0")
    monkeypatch.setattr(sessions, "_lock_human", lock_human)
    assert sessions.authenticate(issued.cookie, mode="oidc").groups == ("readers",)
    assert exchanges == [tokens["refresh_token"]]


@pytest.mark.parametrize("race", ["logout", "newer-lease"])
def test_stale_pending_validation_cannot_delete_newer_authority(
    sessions, provider, monkeypatch, fresh_db, race
):
    from app import oidc

    tokens, claims, _ = provider
    issued = sessions.create_oidc_session(tokens, claims, mode="oidc")
    _expire_access(fresh_db)
    entered, release = threading.Event(), threading.Event()
    validations = []
    exchanges = []

    def exchange(form):
        exchanges.append(form["refresh_token"])
        return {"access_token": "candidate-access", "refresh_token": "rotated-refresh"}

    def validate(token):
        validations.append(token)
        if len(validations) == 1:
            raise oidc.OIDCUnavailable("The signing keys are temporarily unavailable.")
        if len(validations) == 2:
            entered.set()
            assert release.wait(5)
            raise oidc.OIDCError("The earlier validation failed.")
        return {**claims, "groups": ["readers"]}

    monkeypatch.setattr(oidc, "exchange", exchange)
    monkeypatch.setattr(oidc, "validate", validate)
    with pytest.raises(sessions.SessionUnavailable):
        sessions.authenticate(issued.cookie, mode="oidc")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(sessions.authenticate, issued.cookie, mode="oidc")
        assert entered.wait(5)
        try:
            with pytest.raises(sessions.SessionUnavailable):
                sessions.authenticate(issued.cookie, mode="oidc")
            if race == "logout":
                sessions.revoke(issued.cookie)
            else:
                fresh_db.execute("UPDATE browser_sessions SET refresh_until = 0")
                assert sessions.authenticate(issued.cookie, mode="oidc").groups == ("readers",)
        finally:
            release.set()
        error = sessions.SessionInvalid if race == "logout" else sessions.SessionUnavailable
        with pytest.raises(error):
            pending.result(timeout=5)
    assert sessions.metadata(issued.cookie, mode="oidc")["authenticated"] == (race == "newer-lease")
    assert exchanges == [tokens["refresh_token"]]


@pytest.mark.parametrize("fault", ["unavailable", "unusable", "refused", "subject", "client"])
def test_refresh_faults_preserve_only_retryable_sessions(
    sessions, provider, monkeypatch, fresh_db, fault
):
    from app import oidc

    tokens, claims, verified = provider
    issued = sessions.create_oidc_session(tokens, claims, mode="oidc")
    _expire_access(fresh_db)

    def exchange(form):
        if fault == "unavailable":
            raise oidc.OIDCUnavailable("raw upstream secret")
        if fault == "unusable":
            raise oidc.OIDCProviderError("raw upstream secret")
        if fault == "refused":
            raise oidc.OIDCRefused("raw upstream secret")
        verified["replacement"] = {**claims, "sub" if fault == "subject" else "azp": "other"}
        return {"access_token": "replacement"}

    monkeypatch.setattr(oidc, "exchange", exchange)
    error = (
        sessions.SessionUnavailable
        if fault in {"unavailable", "unusable"}
        else sessions.SessionInvalid
    )
    with pytest.raises(error) as caught:
        sessions.authenticate(issued.cookie, mode="oidc")
    assert "raw upstream secret" not in str(caught.value)
    assert bool(fresh_db.query("SELECT * FROM browser_sessions")) == (
        error is sessions.SessionUnavailable
    )


@pytest.mark.parametrize("change", ["logout", "deactivate", "key", "client", "mode", "expiry"])
def test_inflight_refresh_cannot_restore_changed_authority(
    sessions, provider, monkeypatch, fresh_db, change
):
    from app import config, oidc
    from app.services import users

    tokens, claims, verified = provider
    issued = sessions.create_oidc_session(tokens, claims, mode="oidc")
    _expire_access(fresh_db)
    entered, release = threading.Event(), threading.Event()
    verified["replacement"] = {**claims, "exp": claims["exp"] + 60}

    def exchange(form):
        entered.set()
        assert release.wait(5)
        return {"access_token": "replacement"}

    monkeypatch.setattr(oidc, "exchange", exchange)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(sessions.authenticate, issued.cookie, mode="oidc")
        assert entered.wait(5)
        try:
            if change == "logout":
                sessions.revoke(issued.cookie)
            elif change == "deactivate":
                users.set_active("mira", False)
            elif change == "key":
                monkeypatch.setattr(config, "CREDENTIAL_KEY", Fernet.generate_key().decode())
            elif change == "client":
                monkeypatch.setattr(config, "OIDC_CLIENT_ID", "another-client")
            elif change == "mode":
                monkeypatch.setattr(config, "AUTH_MODE", "api-key")
            else:
                fresh_db.execute(
                    "UPDATE browser_sessions SET expires_at = '2000-01-01T00:00:00+00:00'"
                )
        finally:
            release.set()
        if change == "mode":
            assert pending.result(timeout=5).user == "mira"
            with pytest.raises(sessions.SessionInvalid):
                sessions.authenticate(issued.cookie, mode="api-key")
        else:
            with pytest.raises(sessions.SessionInvalid):
                pending.result(timeout=5)


def test_refresh_single_flight_and_stale_failure_cannot_delete_new_lease(
    sessions, provider, monkeypatch, fresh_db
):
    from app import oidc

    tokens, claims, _ = provider
    issued = sessions.create_oidc_session(tokens, claims, mode="oidc")
    _expire_access(fresh_db)
    entered, release = threading.Event(), threading.Event()

    def exchange(form):
        entered.set()
        assert release.wait(5)
        raise oidc.OIDCRefused("old refresh was refused")

    monkeypatch.setattr(oidc, "exchange", exchange)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(sessions.authenticate, issued.cookie, mode="oidc")
        assert entered.wait(5)
        try:
            with pytest.raises(sessions.SessionUnavailable) as caught:
                sessions.authenticate(issued.cookie, mode="oidc")
            assert caught.value.retry_after == 1
            fresh_db.execute("UPDATE browser_sessions SET refresh_nonce = 'new-lease'")
        finally:
            release.set()
        with pytest.raises(sessions.SessionUnavailable):
            pending.result(timeout=5)
    assert (
        fresh_db.query_row("SELECT refresh_nonce FROM browser_sessions")["refresh_nonce"]
        == "new-lease"
    )


def test_refresh_rechecks_access_expiry_after_publication_lock(
    sessions, provider, monkeypatch, fresh_db
):
    from app import oidc

    tokens, claims, verified = provider
    issued = sessions.create_oidc_session(tokens, claims, mode="oidc")
    _expire_access(fresh_db)
    verified["replacement"] = {**claims, "exp": datetime.now(UTC).timestamp() + 1}
    monkeypatch.setattr(oidc, "exchange", lambda form: {"access_token": "replacement"})
    real_lock = fresh_db.name_lock

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(UTC) + timedelta(seconds=2)

    def lock(namespace, name):
        real_lock(namespace, name)
        monkeypatch.setattr(sessions, "datetime", Later)

    monkeypatch.setattr(fresh_db, "name_lock", lock)
    with pytest.raises(sessions.SessionInvalid):
        sessions.authenticate(issued.cookie, mode="oidc")


def test_oidc_authentication_never_fetches_inside_a_transaction(sessions, provider, fresh_db):
    tokens, claims, _ = provider
    issued = sessions.create_oidc_session(tokens, claims, mode="oidc")
    with fresh_db.transaction(), pytest.raises(sessions.SessionUnavailable):
        sessions.authenticate(issued.cookie, mode="oidc")


@pytest.mark.parametrize("source", ["api-key", "oidc"])
def test_creation_retries_when_the_owner_renames_before_the_lock(
    sessions, provider, monkeypatch, fresh_db, source
):
    from app.services import api_keys, users

    tokens, claims, _ = provider
    key = api_keys.create_key("mira")["key"]
    prune = sessions.prune_expired

    def rename_before_lock():
        users.rename_user("mira", "renamed", actor="mira")
        return prune()

    monkeypatch.setattr(sessions, "prune_expired", rename_before_lock)
    with pytest.raises(sessions.SessionUnavailable):
        if source == "api-key":
            sessions.create_key_session(key, mode="oidc")
        else:
            sessions.create_oidc_session(tokens, claims, mode="oidc")
    assert fresh_db.query("SELECT * FROM browser_sessions") == []
    assert users.is_human("renamed")


def test_session_limit_prunes_oldest_and_expired(sessions, fresh_db, monkeypatch):
    monkeypatch.setattr(sessions, "MAX_SESSIONS_PER_USER", 2)
    oldest, _ = _key_session(sessions)
    middle, _ = _key_session(sessions)
    newest, _ = _key_session(sessions)
    assert not sessions.metadata(oldest.cookie, mode="trusted-header")["authenticated"]
    assert sessions.metadata(middle.cookie, mode="trusted-header")["authenticated"]
    assert sessions.metadata(newest.cookie, mode="trusted-header")["authenticated"]
    assert len(fresh_db.query("SELECT * FROM browser_sessions")) == 2
    fresh_db.execute(
        "UPDATE browser_sessions SET expires_at = ?",
        ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(),),
    )
    assert sessions.prune_expired() == 2


def test_a_refresh_keeps_the_id_token_from_sign_in(sessions):
    """A refresh response can omit id_token, and the provider session it
    names is still this one. Dropped, sign-out at the provider stopped
    working after the first refresh."""
    stored = sessions._tokens(
        {"access_token": "a2", "refresh_token": "r2"},
        {"access_token": "a1", "refresh_token": "r1", "id_token": "id-from-sign-in"},
    )
    assert stored["id_token"] == "id-from-sign-in"
