"""Opaque browser credentials. Only hashes and sealed provider tokens reach storage."""

import hashlib
import hmac
import json
import math
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from jwt import ExpiredSignatureError

from .. import config, db, oidc
from ..public.errors import PublicError
from . import api_keys, credentials, oidc_identities, users

COOKIE_NAME = "__Host-skein-session"
SESSION_SECONDS = 8 * 60 * 60
MAX_SESSIONS_PER_USER = 12
REFRESH_LEASE_SECONDS = 30


class SessionInvalid(PublicError):
    def __init__(self) -> None:
        super().__init__(
            "SESSION_INVALID", "The browser session is not valid. Sign in again.", status_code=401
        )


class SessionChanged(PublicError):
    def __init__(self) -> None:
        super().__init__(
            "SESSION_CHANGED",
            "The browser session changed. Reload this page before you continue.",
            status_code=403,
        )


class SessionUnavailable(PublicError):
    def __init__(self, *, retry_after: int = 60, detail: str = "") -> None:
        super().__init__(
            "SESSION_UNAVAILABLE",
            detail or "The browser session is temporarily unavailable. Try again later.",
            status_code=503,
            retryable=True,
        )
        self.retry_after = retry_after


@dataclass(frozen=True)
class BrowserIdentity:
    user: str
    groups: tuple[str, ...]
    source: Literal["api-key", "oidc"]
    claims: dict | None = None


@dataclass(frozen=True)
class IssuedSession:
    cookie: str
    metadata: dict


def _hash(cookie: str) -> str:
    return hashlib.sha256(cookie.encode()).hexdigest()


def csrf_token(cookie: str) -> str:
    if not cookie or len(cookie) > 128:
        return ""
    return hashlib.sha256(b"skein-browser-csrf-v1\0" + cookie.encode()).hexdigest()


def validate_binding(cookie: str, header: str) -> bool:
    expected = csrf_token(cookie)
    return bool(expected and header and hmac.compare_digest(expected.encode(), header.encode()))


def _client_binding() -> str:
    # A deployment change must not send an old refresh credential to a new
    # endpoint or client. The sealing-key fingerprint also kills old sessions.
    # Auth mode is checked against the caller's effective settings in _row:
    # global AUTH_MODE can belong to a different composed application.
    return _hash(
        json.dumps(
            [
                config.OIDC_ISSUER,
                config.OIDC_CLIENT_ID,
                config.OIDC_AUDIENCE,
                config.OIDC_TOKEN_URL,
                config.OIDC_JWKS_URL,
                config.CREDENTIAL_KEY,
            ]
        )
    )


def _human(row: dict | None) -> dict:
    if not row or row["kind"] != "human" or not row["active"]:
        raise SessionInvalid()
    try:
        users.refuse_human_machine_claim(row["name"])
    except ValueError:
        raise SessionInvalid() from None
    return row


def _safe(cookie: str, human: dict | None = None, kind: str | None = None) -> dict:
    return {
        "authenticated": human is not None,
        "user": human["name"] if human else "anonymous",
        "strong": human is not None,
        "auth_method": kind if human else None,
        "csrf_token": csrf_token(cookie),
    }


def _row(cookie: str, mode: str) -> tuple[dict, dict]:
    if not cookie or len(cookie) > 128:
        raise SessionInvalid()
    row = db.query_one("SELECT * FROM browser_sessions WHERE token_hash = ?", (_hash(cookie),))
    if not row or row["mode"] != mode or row["expires_at"] <= db.now():
        raise SessionInvalid()
    human = _human(db.query_one("SELECT * FROM users WHERE id = ?", (row["user_id"],)))
    if row["kind"] == "api-key":
        if not db.query_one(
            "SELECT 1 FROM api_keys WHERE id = ? AND owner = ? AND active = 1",
            (row["key_id"], human["name"]),
        ):
            raise SessionInvalid()
    elif row["client_binding"] != _client_binding() or not db.query_one(
        "SELECT 1 FROM oidc_identities WHERE issuer = ? AND subject = ? AND user_id = ?",
        (row["issuer"], row["subject"], row["user_id"]),
    ):
        raise SessionInvalid()
    return row, human


def metadata(cookie: str, *, mode: str) -> dict:
    try:
        row, human = _row(cookie, mode)
    except SessionInvalid:
        return _safe(cookie)
    return _safe(cookie, human, row["kind"])


def prune_expired() -> int:
    return db.execute_rowcount("DELETE FROM browser_sessions WHERE expires_at <= ?", (db.now(),))


def _insert(
    human: dict,
    *,
    mode: str,
    kind: str,
    key_id: int | None = None,
    sealed: bytes | None = None,
    claims: dict | None = None,
) -> IssuedSession:
    cookie = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    db.execute(
        "INSERT INTO browser_sessions (token_hash, user_id, mode, kind, key_id, sealed_tokens,"
        " issuer, subject, client_binding, access_expires_at, expires_at, origin, created_by, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'human', ?, ?)",
        (
            _hash(cookie),
            human["id"],
            mode,
            kind,
            key_id,
            sealed,
            claims["iss"] if claims else "",
            claims["sub"] if claims else "",
            _client_binding() if claims else "",
            float(claims["exp"]) if claims else None,
            (now + timedelta(seconds=SESSION_SECONDS)).isoformat(timespec="seconds"),
            human["name"],
            now.isoformat(),
        ),
    )
    # The caller holds this owner's identity lock. Pruning only this owner
    # cannot deadlock against a different owner's logout or login.
    db.execute(
        "DELETE FROM browser_sessions WHERE token_hash IN ("
        " SELECT token_hash FROM browser_sessions WHERE user_id = ?"
        " ORDER BY created_at DESC, token_hash DESC OFFSET ?)",
        (human["id"], MAX_SESSIONS_PER_USER),
    )
    db.log_activity(human["name"], "create_browser_session", kind)
    return IssuedSession(cookie, _safe(cookie, human, kind))


def _lock_human(human: dict) -> dict:
    db.name_lock(db.LOCK_IDENTITY, users.fold(human["name"]))
    current = _human(db.query_one("SELECT * FROM users WHERE id = ?", (human["id"],)))
    # A rename can finish before the lock. Taking the new name now reverses
    # rename_user's sorted lock order, so retry rather than hold the wrong name.
    if current["name"] != human["name"]:
        raise SessionUnavailable(retry_after=1)
    return current


def create_key_session(key: str, *, mode: str) -> IssuedSession:
    if not key.startswith(api_keys.PREFIX):
        raise SessionInvalid()
    key_hash = _hash(key)
    key_row = db.query_one("SELECT * FROM api_keys WHERE key_hash = ? AND active = 1", (key_hash,))
    if key_row is None:
        raise SessionInvalid()
    try:
        human = _human(users.ensure_human_identity(key_row["owner"]))
    except ValueError:
        raise SessionInvalid() from None
    prune_expired()
    with db.transaction():
        human = _lock_human(human)
        current_key = db.query_one(
            "SELECT id FROM api_keys WHERE key_hash = ? AND owner = ? AND active = 1 FOR SHARE",
            (key_hash, human["name"]),
        )
        if current_key is None:
            raise SessionInvalid()
        return _insert(human, mode=mode, kind="api-key", key_id=current_key["id"])


def _claims(claims: dict, *, original: dict | None = None) -> tuple[str, tuple[str, ...]]:
    try:
        issuer, subject = oidc.identity(claims)
        name, groups = oidc.principal(claims)
        expiry = float(claims["exp"])
        if not math.isfinite(expiry) or expiry <= datetime.now(UTC).timestamp():
            raise SessionInvalid()
        for field in ("azp", "client_id"):
            if field in claims and claims[field] != config.OIDC_CLIENT_ID:
                raise SessionInvalid()
        if original and (issuer != original["issuer"] or subject != original["subject"]):
            raise SessionInvalid()
        return name, tuple(groups)
    except (oidc.OIDCError, KeyError, TypeError, ValueError):
        raise SessionInvalid() from None


def _tokens(tokens: dict, previous: dict | None = None) -> dict:
    access = tokens.get("access_token")
    refresh = tokens.get("refresh_token", (previous or {}).get("refresh_token", ""))
    if not isinstance(access, str) or not access or len(access) > 64 * 1024:
        raise SessionUnavailable()
    if not isinstance(refresh, str) or len(refresh) > 64 * 1024:
        raise SessionUnavailable()
    return {"access_token": access, "refresh_token": refresh}


def create_oidc_session(tokens: dict, claims: dict, *, mode: str) -> IssuedSession:
    if not credentials.available():
        raise SessionUnavailable(
            detail=(
                "Skein cannot store the browser session. Ask whoever runs the server"
                " to set a valid SKEIN_CREDENTIAL_KEY. Then sign in again."
            )
        )
    name, _ = _claims(claims)
    binding = _client_binding()
    sealed = credentials.seal(json.dumps(_tokens(tokens)))
    try:
        human = _human(oidc_identities.resolve(claims["iss"], claims["sub"], name))
    except ValueError:
        raise SessionInvalid() from None
    prune_expired()
    with db.transaction():
        human = _lock_human(human)
        if binding != _client_binding():
            raise SessionInvalid()
        _claims(claims)
        return _insert(human, mode=mode, kind="oidc", sealed=sealed, claims=claims)


def revoke(cookie: str) -> None:
    if not cookie or len(cookie) > 128:
        return
    with db.transaction():
        # No owner or provider lock: logout must work during provider failure.
        deleted = db.query_one(
            "DELETE FROM browser_sessions WHERE token_hash = ? RETURNING user_id, kind",
            (_hash(cookie),),
        )
        if deleted:
            human = db.query_one("SELECT name FROM users WHERE id = ?", (deleted["user_id"],))
            if human:
                db.log_activity(human["name"], "revoke_browser_session", deleted["kind"])


def revoke_all(cookie: str, *, mode: str) -> int:
    """Every browser session of this session's person, this one included:
    a stolen copy of this cookie is one of the sessions to end. API keys
    stay, because revoking them is its own action (api_keys.revoke_key).

    No identity lock, like revoke: a refresh racing this rewrites its own
    row by token_hash, and a deleted row matches nothing (_refresh)."""
    with db.transaction():
        row, human = _row(cookie, mode)
        n = db.execute_rowcount("DELETE FROM browser_sessions WHERE user_id = ?", (row["user_id"],))
        db.log_activity(human["name"], "revoke_all_browser_sessions", f"{n} session(s)")
    return n


def _unseal(row: dict) -> dict:
    try:
        tokens = json.loads(credentials.unseal(row["sealed_tokens"]))
        return {**_tokens(tokens), "pending_validation": tokens.get("pending_validation") is True}
    except (ValueError, TypeError, AttributeError):
        raise SessionInvalid() from None


def _finish_failure(row: dict, nonce: str, *, invalid: bool) -> None:
    if invalid:
        changed = db.execute_rowcount(
            "DELETE FROM browser_sessions WHERE token_hash = ? AND refresh_nonce = ?",
            (row["token_hash"], nonce),
        )
    else:
        changed = db.execute_rowcount(
            "UPDATE browser_sessions SET refresh_nonce = '', refresh_until = 0"
            " WHERE token_hash = ? AND refresh_nonce = ?",
            (row["token_hash"], nonce),
        )
    if not changed:
        # An older refused refresh cannot delete a later lease or overwrite its tokens.
        if not db.query_one(
            "SELECT 1 FROM browser_sessions WHERE token_hash = ?", (row["token_hash"],)
        ):
            raise SessionInvalid()
        raise SessionUnavailable(retry_after=1)


def _stage_refresh(cookie: str, row: dict, nonce: str, tokens: dict, *, mode: str) -> None:
    _row(cookie, mode)
    # The IdP can consume the old refresh token before a JWKS outage. Persist
    # its replacement before validation, but keep it off the authenticated path.
    sealed = credentials.seal(json.dumps({**tokens, "pending_validation": True}))
    staged = db.execute_rowcount(
        "UPDATE browser_sessions SET sealed_tokens = ?, access_expires_at = 0"
        " WHERE token_hash = ? AND refresh_nonce = ? AND refresh_until > ?"
        " AND expires_at > ? AND mode = ? AND client_binding = ?",
        (
            sealed,
            row["token_hash"],
            nonce,
            datetime.now(UTC).timestamp(),
            db.now(),
            mode,
            _client_binding(),
        ),
    )
    if not staged:
        raise SessionUnavailable(retry_after=1)


def _refresh(cookie: str, row: dict, *, mode: str) -> BrowserIdentity:
    nonce = secrets.token_hex(16)
    now = datetime.now(UTC).timestamp()
    claimed = db.execute_rowcount(
        "UPDATE browser_sessions SET refresh_nonce = ?, refresh_until = ?"
        " WHERE token_hash = ? AND sealed_tokens = ? AND refresh_until <= ? AND expires_at > ?",
        (
            nonce,
            now + REFRESH_LEASE_SECONDS,
            row["token_hash"],
            row["sealed_tokens"],
            now,
            db.now(),
        ),
    )
    if not claimed:
        raise SessionUnavailable(retry_after=1)
    try:
        tokens = _unseal(row)
        replacement = _tokens(tokens)
        claims = None
        if tokens["pending_validation"]:
            try:
                claims = oidc.validate(tokens["access_token"])
            except oidc.OIDCError as exc:
                if not isinstance(exc.__cause__, ExpiredSignatureError):
                    raise
            if claims is not None and float(claims["exp"]) <= datetime.now(UTC).timestamp():
                claims = None
        if claims is None:
            if not tokens["refresh_token"]:
                raise SessionInvalid()
            # A configuration change after lease acquisition must not redirect the
            # saved refresh credential. Completion repeats this check after network I/O.
            _row(cookie, mode)
            replacement = _tokens(
                oidc.exchange(
                    {
                        "grant_type": "refresh_token",
                        "refresh_token": tokens["refresh_token"],
                        "client_id": config.OIDC_CLIENT_ID,
                    }
                ),
                tokens,
            )
            _stage_refresh(cookie, row, nonce, replacement, mode=mode)
            claims = oidc.validate(replacement["access_token"])
        _claims(claims, original=row)
        groups = tuple(oidc.groups(claims, replacement["access_token"]))
        sealed = credentials.seal(json.dumps(replacement))
    except (oidc.OIDCUnavailable, oidc.OIDCProviderError, SessionUnavailable):
        _finish_failure(row, nonce, invalid=False)
        raise SessionUnavailable() from None
    except (oidc.OIDCError, SessionInvalid):
        _finish_failure(row, nonce, invalid=True)
        raise SessionInvalid() from None
    except ValueError:
        _finish_failure(row, nonce, invalid=False)
        raise SessionUnavailable() from None
    current, human = _row(cookie, mode)
    with db.transaction():
        _lock_human(human)
        current, human = _row(cookie, mode)
        # Logout and deactivation delete the row. CAS also rejects a replaced or
        # elapsed lease so a delayed response cannot restore older authority.
        if current["client_binding"] != row["client_binding"]:
            raise SessionInvalid()
        _claims(claims, original=row)
        published = db.execute_rowcount(
            "UPDATE browser_sessions SET sealed_tokens = ?, access_expires_at = ?,"
            " refresh_nonce = '', refresh_until = 0 WHERE token_hash = ?"
            " AND refresh_nonce = ? AND refresh_until > ? AND expires_at > ?",
            (
                sealed,
                float(claims["exp"]),
                row["token_hash"],
                nonce,
                datetime.now(UTC).timestamp(),
                db.now(),
            ),
        )
        if not published:
            raise SessionUnavailable(retry_after=1)
    return BrowserIdentity(human["name"], groups, "oidc", claims)


def authenticate(cookie: str, *, mode: str) -> BrowserIdentity:
    row, human = _row(cookie, mode)
    if row["kind"] == "api-key":
        return BrowserIdentity(human["name"], (), "api-key")
    # Route transaction dependencies must resolve identity first. Even a valid
    # access token can need a JWKS fetch, so no provider call can run under it.
    if db.in_transaction():
        raise SessionUnavailable(retry_after=1)
    if row["access_expires_at"] <= datetime.now(UTC).timestamp():
        return _refresh(cookie, row, mode=mode)
    try:
        access_token = _unseal(row)["access_token"]
        claims = oidc.validate(access_token)
        _claims(claims, original=row)
        groups = tuple(oidc.groups(claims, access_token))
    except (oidc.OIDCUnavailable, oidc.OIDCProviderError):
        raise SessionUnavailable() from None
    except oidc.OIDCError:
        # A token can expire while its signing keys are fetched. Refresh only
        # at its stored expiry, never to recover an invalid signature early.
        if row["access_expires_at"] <= datetime.now(UTC).timestamp():
            return _refresh(cookie, row, mode=mode)
        raise SessionInvalid() from None
    current, human = _row(cookie, mode)
    if current["sealed_tokens"] != row["sealed_tokens"]:
        raise SessionUnavailable(retry_after=1)
    _claims(claims, original=row)
    return BrowserIdentity(human["name"], groups, "oidc", claims)
