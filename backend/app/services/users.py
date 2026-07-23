"""Team roster. Trust model: X-User header from the frontend name picker."""

from .. import db


def ensure_user(name: str, kind: str = "human") -> dict:
    name = (name or "anonymous").strip()[:64] or "anonymous"
    row = db.query_one("SELECT * FROM users WHERE name = ?", (name,))
    if row:
        return row
    uid = db.execute(
        "INSERT INTO users (name, kind, created_at) VALUES (?, ?, ?)",
        (name, kind if kind in ("human", "agent") else "human", db.now()),
    )
    return {"id": uid, "name": name, "kind": kind, "active": 1}


def list_users(active_only: bool = True) -> list[dict]:
    if active_only:
        return db.query("SELECT * FROM users WHERE active = 1 ORDER BY kind, name")
    return db.query("SELECT * FROM users ORDER BY kind, name")
