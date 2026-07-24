"""Per-teammate API keys for the CLI, MCP server, git hooks, and scripts.
Format: sk-strands-<40 hex>. Only the SHA-256 hash is stored; the full key is
shown exactly once at creation."""

import hashlib
import secrets

from .. import db

PREFIX = "sk-strands-"


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


def verify_key(key: str) -> str | None:
    """Return the owning user for a valid active key, else None."""
    if not key.startswith(PREFIX):
        return None
    row = db.query_one(
        "SELECT id, owner FROM api_keys WHERE key_hash = ? AND active = 1",
        (_hash(key),),
    )
    if not row:
        return None
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
        raise ValueError(f"key #{key_id} not found (or not yours)")
    db.log_activity(owner, "revoke_api_key", f"#{key_id}")
    return {"id": key_id, "active": False}


def list_all_keys() -> list[dict]:
    """Team-wide key visibility (trust model: everyone is admin). Makes hidden
    keys minted under a spoofed identity discoverable and revocable."""
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
