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


def set_growth_interests(name: str, interests: str, *, actor: str = "system") -> dict:
    """Self-declared growth interests — person-level data used to plan the
    future (staffing fit), never to judge the past. Display-only: no
    matching logic, no scores."""
    prev = ensure_user(name).get("growth_interests", "")
    db.execute("UPDATE users SET growth_interests = ? WHERE name = ?", (interests.strip(), name))
    # old→new in the ledger: a spoofed overwrite must be visible + recoverable
    db.log_activity(actor, "set_growth_interests", f"{name}: '{prev}' -> '{interests.strip()}'")
    return {"name": name, "growth_interests": interests.strip()}


def list_users(active_only: bool = True) -> list[dict]:
    if active_only:
        return db.query("SELECT * FROM users WHERE active = 1 ORDER BY kind, name")
    return db.query("SELECT * FROM users ORDER BY kind, name")
