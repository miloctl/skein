"""Team roster. Trust model: X-User header from the frontend name picker."""

from .. import db


def ensure_user(name: str, kind: str = "human") -> dict:
    name = (name or "anonymous").strip()[:64] or "anonymous"
    # INSERT OR IGNORE + SELECT: safe under concurrent first requests
    db.execute(
        "INSERT OR IGNORE INTO users (name, kind, created_at) VALUES (?, ?, ?)",
        (name, kind if kind in ("human", "agent") else "human", db.now()),
    )
    return db.query_row("SELECT * FROM users WHERE name = ?", (name,))


def list_users(active_only: bool = True) -> list[dict]:
    if active_only:
        return db.query("SELECT * FROM users WHERE active = 1 ORDER BY kind, name")
    return db.query("SELECT * FROM users ORDER BY kind, name")
