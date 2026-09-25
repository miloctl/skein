"""Per-teammate API keys for the CLI, MCP server, git hooks, and scripts.
Format: sk-skein-<40 hex>. Only the SHA-256 hash is stored; the full key is
shown exactly once at creation."""

import hashlib
import re
import secrets
import shlex
from datetime import UTC, datetime

from .. import db

PREFIX = "sk-skein-"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_key(owner: str, label: str = "", *, at_server: bool = False) -> dict:
    from .users import fold, is_active

    key = PREFIX + secrets.token_hex(20)
    with db.transaction():
        # users.set_active revokes every key under this lock. Without it, a
        # mint that passed the route's active check before a deactivation
        # committed inserts a live key after the revocation, and a later
        # reactivation brings that key back.
        db.name_lock(db.LOCK_IDENTITY, fold(owner))
        if not is_active(owner):
            raise ValueError("The account is not active. An API key cannot be created for it.")
        kid = db.execute(
            "INSERT INTO api_keys (key_hash, prefix, owner, label, created_at)"
            " VALUES (?, ?, ?, ?, ?) RETURNING id",
            (_hash(key), key[: len(PREFIX) + 6], owner, label, db.now()),
        )
        # Under the owner's name, so it shows in their feed and nobody else's.
        # `at_server` says the operator minted it (app.bootstrap_key): logged
        # as theirs alone, a key someone else issued read as one they made.
        where = " (minted at the server)" if at_server else ""
        db.log_activity(owner, "create_api_key", f"#{kid} {label}{where}")
    return {"id": kid, "key": key, "label": label, "note": "store this now — it is not shown again"}


# "@" and "+" for sign-in names, which are often email addresses. After the
# first character both are literal in bash, sh, zsh and PowerShell, and
# shlex.quote leaves them bare. The first character is a letter, digit or _:
# PowerShell expands a leading "@" (it turns "@true" into "True").
_SAFE_NAME = re.compile(r"\w[\w .@+\-]{0,63}")


def request_key(user: str, *, strong: bool = False) -> dict:
    """Self-serve ask: a key can only be minted at the server, but requesting
    one must not require finding the operator — this files a nudge with the
    exact command to the named administrators (SKEIN_ADMINS), or to the team
    when none are named: who asked for a key is nobody else's business.
    Idempotent per requester while one is still
    unread. The name is validated and quoted because the message is designed
    to be copy-pasted into a root shell — the one place spoofable X-User text
    must never smuggle shell metacharacters."""
    if not user or user == "anonymous":
        # the same words as Settings shows for this condition
        raise ValueError(
            "No name is picked, and a key is minted for one name. Pick your name"
            " under Identity in Settings, then request a key."
        )
    if not _SAFE_NAME.fullmatch(user):
        raise ValueError(
            "This name cannot go in the server command that mints a key. The name"
            " must start with a letter, a digit, or _ and use only letters, digits,"
            " spaces, and . - _ @ +. Ask whoever runs the server to mint the key."
        )
    prefix = f"{user} requests a personal API key"
    check = (
        "a key or a sign-in proved the name"
        if strong
        else "self-asserted name — check that the request really comes from them"
    )
    message = (
        f"{prefix} ({check}, then deliver the key out-of-band)"
        f" — mint: python -m app.bootstrap_key {shlex.quote(user)}"
    )
    from .. import config

    # the roster's spelling: SKEIN_ADMINS=Casey names roster `casey`
    # (deps.is_named_admin folds), and a notice to the literal spelling
    # reaches nobody
    # a deactivated administrator, or a name a rename freed, reads nothing,
    # so a notice to them reaches nobody while the requester reads that
    # whoever runs the server has it. A name with no roster row yet stays:
    # that person reads it on sign-in if they sign in with the SKEIN_ADMINS
    # spelling (notifications match the name exactly, and there is no roster
    # row yet to fold against). The rule routes/deps.py::administrator_possible
    # applies.
    from .users import fold, is_claimable, list_users

    roster = {fold(u["name"]): u["name"] for u in list_users()}
    recipients = sorted(
        {roster.get(fold(name), name) for name in config.ADMINS if is_claimable(name)}
    ) or ["team"]
    marks = ", ".join("?" for _ in recipients)
    with db.transaction():
        db.name_lock(db.LOCK_KEY_REQUEST, user)
        # "Unread by ANYONE", not notifications.UNREAD_FOR. This nudge asks a
        # question about the world — has whoever runs the server minted the key
        # — so one operator dismissing it means the ask was seen and the
        # requester may ask again. The per-person read (009) governs whose FEED
        # shows it; this governs whether a second request is a duplicate.
        # starts_with, not LIKE: "_" is a wildcard there, so "a_b" would
        # match an earlier request from "axb" and its own request would never
        # be sent
        pending = db.query_one(
            f'SELECT id FROM notifications WHERE "user" IN ({marks}) AND starts_with(message, ?)'  # noqa: S608 — marks built above
            " AND read_at IS NULL"
            " AND id NOT IN (SELECT notification_id FROM notification_reads)",
            (*recipients, prefix),
        )
        # to_team: no administrator is named, so every teammate got the
        # nudge, and the confirmation says so rather than "whoever runs it"
        to_team = recipients == ["team"]
        if pending:
            return {"requested": True, "already_pending": True, "to_team": to_team}
        from . import notifications

        for recipient in recipients:
            notifications.notify(recipient, message, tier="immediate", link="/settings")
        db.log_activity(user, "request_key", "asked for a personal API key")
    return {"requested": True, "already_pending": False, "to_team": to_team}


def verify_key(key: str) -> str | None:
    """Return the owning user for a valid active key, else None."""
    if not key.startswith(PREFIX):
        return None
    row = db.query_one(
        "SELECT id, owner, last_used_at FROM api_keys WHERE key_hash = ? AND active = 1",
        (_hash(key),),
    )
    if not row:
        return None
    # last_used_at is display telemetry (the key list's "last used" column) —
    # stamped per call, every keyed request pays a write-lock acquisition
    # for it. Under 60 seconds since the stored stamp, skip the write.
    # A negative age (a clock step wrote a future stamp) rewrites too, else
    # the stamp freezes until the wall clock catches up.
    last = row["last_used_at"]
    try:
        age = (datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds()
        fresh = 0 <= age < 60
    except (TypeError, ValueError):
        fresh = False
    if not fresh:
        db.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (db.now(), row["id"]))
    return row["owner"]


# ACTIVE first in both lists below, so the cap can only drop rows that are
# already revoked until a caller passes LIST_LIMIT *live* keys — past that the
# oldest live one falls off, which is exactly the long-lived key an
# administrator hunting a spoofed mint is looking for. Neither list is a count:
# use active_key_count for that.
LIST_LIMIT = 200


def list_keys(owner: str) -> list[dict]:
    return db.query(
        "SELECT id, prefix, label, active, created_at, last_used_at"
        " FROM api_keys WHERE owner = ? ORDER BY active DESC, id DESC LIMIT ?",
        (owner, LIST_LIMIT),
    )


def active_key_count(owner: str) -> int:
    """Counted in SQL, never by measuring a capped page: a number computed over
    a truncated list under-reports without saying so."""
    row = db.query_one(
        "SELECT COUNT(*) AS n FROM api_keys WHERE owner = ? AND active = 1", (owner,)
    )
    return int(row["n"]) if row else 0


def revoke_key(key_id: int, owner: str) -> dict:
    n = db.execute_rowcount(
        "UPDATE api_keys SET active = 0 WHERE id = ? AND owner = ?", (key_id, owner)
    )
    if not n:
        raise db.NotFound(f"key #{key_id} not found (or not yours)")
    # a revoked key is a suspect one, and a merge request is one call it can
    # have made (services/merges.py)
    from .merges import cancel_for

    cancel_for(owner)
    db.log_activity(owner, "revoke_api_key", f"#{key_id}")
    return {"id": key_id, "active": False}


def list_all_keys() -> list[dict]:
    """Team-wide key visibility for admins (the route is AdminUser — one
    teammate must not enumerate another's credentials). Makes hidden keys
    minted under a spoofed identity discoverable and revocable — up to
    LIST_LIMIT. The constant records what falls off the end, and it is the
    long-lived key such a hunt is looking for."""
    return db.query(
        "SELECT id, prefix, owner, label, active, created_at, last_used_at"
        " FROM api_keys ORDER BY active DESC, id DESC LIMIT ?",
        (LIST_LIMIT,),
    )


def revoke_all_keys(*, actor: str) -> dict:
    """Kill switch: revoke every active key (e.g. after rotating the shared
    token, so a leaked token can't have left durable access behind)."""
    n = db.execute_rowcount("UPDATE api_keys SET active = 0 WHERE active = 1")
    # every credential is suspect: a request filed with one must not be
    # confirmable after the kill switch (services/merges.py)
    from .merges import cancel_for

    cancel_for()
    db.log_activity(actor, "revoke_all_api_keys", f"{n} keys")
    # every key holder just lost access; the ledger row reaches the actor's
    # feed alone
    from .notifications import notify

    notify("team", f"{actor} revoked every API key. Ask for a new key.", tier="immediate")
    return {"revoked": n}


def revoke_keys_for(owner: str, *, actor: str = "system") -> int:
    """Revoke every active key an owner holds — the offboarding half of
    users.set_active(False)."""
    n = db.execute_rowcount(
        "UPDATE api_keys SET active = 0 WHERE owner = ? AND active = 1", (owner,)
    )
    # deactivation and every merge land here: a pending merge request that
    # names this account is cancelled with its credentials
    from .merges import cancel_for

    cancel_for(owner)
    if n:
        db.log_activity(actor, "revoke_api_keys_for", f"{owner}: {n} key(s)")
    return n
