"""Team roster. Trust model: X-User header from the frontend name picker."""

from .. import db


def _is_bench_slug(name: str) -> bool:
    from . import personas

    return name in personas.bench_slugs()


def refuse_reserved_name(name: str) -> None:
    """One predicate for every identity entry point — ensure_user, rename, and
    the credential doors in routes/deps.py.

    ANY kind, not just human: the activity feed shows a system actor's rows to
    EVERY viewer, so whoever holds one of these names walks their own writes
    past the scope rule. delegate_task and set_authority both mint agents from
    a caller-supplied string, so a human-only check is a hole, not a wall."""
    from .activity import SYSTEM_ACTORS

    if name.casefold() in {a.casefold() for a in SYSTEM_ACTORS}:
        raise ValueError(f"'{name}' is reserved for the system — pick another name")


def ensure_user(name: str, kind: str = "human") -> dict:
    name = (name or "anonymous").strip()[:64] or "anonymous"
    refuse_reserved_name(name)
    # bench persona slugs are reserved identities: a human picking one would
    # silently absorb the persona's trust/authority history (and vice versa)
    existing = db.query_one("SELECT * FROM users WHERE name = ?", (name,))
    if existing is None and kind == "human" and _is_bench_slug(name):
        raise ValueError(f"'{name}' is reserved for a bench persona — pick another name")
    if existing is not None and existing["kind"] != kind and _is_bench_slug(name):
        raise ValueError(
            f"'{name}' already exists as a {existing['kind']} — bench persona"
            " slugs cannot be shared across kinds"
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


def get_growth_interests(name: str) -> str:
    row = db.query_one("SELECT growth_interests FROM users WHERE name = ?", (name,))
    return (row or {}).get("growth_interests") or ""


def _validate_theme(theme: str) -> str:
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
    return theme


def set_theme(name: str, theme: str) -> dict:
    """Theme prefs follow the person across browsers. Stored as a small JSON
    object; validated for shape and size, never interpreted server-side.
    Deliberate provenance exception: NOT activity-logged — saves arrive on a
    debounced timer (slider drags would flood the ledger), the data is
    cosmetic and self-visible only, and it's recoverable from backups."""
    theme = _validate_theme(theme)
    ensure_user(name)
    db.execute("UPDATE users SET theme = ? WHERE name = ?", (theme, name))
    return {"name": name, "saved": bool(theme)}


def set_team_default_theme(theme: str, *, actor: str) -> dict:
    """Operator-set look for fresh browsers and anonymous visitors — a
    default, never an override: any personal choice beats it."""
    theme = _validate_theme(theme)
    db.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES ('team_theme', ?, ?)"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
        " updated_at = excluded.updated_at",
        (theme, db.now()),
    )
    db.log_activity(actor, "set_team_theme", "team default theme updated")
    return {"saved": bool(theme)}


def get_team_default_theme() -> str:
    row = db.query_one("SELECT value FROM app_settings WHERE key = 'team_theme'")
    return row["value"] if row else ""


def get_theme(name: str) -> str:
    row = db.query_one("SELECT theme FROM users WHERE name = ?", (name,))
    return row["theme"] if row else ""


def is_agent(name: str) -> bool:
    row = db.query_one("SELECT kind FROM users WHERE name = ?", (name,))
    return bool(row and row["kind"] == "agent")


def resolve_teammate(
    name: str, actor: str = "", label: str = "name", allow_team: bool = True
) -> str:
    """Case-insensitive roster match; empty and 'team' (the broadcast
    target) pass through, as does self-attribution (name == actor — Slack
    and capture route foreign usernames through as themselves).
    Notifications match `user = ?` exactly, so a typo'd THIRD-PARTY owner
    looks handled but notifies nobody — refuse that here, once.
    allow_team=False for person-shaped data (allocations, absences) where
    'team' would be a phantom capacity row, not a broadcast."""
    name = name.strip()
    if not name or (allow_team and name == "team") or name == actor:
        return name
    known = {u["name"].lower(): u["name"] for u in list_users()}
    match = known.get(name.lower())
    if not match:
        raise ValueError(f"{label} '{name}' is not an active teammate")
    return match


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
    "feature_unlocks": ("person",),
    # activity is DELIBERATELY absent: every chained row's digest covers its
    # actor, so a bulk rewrite here breaks verify_chain permanently at the
    # renamed person's earliest row — and the off-box anchor log makes
    # re-chaining impossible by design. A rename leaves ledger history under
    # the old name; the ledger records what was true when it was written.
    "tool_usage": ("user",),
    "feedback": ("created_by",),  # pulse rows store '' and stay untouched
    "api_keys": ("owner",),
    "memories": ("user", "created_by"),
    "agent_authority": ("agent", "updated_by"),
    "artifacts": ("created_by",),
    "context_packs": ("created_by",),
    "finding_dispositions": ("created_by",),
    "chat_threads": ("owner",),
    "chat_folders": ("owner",),
    "absences": ("created_by", "person"),
    "task_worklog": ("author",),
}


def rename_user(old: str, new: str, *, actor: str = "system") -> dict:
    """Rename (or merge, when `new` already exists) a roster entry across
    every attribution column — the fix for 'Mira' vs 'mira'. History moves;
    the old row is deleted (merge) or renamed in place. Strong identity
    required at the route; team-visible tables only (private.db is scoped
    by author name, so the author keeps access by renaming there too)."""
    old, new = old.strip(), new.strip()[:64]
    if not old or not new:
        raise ValueError("both names are required")
    if old == new:
        raise ValueError("that is already their name")
    if old == "anonymous" or new == "anonymous":
        raise ValueError("anonymous is not renamable")
    # rename must honor the same identity walls ensure_user enforces —
    # otherwise it's the back door around the bench reservation and the
    # human/agent boundary that trust scores and authority assume
    row = db.query_one("SELECT * FROM users WHERE name = ?", (old,))
    if not row:
        raise db.NotFound(f"no user named '{old}'")
    # A third-party rename of an author WITH private notes is refused outright
    # rather than half-completed, and refused BEFORE any row moves. There is
    # no self-repair afterwards: this function deletes the `old` roster row,
    # so the author cannot re-run it as themselves, and their keys have moved
    # with the rename — the notes would be stranded with no supported
    # recovery, which is worse for the legitimate "Mira vs mira" cleanup than
    # refusing the rename.
    from . import private_notes as _pn

    if actor != old and _pn.author_has_notes(old):
        raise ValueError(
            f"'{old}' has private 1:1 notes, and only they can move them."
            f" Ask {old} to rename their own account."
        )
    # rename is the back door around ensure_user's walls, so it honors the
    # same ones. Without this a teammate is renameable to a system actor, and
    # their surviving API key then writes rows every viewer can read.
    refuse_reserved_name(new)
    if _is_bench_slug(new):
        # unconditional: persona names come from files, never from rename —
        # even agent→agent would fold foreign history into the persona
        raise ValueError(f"'{new}' is reserved for a bench persona")
    target = db.query_one("SELECT * FROM users WHERE name = ?", (new,))
    if target and target["kind"] != row["kind"]:
        raise ValueError(
            f"'{old}' is a {row['kind']} and '{new}' is a {target['kind']} —"
            " merging across the human/agent boundary would fold trust and"
            " authority history that must stay separate"
        )
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
        # feature_unlocks (person, knot, kind): the target's existing unlock
        # wins. Without this move, a rename orphaned the guide state under the
        # old name — and since the ledger is immutable across rename, the
        # activity-based predicates could never re-tie under the new one.
        db.execute(
            "DELETE FROM feature_unlocks WHERE person = ? AND EXISTS"
            " (SELECT 1 FROM feature_unlocks n WHERE n.knot = feature_unlocks.knot"
            " AND n.kind = feature_unlocks.kind AND n.person = ?)",
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
            # merge keeps the target's person-level fields but backfills any
            # it never set — the typo'd row is usually the real profile
            db.execute(
                "UPDATE users SET"
                " theme = CASE WHEN theme = '' THEN"
                "   (SELECT theme FROM users WHERE name = ?) ELSE theme END,"
                " growth_interests = CASE WHEN growth_interests = '' THEN"
                "   (SELECT growth_interests FROM users WHERE name = ?) ELSE growth_interests END"
                " WHERE name = ?",
                (old, old, new),
            )
            db.execute("DELETE FROM users WHERE name = ?", (old,))
        else:
            db.execute("UPDATE users SET name = ? WHERE name = ?", (new, old))
    from . import private_notes

    # The private journal follows the person ONLY when the person is doing the
    # renaming. Every keyholder can rename any roster row (the trusted-network
    # model makes them all admins over TEAM data) — but a rename that also
    # moved the private half would let anyone merge someone else's row into
    # their own name and inherit their 1:1 notes and fb: journal, the one
    # dataset the product promises teammates cannot read.
    # ALWAYS move the subject reference: notes other people keep ABOUT this
    # person carry no ownership, so moving them leaks nothing — and NOT moving
    # them stranded every teammate's 1:1 journal about the renamed person
    # under a name with no roster row (empty brief, feedback-gap reset).
    private_notes.rename_subject(old, new)
    # Ownership (the person's OWN notes and audit) moves only when they are
    # the one renaming — the guard above refuses a third-party rename that
    # would need this, so reaching here with actor != old means nothing to move.
    private_moved = actor == old
    if private_moved:
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
        # the caller must be able to tell the operator that private notes did
        # not follow, and that only the author can complete that half
        "private_notes_moved": private_moved,
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
