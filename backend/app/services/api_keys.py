"""Per-teammate API keys for the CLI, MCP server, git hooks, and scripts.
Format: sk-skein-<40 hex>. Only the SHA-256 hash is stored; the full key is
shown exactly once at creation."""

import hashlib
import re
import secrets
import shlex
from datetime import datetime, timezone

from .. import db

PREFIX = "sk-skein-"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_key(owner: str, label: str = "") -> dict:
    key = PREFIX + secrets.token_hex(20)
    kid = db.execute(
        "INSERT INTO api_keys (key_hash, prefix, owner, label, created_at) VALUES (?, ?, ?, ?, ?)",
        (_hash(key), key[: len(PREFIX) + 6], owner, label, db.now()),
    )
    db.log_activity(owner, "create_api_key", f"#{kid} {label}")
    return {"id": kid, "key": key, "label": label, "note": "store this now — it is not shown again"}


_SAFE_NAME = re.compile(r"[\w .\-]{1,64}")


def request_key(user: str) -> dict:
    """Self-serve ask: a key can only be minted at the server, but requesting
    one shouldn't require finding the operator — this files a team-visible
    nudge with the exact command. Idempotent per requester while one is still
    unread. The name is validated and quoted because the message is designed
    to be copy-pasted into a root shell — the one place spoofable X-User text
    must never smuggle shell metacharacters."""
    if not user or user == "anonymous":
        raise ValueError("pick your name first — the key is minted for it")
    if not _SAFE_NAME.fullmatch(user):
        raise ValueError("that name cannot go in a mint command — letters, digits, . - _ only")
    prefix = f"{user} requests a personal API key"
    message = (
        f"{prefix} (self-asserted name — check that the request really comes from"
        f" them, then deliver the key out-of-band)"
        f" — mint: python -m app.bootstrap_key {shlex.quote(user)}"
    )
    with db.transaction():
        pending = db.query_one(
            "SELECT id FROM notifications WHERE user = 'team' AND message LIKE ?"
            " AND read_at IS NULL",
            (prefix + "%",),
        )
        if pending:
            return {"requested": True, "already_pending": True}
        from . import notifications

        notifications.notify("team", message, tier="immediate", link="/settings")
        db.log_activity(user, "request_key", "asked for a personal API key")
    return {"requested": True, "already_pending": False}


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
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds()
        fresh = 0 <= age < 60
    except (TypeError, ValueError):
        fresh = False
    if not fresh:
        db.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (db.now(), row["id"]))
    return row["owner"]


def list_keys(owner: str) -> list[dict]:
    return db.query(
        "SELECT id, prefix, label, active, created_at, last_used_at"
        " FROM api_keys WHERE owner = ? ORDER BY id DESC",
        (owner,),
    )


def revoke_key(key_id: int, owner: str) -> dict:
    n = db.execute_rowcount(
        "UPDATE api_keys SET active = 0 WHERE id = ? AND owner = ?", (key_id, owner)
    )
    if not n:
        raise db.NotFound(f"key #{key_id} not found (or not yours)")
    db.log_activity(owner, "revoke_api_key", f"#{key_id}")
    return {"id": key_id, "active": False}


def list_all_keys() -> list[dict]:
    """Team-wide key visibility for admins (the route is AdminUser — one
    teammate must not enumerate another's credentials). Makes hidden keys
    minted under a spoofed identity discoverable and revocable."""
    return db.query(
        "SELECT id, prefix, owner, label, active, created_at, last_used_at"
        " FROM api_keys ORDER BY active DESC, id DESC"
    )


def revoke_all_keys(*, actor: str) -> dict:
    """Kill switch: revoke every active key (e.g. after rotating the shared
    token, so a leaked token can't have left durable access behind)."""
    n = db.execute_rowcount("UPDATE api_keys SET active = 0 WHERE active = 1")
    db.log_activity(actor, "revoke_all_api_keys", f"{n} keys")
    return {"revoked": n}


def revoke_keys_for(owner: str, *, actor: str = "system") -> int:
    """Revoke every active key an owner holds — the offboarding half of
    users.set_active(False)."""
    n = db.execute_rowcount(
        "UPDATE api_keys SET active = 0 WHERE owner = ? AND active = 1", (owner,)
    )
    if n:
        db.log_activity(actor, "revoke_api_keys_for", f"{owner}: {n} key(s)")
    return n
