"""Crews: durable groups of people.

The membership the visibility tier reads (docs/VISIBILITY.md).
`crews_of` is what scope.Viewer resolves once per request, and
`assert_writable` is what every write that names a crew_id checks.

A crew is not an engagement. An engagement joins people to WORK: it reaches
`closed`, allocates a percent, and carries a date window, so access built on
one expires the moment the work ships. Membership here is durable and binary.

There is deliberately no delete. `crew_members.crew_id` cascades, so dropping
a crew would strip every membership while content rows kept naming the crew
id — those rows become invisible to everyone but their author, with nothing
to restore the scope from. Deactivating a crew is `update_crew(active=False)`,
which stops new rows being scoped to it and leaves the old ones readable.
"""

from .. import db
from . import users

NAME_LEN = 60
SUMMARY_LEN = 280
ROLES = ("member", "steward")

# crews_of is ONE indexed SELECT and is deliberately not memoized. Two
# things make the obvious cache wrong here, and both fail silently:
# a ContextVar copies the MAPPING and not the value, so one mutable dict set
# in an outer context is shared process-wide; and a job thread runs on
# APScheduler's plain ThreadPoolExecutor, which neither copies nor resets
# contexts, so its worker holds a membership no request-side invalidation can
# reach. A correct cache needs db.on_commit (db.py) for invalidation, an
# immutable value per context, and no caching outside a request scope.
# scope.Viewer resolves the list once per request instead, which is where the
# repetition actually was.


def _row(crew_id: int, *, hold: bool = False) -> dict:
    """The crew, optionally HOLDING it for the rest of the transaction.

    hold=True is what serializes membership changes. The steward floor is a
    count-then-delete, and a plain read takes no lock: two concurrent removals
    both counted two stewards, both passed the floor, and the crew ended with
    none. Locking the parent crew row makes the pair atomic — the members
    table has no single row for them to contend on.
    """
    suffix = " FOR UPDATE" if hold and db.in_transaction() else ""
    row = db.query_one(f"SELECT * FROM crews WHERE id = ?{suffix}", (crew_id,))  # noqa: S608 — fixed literal
    if not row:
        raise db.NotFound(f"crew #{crew_id} not found")
    return row


def _clean_name(name: str, crew_id: int = 0) -> str:
    """Normalize and refuse a name that collides with an existing crew.

    The baseline's lower(name) index is Unicode-aware, so the engine's own
    reads `Café` and `CAFÉ` as two names, and a fullwidth capital as different
    from its plain form. users.py::refuse_fold_collision exists for exactly
    that on the roster. A crew picker is how a writer chooses who reads a row,
    so two entries that render identically are the same class of mistake here.
    """
    name = " ".join(name.split())[:NAME_LEN]
    if not name:
        raise ValueError("a crew needs a name")
    # the four system-actor names are refused for roster rows because holding
    # one walks your writes past the anti-surveillance rule. A crew named
    # `team` is the same hazard from the other side: notifications address
    # `user = 'team'` as a broadcast, so a crew by that name reads as one.
    users.refuse_reserved_name(name)
    folded = users.fold(name)
    for row in db.query("SELECT id, name FROM crews"):
        if row["id"] != crew_id and users.fold(row["name"]) == folded:
            raise ValueError(f"a crew named '{row['name']}' already exists")
    return name


def create_crew(name: str, *, summary: str = "", actor: str, origin: str = "human") -> dict:
    """Create the crew and make the creator its first steward.

    The steward is not optional. A crew with no steward can only be edited by
    an administrator, and the person who made it is the one who knows who
    belongs in it.
    """
    with db.transaction():
        # inside the transaction, not before it: the crew row hold is what makes
        # the check-then-insert atomic, and the index is only the backstop
        name = _clean_name(name)
        now = db.now()
        crew_id = db.execute(
            "INSERT INTO crews (name, summary, origin, created_by, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " RETURNING id",
            (name, summary.strip()[:SUMMARY_LEN], origin, actor, now, now),
        )
        db.execute(
            "INSERT INTO crew_members (crew_id, person, role, origin, created_by, created_at)"
            " VALUES (?, ?, 'steward', ?, ?, ?)",
            (crew_id, actor, origin, actor, now),
        )
        db.log_activity(actor, "create_crew", f"crew #{crew_id} {name}")
    return get_crew(crew_id)


def update_crew(
    crew_id: int,
    *,
    name: str = "",
    summary: str | None = None,
    active: bool | None = None,
    actor: str,
    origin: str = "human",
    admin_override: bool = False,
) -> dict:
    with db.transaction():
        _row(crew_id)
        assert_steward(crew_id, actor, admin_override=admin_override)
        if name:
            db.execute(
                "UPDATE crews SET name = ? WHERE id = ?", (_clean_name(name, crew_id), crew_id)
            )
        if summary is not None:
            db.execute(
                "UPDATE crews SET summary = ? WHERE id = ?",
                (summary.strip()[:SUMMARY_LEN], crew_id),
            )
        if active is not None:
            db.execute("UPDATE crews SET active = ? WHERE id = ?", (1 if active else 0, crew_id))
        db.execute("UPDATE crews SET updated_at = ? WHERE id = ?", (db.now(), crew_id))
        db.log_activity(actor, "update_crew", f"crew #{crew_id} ({origin})")
    return get_crew(crew_id)


def list_crews(active_only: bool = True) -> list[dict]:
    """Every crew with its members. Batched, not one query per crew: the
    settings page lists them all, and the picker in every create form reads
    the same payload."""
    rows = (
        db.query("SELECT * FROM crews WHERE active = 1 ORDER BY lower(name)")
        if active_only
        else db.query("SELECT * FROM crews ORDER BY lower(name)")
    )
    if not rows:
        return []
    members = db.query(
        "SELECT crew_id, person, role FROM crew_members ORDER BY role DESC, lower(person)"
    )
    by_crew: dict[int, list[dict]] = {}
    for m in members:
        by_crew.setdefault(m["crew_id"], []).append({"person": m["person"], "role": m["role"]})
    for crew in rows:
        crew["members"] = by_crew.get(crew["id"], [])
    return rows


def get_crew(crew_id: int) -> dict:
    crew = dict(_row(crew_id))
    crew["members"] = db.query(
        "SELECT person, role FROM crew_members WHERE crew_id = ? ORDER BY role DESC, lower(person)",
        (crew_id,),
    )
    return crew


def crews_of(person: str) -> list[int]:
    """The crew ids this person belongs to — the whole read side of the
    visibility filter (docs/VISIBILITY.md).

    Returns [] for someone in no crew, which is the COMMON case. SQL has no
    `IN ()`, so a filter that interpolates this list must drop the disjunct
    rather than emit an empty one.

    Deactivated crews are INCLUDED: a row scoped to a crew that was later
    switched off must stay readable by the people who could always read it,
    or deactivating a crew silently hides their own work from them.
    """
    if not person:
        return []
    # ORDER BY so the params scope.visible_filter emits are deterministic —
    # two callers with the same membership must produce the same query, and
    # a test that asserts on it must not depend on insert order. Free: the
    # index is (person, crew_id), so this is served without a sort.
    rows = db.query("SELECT crew_id FROM crew_members WHERE person = ? ORDER BY crew_id", (person,))
    return [r["crew_id"] for r in rows]


def is_steward(crew_id: int, person: str) -> bool:
    return bool(
        db.query_one(
            "SELECT 1 FROM crew_members WHERE crew_id = ? AND person = ? AND role = 'steward'",
            (crew_id, person),
        )
    )


def assert_steward(crew_id: int, actor: str, *, admin_override: bool = False) -> None:
    """Refuse a change to this crew by anyone who does not steward it.

    In the SERVICE, not only in the route, and inside the caller's
    transaction. Three routes were the only callers and each checked before
    entering it, which left two gaps. The small one is a race: a steward
    demoted between the check and the write still lands it. The large one is
    layering — crew membership decides what every person reads, so a guard
    that lives in a route is a guard the next caller does not have. There is
    no crew tool today; the moment there is, it writes with no check at all.

    `admin_override` comes from the route because only the route can know it:
    is_named_admin reads the OIDC groups stashed on the request, and the
    strong-identity bar is a property of the door. That half stays there —
    this is the half that belongs to the data.

    remove_member already runs its sole-steward floor inside the transaction,
    with a comment naming this exact hazard. This applies the same rule to the
    authorization itself.
    """
    if admin_override or is_steward(crew_id, actor):
        return
    raise PermissionError("only a steward of this crew can change it. Ask a steward to add you.")


def assert_writable(crew_id: int, person: str) -> int:
    """May this person scope a row to this crew?

    Every phase-3 write path that accepts a crew_id calls this INSIDE the same
    db.transaction() as the insert, not merely before it. Called bare it opens
    its own connections, so a person removed from the crew between the check
    and the write still scopes a row into it.

    Without it a writer can scope a row into a crew they are not in — either
    injecting it into that crew's reading list, or hiding it from everyone
    including themselves. A deactivated crew is refused for new rows, which
    is the other half of the rule crews_of documents: old rows stay readable,
    no new ones arrive.
    """
    crew = _row(crew_id)
    # membership FIRST: the not-active refusal names the crew, and checking
    # it first told a non-member that a crew by that id exists and what it is
    # called. An error never confirms the existence of something the caller
    # cannot see.
    if crew_id not in crews_of(person):
        raise db.NotFound(f"no crew #{crew_id} for {person}")
    if not crew["active"]:
        raise ValueError(f"crew '{crew['name']}' is not active. Pick an active crew.")
    return crew_id


def _stewards(crew_id: int) -> list[str]:
    return [
        r["person"]
        for r in db.query(
            "SELECT person FROM crew_members WHERE crew_id = ? AND role = 'steward'", (crew_id,)
        )
    ]


def add_member(
    crew_id: int,
    person: str,
    *,
    role: str = "member",
    actor: str,
    origin: str = "human",
    admin_override: bool = False,
) -> dict:
    """Add someone, or change the role of someone already in the crew.

    A deactivated crew takes nobody new. crews_of keeps returning one, so
    adding a member would hand them every row already scoped to it — the
    opposite of what deactivating a crew means.
    """
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    # allow_team=False: 'team' is the notifications broadcast address, and a
    # membership row naming it would be a phantom member no rename can move
    person = users.resolve_teammate(person, actor, "person", allow_team=False)
    if users.is_agent(person):
        # a crew scopes what PEOPLE read. Agents read through the gated tool
        # surface, which has its own authority matrix (tools/_gate.py), and a
        # membership row for one would hand it a human's read scope.
        raise ValueError(f"'{person}' is an agent identity and cannot join a crew")
    with db.transaction():
        # held for the same reason as remove_member: the steward floor below
        # is a count-then-write, and a demotion racing a removal must not slip
        # the crew to zero stewards between the two.
        crew = _row(crew_id, hold=True)
        assert_steward(crew_id, actor, admin_override=admin_override)
        already = bool(
            db.query_one(
                "SELECT 1 FROM crew_members WHERE crew_id = ? AND person = ?", (crew_id, person)
            )
        )
        # a role change on an existing member still works, so a deactivated
        # crew can be tidied. Only a NEW member is refused.
        if not crew["active"] and not already:
            raise ValueError(f"crew '{crew['name']}' is not active. Reactivate it first.")
        # demotion is a role CHANGE, so the steward floor belongs here too.
        # Guarding only remove_member let a sole steward set their own role to
        # member and lock the crew: nobody could then edit it, including them.
        if role != "steward" and _stewards(crew_id) == [person]:
            raise ValueError("a crew needs one steward. Make someone else a steward first.")
        db.execute(
            "INSERT INTO crew_members (crew_id, person, role, origin, created_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (crew_id, person) DO UPDATE SET role = excluded.role",
            (crew_id, person, role, origin, actor, db.now()),
        )
        db.log_activity(actor, "crew_member_add", f"crew #{crew_id} {person} ({role})")
    return get_crew(crew_id)


def remove_member(crew_id: int, person: str, *, actor: str, admin_override: bool = False) -> dict:
    """Remove someone from the crew.

    This revokes their read access to every row scoped to
    this crew, except the rows they authored themselves.
    """
    # the same resolution add_member applies, so removing `Ava` finds `ava`.
    # allow_team=False, and self-attribution passes through the way it does
    # everywhere else resolve_teammate is called.
    person = users.resolve_teammate(person, actor, "person", allow_team=False)
    with db.transaction():
        _row(crew_id, hold=True)
        assert_steward(crew_id, actor, admin_override=admin_override)
        # under the crew lock taken above: unheld, two concurrent removals
        # both saw two stewards, both passed, and the crew ended with none
        if _stewards(crew_id) == [person]:
            raise ValueError("a crew needs one steward. Make someone else a steward first.")
        gone = db.execute_rowcount(
            "DELETE FROM crew_members WHERE crew_id = ? AND person = ?", (crew_id, person)
        )
        if not gone:
            raise db.NotFound(f"{person} is not in crew #{crew_id}")
        db.log_activity(actor, "crew_member_remove", f"crew #{crew_id} {person}")
    return get_crew(crew_id)
