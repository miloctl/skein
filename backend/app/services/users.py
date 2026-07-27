"""Team roster. Trust model: X-User header from the frontend name picker."""

from .. import db

_bench_slugs: set[str] | None = None


def _is_bench_slug(name: str) -> bool:
    global _bench_slugs
    if _bench_slugs is None:
        from . import personas

        _bench_slugs = {p["slug"] for p in personas.list_personas()}
    return name in _bench_slugs


def ensure_user(name: str, kind: str = "human") -> dict:
    name = (name or "anonymous").strip()[:64] or "anonymous"
    # bench persona slugs are reserved identities: a human picking one would
    # silently absorb the persona's trust/authority history (and vice versa)
    existing = db.query_one("SELECT * FROM users WHERE name = ?", (name,))
    if existing is None and kind == "human" and _is_bench_slug(name):
        raise ValueError(f"'{name}' is reserved for a bench persona — pick another name")
    if existing is not None and existing["kind"] != kind and _is_bench_slug(name):
        raise ValueError(
            f"'{name}' already exists as a {existing['kind']} — bench persona"
            " slugs can't be shared across kinds"
        )
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


def set_theme(name: str, theme: str) -> dict:
    """Theme prefs follow the person across browsers. Stored as a small JSON
    object; validated for shape and size, never interpreted server-side."""
    import json

    theme = theme.strip()
    if theme:
        if len(theme) > 400:
            raise ValueError("theme blob too large")
        try:
            parsed = json.loads(theme)
        except ValueError as e:
            raise ValueError("theme must be JSON") from e
        if not isinstance(parsed, dict) or not set(parsed) <= {
            "pack",
            "colorway",
            "appearance",
            "custom",
        }:
            raise ValueError("unknown keys in theme")
    ensure_user(name)
    db.execute("UPDATE users SET theme = ? WHERE name = ?", (theme, name))
    return {"name": name, "saved": bool(theme)}


def get_theme(name: str) -> str:
    row = db.query_one("SELECT theme FROM users WHERE name = ?", (name,))
    return row["theme"] if row else ""


def list_users(active_only: bool = True) -> list[dict]:
    """'anonymous' is the pre-name-pick fallback identity, not a teammate —
    no listing surface (roster, People, staffing) should show it."""
    if active_only:
        return db.query(
            "SELECT * FROM users WHERE active = 1 AND name != 'anonymous' ORDER BY kind, name"
        )
    return db.query("SELECT * FROM users WHERE name != 'anonymous' ORDER BY kind, name")


# every column that attributes a row to a person, per table — explicit so a
# new table with an attribution column fails the parity test until added here
_ATTRIBUTION: dict[str, tuple[str, ...]] = {
    "milestones": ("created_by", "owner"),
    "tasks": ("created_by", "assignee", "delegated_agent", "sponsor"),
    "questions": ("created_by", "asked_by", "assigned_to"),
    "decisions": ("created_by", "decided_by"),
    "standups": ("created_by", "author"),
    "notes": ("created_by", "author"),
    "events": ("created_by",),
    "blockers": ("created_by", "owner"),
    "commitments": ("created_by",),
    "engagements": ("created_by", "lead"),
    "allocations": ("created_by", "person"),
    "intake_requests": ("created_by", "requester"),
    "lessons": ("created_by",),
    "pending_changes": ("proposed_by", "reviewed_by", "requested_by"),
    "notifications": ("user",),
    "activity": ("actor",),
    "tool_usage": ("user",),
    "feedback": ("created_by",),  # pulse rows store '' and stay untouched
    "api_keys": ("owner",),
    "memories": ("user",),
    "agent_authority": ("agent", "updated_by"),
    "artifacts": ("created_by",),
    "context_packs": ("created_by",),
    "finding_dispositions": ("created_by",),
    "chat_threads": ("owner",),
    "chat_folders": ("owner",),
}


def rename_user(old: str, new: str, *, actor: str = "system") -> dict:
    """Rename (or merge, when `new` already exists) a roster entry across
    every attribution column — the fix for 'Mira' vs 'mira'. History moves;
    the old row is deleted (merge) or renamed in place. Strong identity
    required at the route; team-visible tables only (private.db is scoped
    by author name, so the author keeps access by renaming there too)."""
    old, new = old.strip(), new.strip()
    if not old or not new:
        raise ValueError("both names are required")
    if old == new:
        raise ValueError("that is already their name")
    if old == "anonymous" or new == "anonymous":
        raise ValueError("anonymous is not renamable")
    row = db.query_one("SELECT * FROM users WHERE name = ?", (old,))
    if not row:
        raise ValueError(f"no user named '{old}'")
    target = db.query_one("SELECT * FROM users WHERE name = ?", (new,))
    moved: dict[str, int] = {}
    with db.transaction():
        # unique-keyed tables first: fold rather than collide
        # tool_usage (day, user, surface): sum counts into the target's rows
        db.execute(
            "UPDATE tool_usage SET actions = actions + COALESCE((SELECT o.actions"
            " FROM tool_usage o WHERE o.day = tool_usage.day"
            " AND o.surface = tool_usage.surface AND o.user = ?), 0) WHERE user = ?",
            (old, new),
        )
        db.execute(
            "DELETE FROM tool_usage WHERE user = ? AND EXISTS (SELECT 1 FROM tool_usage n"
            " WHERE n.day = tool_usage.day AND n.surface = tool_usage.surface AND n.user = ?)",
            (old, new),
        )
        # agent_authority (agent, entity): the target's existing grant wins
        db.execute(
            "DELETE FROM agent_authority WHERE agent = ? AND EXISTS"
            " (SELECT 1 FROM agent_authority n WHERE n.entity = agent_authority.entity"
            " AND n.agent = ?)",
            (old, new),
        )
        for table, cols in _ATTRIBUTION.items():
            for col in cols:
                n = db.execute_rowcount(
                    f"UPDATE {table} SET {col} = ? WHERE {col} = ?",  # noqa: S608 — constant map
                    (new, old),
                )
                if n:
                    moved[f"{table}.{col}"] = moved.get(f"{table}.{col}", 0) + n
        if target:
            db.execute("DELETE FROM users WHERE name = ?", (old,))
        else:
            db.execute("UPDATE users SET name = ? WHERE name = ?", (new, old))
    from . import private_notes

    private_notes.rename_author(old, new)
    db.log_activity(
        actor,
        "rename_user",
        f"{old} -> {new} ({'merged' if target else 'renamed'}, {sum(moved.values())} rows)",
    )
    return {
        "old": old,
        "new": new,
        "merged": bool(target),
        "rows_moved": sum(moved.values()),
    }


def set_active(name: str, active: bool, *, actor: str = "system") -> dict:
    """Deactivate a roster entry (typo'd name, departed teammate). History
    stays attributed; the name leaves the roster, adoption counts, and the
    context pack — and every API key they own is revoked, so deactivation
    IS the offboarding switch for strong identity too. Reactivation does
    not resurrect keys (mint fresh ones). Strong identity required at the
    route."""
    row = db.query_one("SELECT * FROM users WHERE name = ?", (name,))
    if not row:
        raise ValueError(f"no user named '{name}'")
    if name == actor and not active:
        raise ValueError("you cannot deactivate yourself")
    db.execute("UPDATE users SET active = ? WHERE name = ?", (1 if active else 0, name))
    revoked = 0
    if not active:
        from .api_keys import revoke_keys_for

        revoked = revoke_keys_for(name, actor=actor)
    db.log_activity(
        actor,
        "set_user_active",
        f"{name} -> {'active' if active else 'inactive'}"
        + (f" ({revoked} key(s) revoked)" if revoked else ""),
    )
    return {"name": name, "active": bool(active), "keys_revoked": revoked}
