"""The visibility inventory, and the fragment the tier filter emits.

visible_filter has callers across app/services now. These tests are still
about the INVENTORY, because that is the part that rots: two of the three
enumerate-everything structures in this repository went stale in exactly the
direction CI did not check. tests/test_visibility_authz.py covers the two
directions this one does not — which mutations are guarded, and which reads
are filtered.
"""

import pytest

from app import db
from app.services import crews, scope, users, work


def test_every_table_is_classified(client, fresh_db):
    """A new table joins CLASSIFIED or UNSCOPED, with a reason. Without this
    a migration adds a content table and the tier silently skips it — which
    is the failure mode a filter-per-query design cannot otherwise catch."""
    missing = scope.unclassified()
    assert not missing, (
        f"these tables have no visibility classification: {sorted(missing)}."
        " Add each to scope.CLASSIFIED with its author column, or to"
        " scope.UNSCOPED with the reason a tier does not belong on it."
    )


def test_the_inventory_has_no_stale_entries(client, fresh_db):
    """A classification for a table that no longer exists is a reason the
    next reader trusts about something else."""
    live = {
        r["name"]
        for r in fresh_db.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    ghosts = (set(scope.CLASSIFIED) | set(scope.UNSCOPED)) - live
    assert not ghosts, f"these tables are gone. Delete their entries: {sorted(ghosts)}"


def test_no_table_is_in_both_maps():
    both = set(scope.CLASSIFIED) & set(scope.UNSCOPED)
    assert not both, f"classified and unscoped at once: {sorted(both)}"


def test_every_classified_table_has_a_reader_facing_noun():
    """scope.missing does NOUN[table] on the not-found path of every guarded
    service. A classified table with no entry raises KeyError there, so the
    one sentence a caller sees for an absent row becomes a 500 — and only for
    the table somebody just added, which is the last place anyone looks."""
    orphans = set(scope.CLASSIFIED) - set(scope.NOUN)
    assert not orphans, (
        f"classified with no reader-facing noun: {sorted(orphans)}."
        " Add one to scope.NOUN — `table[:-1]` renders 'memorie'."
    )
    stale = set(scope.NOUN) - set(scope.CLASSIFIED)
    assert not stale, f"nouns for tables that carry no tier: {sorted(stale)}"
    for table, noun in scope.NOUN.items():
        assert "_" not in noun, f"{table} reads as an identifier, not a word: {noun!r}"


def test_every_author_column_exists(client, fresh_db):
    """The filter emits this column name into SQL. A typo here is a 500 on
    every scoped read of that table."""
    for table, column in scope.CLASSIFIED.items():
        have = {c["name"] for c in fresh_db.query(f"PRAGMA table_info({table})")}
        assert column in have, f"{table}.{column} does not exist"


def test_every_unscoped_reason_is_written():
    for table, reason in scope.UNSCOPED.items():
        assert len(reason.strip()) > 20, f"{table} needs a real reason, not a shrug"


def test_the_filter_defaults_closed(fresh_db):
    """A read that cannot name its caller must not be handed the private
    tier. Default-open here would make every unidentified job a leak."""
    sql, params = scope.visible_filter(scope.NOBODY, "tasks")
    assert sql == "(visibility = ?)"
    assert params == [scope.WORKSPACE]


def test_the_filter_drops_the_crew_clause_for_someone_in_no_crew(fresh_db):
    """SQLite has no `IN ()`. Emitting an empty one is a syntax error, not a
    disjunct that matches nothing — and being in no crew is the common case."""
    users.ensure_user("ava")
    sql, params = scope.visible_filter(scope.Viewer("ava", True), "tasks")
    assert "crew_id" not in sql
    assert params == [scope.WORKSPACE, "ava"]


def test_the_filter_names_the_viewers_crews(fresh_db):
    users.ensure_user("ava")
    a = crews.create_crew("Platform", actor="ava")["id"]
    b = crews.create_crew("Design", actor="ava")["id"]
    sql, params = scope.visible_filter(scope.Viewer("ava", True), "tasks", alias="t")
    assert "t.crew_id IN (?, ?)" in sql
    assert "t.visibility = ?" in sql and "t.created_by = ?" in sql
    assert params == [scope.WORKSPACE, "ava", scope.CREW, *sorted([a, b])]
    assert crews.crews_of("ava") == sorted([a, b]), "unordered ids make the SQL text vary"


@pytest.mark.parametrize("alias", ["", "t"])
def test_the_fragment_is_valid_sql_against_a_real_table(fresh_db, alias):
    """The fragment is interpolated into a WHERE clause, so it has to parse
    with and without a table alias, and its params have to line up."""
    users.ensure_user("ava")
    crews.create_crew("Platform", actor="ava")
    fresh_db.execute("CREATE TABLE probe (created_by TEXT, visibility TEXT, crew_id INTEGER)")
    fresh_db.execute(
        "INSERT INTO probe (created_by, visibility, crew_id) VALUES ('bo', 'workspace', NULL)"
    )
    sql, params = scope.visible_filter(scope.Viewer("ava", True), "tasks", alias=alias)
    prefix = "probe t" if alias else "probe"
    rows = fresh_db.query(f"SELECT * FROM {prefix} WHERE {sql}", tuple(params))  # noqa: S608 — the fragment is what is under test, params bound separately
    assert len(rows) == 1


def test_a_private_row_is_not_reachable_through_the_filter(fresh_db):
    """private is excluded structurally, never by this predicate — the whole
    reason the filter covers one tier instead of three. Its own author reads
    it through the author disjunct, and nobody else has a clause that can."""
    users.ensure_user("ava")
    sql, params = scope.visible_filter(scope.Viewer("bo", True), "tasks")
    fresh_db.execute("CREATE TABLE probe (created_by TEXT, visibility TEXT, crew_id INTEGER)")
    fresh_db.execute(
        "INSERT INTO probe (created_by, visibility, crew_id) VALUES ('ava', 'private', NULL)"
    )
    assert fresh_db.query(f"SELECT * FROM probe WHERE {sql}", tuple(params)) == []  # noqa: S608 — as above
    own_sql, own_params = scope.visible_filter(scope.Viewer("ava", True), "tasks")
    got = fresh_db.query(f"SELECT * FROM probe WHERE {own_sql}", tuple(own_params))  # noqa: S608 — as above
    assert len(got) == 1


def test_a_viewers_crews_are_resolved_once(fresh_db):
    """Per viewer, not per query: a dashboard fans out to ~27 scoped reads and
    db.connect() costs 280us against a 2us SELECT."""
    users.ensure_user("ava")
    cid = crews.create_crew("Platform", actor="ava")["id"]
    users.ensure_user("bo")
    crews.add_member(cid, "bo", actor="ava")
    v = scope.Viewer("bo", True)
    assert v.crew_ids == [cid]
    # the snapshot is the point — a membership change mid-request must not
    # make two queries in one RESPONSE disagree with each other. Removing
    # `bo`, not `ava`: ava is the sole steward and cannot be removed, which is
    # why this call was once written `if False else None` and asserted nothing.
    crews.remove_member(cid, "bo", actor="ava")
    assert v.crew_ids == [cid], "the viewer already built must not change under it"
    # and the NEXT request sees the removal — asserted on a new Viewer, because
    # the old one is the snapshot and re-reading it proves nothing
    assert scope.Viewer("bo", True).crew_ids == []


def test_the_fragment_survives_a_caller_predicate_beside_it(fresh_db):
    """The docstring's first splicing hazard, asserted rather than described.

    `visible_filter` returns its disjuncts wrapped in parentheses. Unwrapped,
    `WHERE assignee = ? AND visibility = ? OR created_by = ?` binds the OR
    loosest and returns every row the viewer authored, whatever the caller's
    own predicate said — which is a leak that reads as a working filter.

    Spliced with NOTHING before it, parentheses cannot matter, and that is how
    the existing SQL-validity test splices it. Every real caller has a
    predicate beside it, so this one does too.
    """
    users.ensure_user("ava")
    users.ensure_user("bo")
    work.create_task(title="ava's own private task", actor="ava", visibility="private")
    frag, params = scope.visible_filter(scope.Viewer("ava", True), "tasks")
    rows = db.query(
        f"SELECT title FROM tasks WHERE assignee = ? AND {frag}",  # noqa: S608 — test
        ("nobody-has-this-name", *params),
    )
    assert rows == [], (
        "the caller's own predicate was defeated — visible_filter must"
        f" parenthesize its disjuncts. Fragment was: {frag}"
    )


def test_a_machine_name_is_never_a_viewer(fresh_db):
    """`Viewer` blanks a name that belongs to no person. The list it uses and
    the one `is_machine` uses must agree: with only `_NOT_A_VIEWER`,
    Viewer("scheduler", True) kept its name and earned an author arm over
    every row the scheduler ever wrote, while is_machine("scheduler") said
    the opposite two functions away."""
    for name in ("system", "scheduler", "forge", "ci", "mcp", "agent", "anonymous"):
        assert scope.is_machine(name), name
        assert scope.Viewer(name, True).name == "", f"{name} became a viewer"
    users.ensure_user("ava")
    assert scope.Viewer("ava", True).name == "ava"


def test_the_tiers_match_the_documented_three():
    assert scope.TIERS == ("private", "crew", "workspace")
    assert scope.WORKSPACE == "workspace", "the migration default — changing it changes every row"


def test_a_content_table_added_later_fails_the_sweep(client, fresh_db):
    """Proves the sweep is not tautological: a new table must actually break
    it, or the inventory is decoration."""
    db.execute("CREATE TABLE probe_notes (id INTEGER PRIMARY KEY, created_by TEXT)")
    try:
        assert "probe_notes" in scope.unclassified()
    finally:
        db.execute("DROP TABLE probe_notes")


def test_a_crew_row_reaches_its_members_and_nobody_else(fresh_db):
    """The property the whole module exists for, and the one a fragment test
    cannot prove: asserting on the SQL string passes for `crew_id IN (…) OR 1`,
    which shows every crew row to anyone in any crew."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    users.ensure_user("cass")
    mine = crews.create_crew("Platform", actor="ava")["id"]
    crews.add_member(mine, "bo", actor="ava")
    theirs = crews.create_crew("Design", actor="cass")["id"]

    fresh_db.execute("CREATE TABLE probe (created_by TEXT, visibility TEXT, crew_id INTEGER)")
    for who, tier, crew in [
        ("ava", "crew", mine),  # ava's crew row
        ("cass", "crew", theirs),  # a crew ava is not in
        ("cass", "workspace", None),  # everyone
        ("cass", "private", None),  # nobody but cass
    ]:
        fresh_db.execute(
            "INSERT INTO probe (created_by, visibility, crew_id) VALUES (?, ?, ?)",
            (who, tier, crew),
        )

    def seen(viewer: str) -> set[tuple]:
        sql, params = scope.visible_filter(scope.Viewer(viewer, True), "tasks")
        rows = fresh_db.query(f"SELECT * FROM probe WHERE {sql}", tuple(params))  # noqa: S608 — as above
        return {(r["created_by"], r["visibility"]) for r in rows}

    # a member reads their crew's row and the workspace one, never the other
    # crew's and never cass's private
    assert seen("bo") == {("ava", "crew"), ("cass", "workspace")}
    # cass reads their own crew, their own private, and the workspace row
    assert seen("cass") == {("cass", "crew"), ("cass", "private"), ("cass", "workspace")}
    # someone in no crew reads only the workspace row
    assert seen("dana") == {("cass", "workspace")}


def test_an_unclassified_table_is_refused(fresh_db):
    """The column is looked up, never passed. A caller-supplied column was an
    authorization bypass (`id IS NOT NULL OR \'\'` returns every private row)
    and, worse, a silent misfilter: `notes` has `created_by` as well as
    `author`, so the wrong one compiled and hid a note from its own writer."""
    # KeyError, not ValueError: app/main.py maps ValueError to 400, and the
    # table is a literal at every call site — a miss is our bug, so a 500
    v = scope.Viewer("ava", True)
    with pytest.raises(KeyError, match="carries no visibility tier"):
        scope.visible_filter(v, "users")
    with pytest.raises(KeyError, match="carries no visibility tier"):
        scope.visible_filter(v, "id IS NOT NULL OR ''")


def test_the_table_decides_the_author_column(fresh_db):
    """Four tables carry both their real author column and a `created_by`
    holding the agent slug. The mapping lives in CLASSIFIED and nowhere else."""
    v = scope.Viewer("ava", True)
    assert "notes.author = ?" in scope.visible_filter(v, "notes", alias="notes")[0]
    assert "created_by" not in scope.visible_filter(v, "notes", alias="notes")[0]
    assert "m.user = ?" in scope.visible_filter(v, "memories", alias="m")[0]
    assert "a.person = ?" in scope.visible_filter(v, "absences", alias="a")[0]


def test_a_weak_or_shared_identity_is_not_a_viewer(fresh_db):
    """The enforcement bar is STRONG identity (docs/VISIBILITY.md). Carried in
    the type, not at 95 call sites: `anonymous` and `agent` are shared
    fallbacks, and a weak name is whatever the caller typed."""
    for name, strong in [("ava", False), ("anonymous", True), ("agent", True)]:
        sql, params = scope.visible_filter(scope.Viewer(name, strong), "tasks")
        assert sql == "(visibility = ?)", (name, strong)
        assert params == [scope.WORKSPACE]
    sql, params = scope.visible_filter(scope.Viewer("anonymous", True), "tasks")
    assert sql == "(visibility = ?)"
    assert params == [scope.WORKSPACE]


def test_the_author_column_is_one_a_rename_moves(client, fresh_db):
    """The filter reads this column and rename_user must move it, or a renamed
    person loses every scoped row they wrote."""
    from app.services.users import _ATTRIBUTION

    for table, column in scope.CLASSIFIED.items():
        assert column in _ATTRIBUTION.get(table, ()), (
            f"scope.CLASSIFIED[{table!r}] = {column!r} is not in users._ATTRIBUTION."
            " A rename would strand every scoped row of that person."
        )
