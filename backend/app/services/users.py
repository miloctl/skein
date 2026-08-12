"""Team roster. Trust model: X-User header from the frontend name picker."""

import re

from .. import db
from ..identity_names import (
    CORE_MACHINE_SUBJECTS,
    HUMAN_RESERVED_SUBJECTS,
    ROSTER_CORE_AGENT_SUBJECTS,
    fold_identity,
)

IDENTITY_COLLISION = (
    "This identity has conflicting roster ownership. Ask whoever runs the server to"
    " run 'python -m app.identity_audit'."
)


def _is_bench_slug(name: str) -> bool:
    """Names the agent layer owns: a persona slug, and a FLOCK slug.

    A flock never writes as itself, but the merge step logs its spend under
    the flock slug (routes/chat.py::_log_usage), so a human holding that name
    collects a bill for model calls they did not make. Both sets are computed
    live from the files, so a slug added to an overlay is reserved with it.
    """
    from . import flocks, personas

    return name in personas.bench_slugs() or name in {f["slug"] for f in flocks.list_flocks()}


def fold(name: str) -> str:
    """The one normalization every identity comparison uses — roster names
    here, and crew names in services/crews.py. NFKC because
    a fullwidth `TEAM` renders as `team`, and category Cf is stripped
    because a zero-width joiner inside `team` does too — a name that reads as a system actor in every
    surface must not be a different identity from the system actor."""
    return fold_identity(name)


def names_someone(text: str, roster: set[str]) -> bool:
    """Does this free text carry a roster name, at WORD boundaries?

    `name in text` is the trap this exists to close: with a roster holding
    Ram, Ian and Ana it suppressed "Program review", "Alliance sync" and
    "Analytics review" — three ordinary meeting titles out of four. Callers
    use this to decide whether a team-wide string may quote free text, so a
    false positive costs one withheld sentence and a false negative names a
    person beside a past failure.

    `roster` holds ALREADY-FOLDED names. Multi-word names match when all their
    parts appear, so "Dana Whitfield" is found in "1:1 dana / whitfield".
    """
    words = {w for w in re.split(r"[^\w]+", fold(text)) if w}
    if not words:
        return False
    return any(
        parts and parts <= words
        for parts in ({w for w in re.split(r"[^\w]+", mate) if w} for mate in roster)
    )


def refuse_reserved_name(name: str) -> None:
    """One predicate for every identity entry point — ensure_user, rename, and
    the credential doors in routes/deps.py.

    ANY kind, not just human: the activity feed shows a system actor's rows to
    EVERY viewer, so whoever holds one of these names walks their own writes
    past the scope rule. delegate_task and set_authority both mint agents from
    a caller-supplied string, so a human-only check is a hole, not a wall.
    It normalizes its own input, because two of its four callers hand it a
    header value that nothing else has touched."""
    if fold(name) in {fold(a) for a in HUMAN_RESERVED_SUBJECTS}:
        raise ValueError("that name is reserved for the system — pick another name")


def refuse_authenticated_name(name: str) -> None:
    """Refuse every synthetic or machine-owned authenticated identity.

    ``anonymous`` remains valid for old unnamed records and for the absent
    weak-header fallback. It is never a person, service, or agent identity.
    """
    if fold(name) in {fold(a) for a in CORE_MACHINE_SUBJECTS}:
        raise ValueError("that name is reserved for the system — pick another name")


def reserved_refusal(name: str) -> str:
    """The reserved-name refusal as a STRING, for the perimeter middleware,
    which returns responses rather than raising. One rule, two shapes."""
    try:
        refuse_authenticated_name(name)
    except ValueError as exc:
        return str(exc)
    return ""


def reserved_name_rows() -> list[str]:
    """Roster rows whose name the wall now refuses. Named at boot so whoever
    runs the server can move them with rename_user, the one code path that
    knows every attribution column and the private notes DB."""
    reserved = {fold(a): a for a in HUMAN_RESERVED_SUBJECTS}
    stuck: list[str] = []
    for row in db.query("SELECT name, kind FROM users"):
        claim = reserved.get(fold(row["name"]))
        if not claim:
            continue
        if claim in ROSTER_CORE_AGENT_SUBJECTS and row["name"] == claim and row["kind"] == "agent":
            continue
        stuck.append(row["name"])
    return stuck


def refuse_fold_collision(name: str, *, ignore: str = "") -> None:
    """One folded name, one roster row — checked wherever a row is created OR
    moved. Checked only in ensure_user, rename_user is the back door:
    renaming a human onto an agent's name bricks them at every door,
    including the rename route itself. Kind does not soften it:
    authority_level and trust key on the EXACT name, so a second agent row
    folding onto the first would answer to neither's kill switch.

    Folds in Python, not in SQL. sqlite's lower() is ASCII-only, so it reads
    `SCOÜT` and `scoüt` as different names while resolve_teammate and
    mentions.scan read them as the same one — the collision then arrives
    through the very guard meant to stop it."""
    target = fold(name)
    for row in db.query("SELECT name, kind FROM users"):
        # the EXACT-name case belongs to the callers: ensure_user reads it as
        # `existing`, and rename_user's target lookup explains the human/agent
        # boundary in the terms that case deserves. This guard is for the
        # variants those exact lookups cannot see.
        if row["name"] in (ignore, name):
            continue
        if fold(row["name"]) == target:
            article = "an" if row["kind"] == "agent" else "a"
            raise ValueError(
                f"'{row['name']}' already exists as {article} {row['kind']} —"
                f" '{name}' differs only by case, and one name must mean one identity"
            )


def refuse_ambiguous_identity(name: str) -> None:
    """Fail closed when an upgraded roster already has a folded duplicate."""
    try:
        refuse_fold_collision(name)
    except ValueError as exc:
        # An exact row plus a folded peer is inherited ambiguous ownership.
        # A caller-supplied variant with no exact row is an ordinary name
        # collision and keeps the established, more useful error contract.
        if db.query_one("SELECT 1 FROM users WHERE name = ?", (name,)):
            raise ValueError(IDENTITY_COLLISION) from exc
        raise


def _content_machine_claims() -> dict[str, str]:
    """Deployment content names that belong to machine identities."""
    from . import flocks, personas

    claims = {fold(slug): slug for slug in personas.bench_slugs()}
    claims.update({fold(item["slug"]): item["slug"] for item in flocks.list_flocks()})
    return claims


def refuse_human_machine_claim(name: str) -> None:
    """Refuse a human name owned by content, including inherited rows."""
    refuse_authenticated_name(name)
    refuse_ambiguous_identity(name)
    if fold(name) in _content_machine_claims():
        raise ValueError(IDENTITY_COLLISION)


def identity_collision_refusal(name: str) -> str:
    """Generic perimeter refusal for ambiguous human identity ownership."""
    try:
        refuse_human_machine_claim(name)
    except ValueError as exc:
        return str(exc)
    return ""


def folded_identity_collisions() -> list[list[dict]]:
    """All legacy roster groups that violate one-folded-name/one-owner."""
    groups: dict[str, list[dict]] = {}
    for row in db.query("SELECT name, kind FROM users ORDER BY name"):
        groups.setdefault(fold(row["name"]), []).append(row)
    return [rows for rows in groups.values() if len(rows) > 1]


def identity_ownership_conflicts() -> list[dict]:
    """Legacy roster conflicts that need an explicit operator repair."""
    conflicts = [
        {"kind": "folded-roster", "names": tuple(row["name"] for row in rows)}
        for rows in folded_identity_collisions()
    ]
    content = _content_machine_claims()
    reserved = {fold(name): name for name in HUMAN_RESERVED_SUBJECTS}
    duplicate_names = {name for item in conflicts for name in item["names"]}
    for row in db.query("SELECT name, kind FROM users ORDER BY name"):
        if row["name"] in duplicate_names:
            continue
        folded = fold(row["name"])
        if claim := reserved.get(folded):
            legitimate_core_agent = (
                claim in ROSTER_CORE_AGENT_SUBJECTS
                and row["kind"] == "agent"
                and row["name"] == claim
            )
            if not legitimate_core_agent:
                conflicts.append(
                    {
                        "kind": "core-owner",
                        "names": (row["name"],),
                        "claim": claim,
                    }
                )
            continue
        claim = content.get(folded)
        if claim and (row["kind"] != "agent" or row["name"] != claim):
            conflicts.append(
                {
                    "kind": "content-owner",
                    "names": (row["name"],),
                    "claim": claim,
                }
            )
    return conflicts


def identity_ownership_error() -> str:
    """Safe health text: report the fault without exposing roster names."""
    count = len(identity_ownership_conflicts())
    if not count:
        return ""
    noun = "group" if count == 1 else "groups"
    return f"{count} conflicting identity {noun}. Run 'python -m app.identity_audit' on the server."


def ensure_user(name: str, kind: str = "human") -> dict:
    name = (name or "anonymous").strip()[:64] or "anonymous"
    effective_kind = kind if kind in ("human", "agent") else "human"
    # The folded-name check and insert are one write transaction. SQLite has
    # no Unicode case-fold collation for a unique index, so serialization is
    # what prevents concurrent `Mira` and `MIRA` rows.
    with db.transaction():
        if effective_kind == "agent":
            refuse_authenticated_name(name)
        else:
            refuse_reserved_name(name)
        # bench persona slugs are reserved identities: a human picking one
        # would silently absorb the persona's trust/authority history (and
        # vice versa)
        existing = db.query_one("SELECT * FROM users WHERE name = ?", (name,))
        refuse_fold_collision(name)
        if kind == "human" and _is_bench_slug(name):
            raise ValueError("that name is reserved for a bench persona — pick another name")
        if existing is not None and existing["kind"] != kind and _is_bench_slug(name):
            raise ValueError(
                f"'{name}' already exists as a {existing['kind']} — bench persona"
                " slugs cannot be shared across kinds"
            )
        # INSERT OR IGNORE preserves the established idempotent contract for
        # an exact existing name. Strict human and agent entry points validate
        # the returned kind below this compatibility layer.
        db.execute(
            "INSERT OR IGNORE INTO users (name, kind, created_at) VALUES (?, ?, ?)",
            (name, effective_kind, db.now()),
        )
        return db.query_row("SELECT * FROM users WHERE name = ?", (name,))


def ensure_human_identity(name: str) -> dict:
    """Reserve a human name and refuse any exact or folded machine owner."""
    normalized = (name or "anonymous").strip()[:64] or "anonymous"
    refuse_authenticated_name(normalized)
    # Durable exact ownership needs no write lock. This is the steady-state
    # OIDC path, so authenticated reads retain WAL's reader/writer concurrency.
    existing = db.query_one("SELECT * FROM users WHERE name = ?", (normalized,))
    if existing is not None:
        refuse_human_machine_claim(normalized)
        if existing["kind"] != "human":
            raise ValueError(f"'{normalized}' is already owned by an agent identity")
        return existing
    with db.transaction():
        existing = db.query_one("SELECT kind FROM users WHERE name = ?", (normalized,))
        if existing is not None and existing["kind"] != "human":
            raise ValueError(f"'{normalized}' is already owned by an agent identity")
        row = ensure_user(normalized, kind="human")
        if row["kind"] != "human":
            raise ValueError(f"'{normalized}' is already owned by an agent identity")
        return row


def ensure_agent_identity(name: str) -> dict:
    """Reserve one exact name for machine use without reusing a human row.

    Keep the collision check, insert, and final kind check under one immediate
    transaction. Otherwise a concurrent human claim can land between the
    first query and ``INSERT OR IGNORE`` and make a machine use the human row.
    """
    normalized = (name or "anonymous").strip()[:64] or "anonymous"
    with db.transaction():
        refuse_ambiguous_identity(normalized)
        existing = db.query_one("SELECT * FROM users WHERE name = ?", (normalized,))
        if existing is not None and existing["kind"] != "agent":
            raise ValueError(f"'{normalized}' is already owned by a human identity")
        if fold(normalized) in {fold(item) for item in CORE_MACHINE_SUBJECTS}:
            # A caller can use a core agent that startup already reserved, but
            # it cannot create a system actor from user-supplied text.
            if existing is not None and normalized in ROSTER_CORE_AGENT_SUBJECTS:
                return existing
            refuse_authenticated_name(normalized)
        row = ensure_user(normalized, kind="agent")
        if row["kind"] != "agent":
            # This is also a postcondition for callers that are already in an
            # outer transaction. A successful machine reservation must never
            # return a human-owned row.
            raise ValueError(f"'{normalized}' is already owned by a human identity")
        return row


def _reserve_core_agent_identity(name: str) -> dict:
    """Reserve the one roster-backed core agent during application startup."""
    if name != "agent":
        raise ValueError("only the built-in agent has a core roster identity")
    with db.transaction():
        refuse_ambiguous_identity(name)
        existing = db.query_one("SELECT kind FROM users WHERE name = ?", (name,))
        if existing is not None and existing["kind"] != "agent":
            raise ValueError(f"'{name}' is already owned by a human identity")
        db.execute(
            "INSERT OR IGNORE INTO users (name, kind, created_at) VALUES (?, 'agent', ?)",
            (name, db.now()),
        )
        row = db.query_row("SELECT * FROM users WHERE name = ?", (name,))
        if row["kind"] != "agent":
            raise ValueError(f"'{name}' is already owned by a human identity")
        return row


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
            raise ValueError("keep the theme under 400 characters")
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
            raise ValueError(
                "theme must be a JSON object with only these keys: pack,"
                " colorway, appearance, custom"
            )
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
    """Does ANY row with this name belong to an agent?

    Case-insensitive, and asking about the SET rather than about one row.
    `users.name` is case-sensitively unique, so `Scout` and `scout` coexist —
    an exact match let a human's capitalization walk through the agent wall in
    routes/deps.py, and a first-row match let it walk through the forge's.
    resolve_teammate already matches this way, so the question every surface
    asks about a name is now the same question."""
    target = fold(name)
    if not target:
        return False
    # folded in PYTHON, not in SQL. sqlite's lower() is ASCII-only, so it
    # reads `SCOÜT` and `scoüt` as different names while resolve_teammate and
    # mentions.scan read them as the same one — the wall and the resolver must
    # not disagree about what two names being equal means.
    return any(
        fold(r["name"]) == target for r in db.query("SELECT name FROM users WHERE kind = 'agent'")
    )


def is_active(name: str) -> bool:
    """Is this identity allowed through a door? True when NO row exists — a
    first-ever sign-in has not been added to the roster yet, and reads never
    mint rows. Folded like is_agent above, or `ALICE` walks past the check that
    `alice` fails, and the wall would disagree with the resolver about what two
    names being equal means."""
    target = fold(name)
    if not target:
        return True
    rows = [r for r in db.query("SELECT name, active FROM users") if fold(r["name"]) == target]
    return all(r["active"] for r in rows)


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
        raise ValueError(f"{label} is not an active teammate")
    return match


def list_users(active_only: bool = True) -> list[dict]:
    """'anonymous' is the pre-name-pick fallback identity, not a teammate —
    no listing surface (roster, People, staffing) shows it."""
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
    "promises": ("created_by",),
    "engagements": ("created_by", "lead"),
    "allocations": ("created_by", "person"),
    "intake_requests": ("created_by", "requester"),
    "lessons": ("created_by",),
    # sponsor_at_submission is a NAME (010) and `tasks.sponsor` is renamed —
    # leaving it behind makes the two disagree, and review._acceptance_evidence
    # reads that disagreement as a handover, printing "X sponsored this when the
    # work was submitted" about a delegation that never moved, in front of the
    # Approve button.
    "pending_changes": (
        "proposed_by",
        "reviewed_by",
        "requested_by",
        "sponsor_at_submission",
    ),
    "notifications": ("user",),
    "notification_reads": ("user",),
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
    # crew membership keys on the roster name, and a rename that leaves it
    # behind silently drops the person out of every crew they could read
    "crew_members": ("person", "created_by"),
    "crews": ("created_by",),
    "chat_folders": ("owner",),
    "absences": ("created_by", "person"),
    "task_worklog": ("author",),
    # the member slugs inside members-JSON are agent identities, not roster
    # names, and rename_user moves an agent row too — but a slug rename would
    # need a JSON rewrite, so only the asking human moves here
    "flock_traces": ("user",),
}


def _validate_rename_target(old: str, new: str, row: dict, *, identity_repair: bool) -> dict | None:
    """Validate every target rule before a core or private identity moves."""
    refuse_reserved_name(new)
    refuse_fold_collision(new, ignore=old)
    if _is_bench_slug(new):
        raise ValueError("the new name is reserved for a bench persona")
    target = db.query_one("SELECT * FROM users WHERE name = ?", (new,))
    if target and identity_repair:
        raise ValueError("identity ownership repair cannot merge roster rows")
    if target and target["kind"] != row["kind"]:
        raise ValueError(
            f"'{old}' is a {row['kind']} and '{new}' is a {target['kind']} —"
            " merging across the human/agent boundary would fold trust and"
            " authority history that must stay separate"
        )
    return target


def _rename_names(old: str, new: str) -> tuple[str, str]:
    old, new = old.strip(), new.strip()[:64]
    if not old or not new:
        raise ValueError("both names are required")
    if old == new:
        raise ValueError("that is already their name")
    if old == "anonymous" or new == "anonymous":
        raise ValueError(
            "anonymous cannot be renamed, and no account can be renamed to"
            " anonymous — pick a real name first"
        )
    return old, new


def rename_user(
    old: str,
    new: str,
    *,
    actor: str = "system",
    _identity_repair: bool = False,
) -> dict:
    """Rename (or merge, when `new` already exists) a roster entry across
    every attribution column — the fix for 'Mira' vs 'mira'. History moves;
    the old row is deleted (merge) or renamed in place. Strong identity
    required at the route; team-visible tables only (private.db is scoped
    by author name, so the author keeps access by renaming there too)."""
    old, new = _rename_names(old, new)
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
    target = _validate_rename_target(old, new, row, identity_repair=_identity_repair)
    moved: dict[str, int] = {}
    with db.transaction():
        # Repeat every ownership check after the immediate transaction starts.
        # A concurrent create or rename can land after the compatibility checks
        # above but before this write transaction.
        current = db.query_one("SELECT * FROM users WHERE name = ?", (old,))
        if not current:
            raise db.NotFound(f"no user named '{old}'")
        target = _validate_rename_target(old, new, current, identity_repair=_identity_repair)
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
        # crew_members (crew_id, person): the target's row wins, but a STEWARD
        # row is kept over a member one — merging two halves of one person
        # must not quietly demote them out of a crew they steward. Two rows
        # that are the same person almost always share a crew, so without this
        # the whole merge raised IntegrityError and answered 500.
        db.execute(
            "UPDATE crew_members SET role = 'steward' WHERE person = ?"
            " AND EXISTS (SELECT 1 FROM crew_members o WHERE o.crew_id = crew_members.crew_id"
            " AND o.person = ? AND o.role = 'steward')",
            (new, old),
        )
        db.execute(
            "DELETE FROM crew_members WHERE person = ? AND EXISTS"
            " (SELECT 1 FROM crew_members n WHERE n.crew_id = crew_members.crew_id"
            " AND n.person = ?)",
            (old, new),
        )
        # chat_folders (owner, name): the target's folder wins. Same shape and
        # the same 500 — a rename where both halves made a folder of the same
        # name could not complete.
        db.execute(
            "DELETE FROM chat_folders WHERE owner = ? AND EXISTS"
            " (SELECT 1 FROM chat_folders n WHERE n.name = chat_folders.name"
            " AND n.owner = ?)",
            (old, new),
        )
        # notification_reads (notification_id, user): a dismissal the target
        # already made wins. A team announcement is one shared row that every
        # reader dismisses separately (009), so two halves of one person having
        # both dismissed the same announcement is the ORDINARY case — without
        # this fold the UPDATE below hits the primary key and the whole merge
        # raises IntegrityError, which has no handler and answers 500.
        db.execute(
            "DELETE FROM notification_reads WHERE user = ? AND EXISTS"
            " (SELECT 1 FROM notification_reads n"
            "  WHERE n.notification_id = notification_reads.notification_id AND n.user = ?)",
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
    detail = f"{old} -> {new} ({'merged' if target else 'renamed'}, {sum(moved.values())} rows)"
    if _identity_repair:
        db.log_activity(actor, "repair_identity_ownership", detail)
    else:
        db.log_activity(actor, "rename_user", detail)
    return {
        "old": old,
        "new": new,
        "merged": bool(target),
        "rows_moved": sum(moved.values()),
        # the caller must be able to tell the operator that private notes did
        # not follow, and that only the author can complete that half
        "private_notes_moved": private_moved,
    }


def repair_identity_ownership(old: str, new: str) -> dict:
    """Repair one quarantined identity without impersonating its owner.

    Private ownership moves first and writes a private administrative audit.
    The core rename follows with the reserved ``system`` actor. The two SQLite
    stores cannot share a transaction. If the core step fails, fix its stated
    cause and repeat the same command; the private move is idempotent.
    """
    old, new = _rename_names(old, new)
    from . import private_notes

    with db.transaction():
        conflicted = {
            name for conflict in identity_ownership_conflicts() for name in conflict["names"]
        }
        if old not in conflicted:
            raise ValueError(f"'{old}' is not in a current identity ownership conflict")
        row = db.query_row("SELECT * FROM users WHERE name = ?", (old,))
        _validate_rename_target(old, new, row, identity_repair=True)
        private_notes.recover_identity_ownership(old, new)
        result = rename_user(
            old,
            new,
            actor="system",
            _identity_repair=True,
        )
    return {**result, "private_notes_moved": True, "repair_origin": "identity-audit"}


def set_active(name: str, active: bool, *, actor: str = "system") -> dict:
    """Deactivate a roster entry (typo'd name, departed teammate). History
    stays attributed; the name leaves the roster, adoption counts, and the
    context pack — and every API key they own is revoked, so deactivation
    IS the offboarding switch for strong identity too. Reactivation does
    not resurrect keys (mint fresh ones). Strong identity required at the
    route.

    The claim above holds because routes/deps.py::_refuse_inactive and the
    perimeter middleware both consult is_active. Revoking keys alone left the
    oidc and header doors open, and an offboarded teammate kept strong read and
    write until someone separately disabled the IdP account."""
    row = db.query_one("SELECT * FROM users WHERE name = ?", (name,))
    if not row:
        raise ValueError("no user with that name")
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
