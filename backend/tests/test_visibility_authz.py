"""Who may CHANGE a scoped row.

scope.visible_filter covers reading. It does nothing for a write, and every
mutation here finds its row by a caller-supplied id — `UPDATE notes SET ...
WHERE id = ?` matches a private note whoever asks. Ids are sequential small
integers, so this is enumeration, not obscurity.

The table below is the inventory: one entry per service function that mutates
a CLASSIFIED table by id. A new one added without an entry is what
test_every_id_addressed_mutation_is_listed catches.
"""

import ast

import pytest

from app import db
from app.services import (
    absences,
    blockers,
    collab,
    delegation,
    engagements,
    intake,
    memory,
    promises,
    schedule,
    scope,
    users,
    work,
)


@pytest.fixture
def crew_world(fresh_db):
    """ava owns a crew; bo is on the roster and in no crew. Every row below is
    scoped to ava's crew, so bo is a non-reader of all of them."""
    from app.services import crews

    for name in ("ava", "bo"):
        users.ensure_user(name)
    cid = crews.create_crew("Platform", actor="ava")["id"]
    return cid


def _seed(cid, n=0):
    """One crew row of every kind that has an id-addressed mutation. `n` keeps
    the engagement name unique — create_engagement refuses a duplicate, and
    the author test re-seeds once per call."""
    from app import ratelimit

    # the author test seeds once per mutation, and memory.remember is capped
    # at 10 writes a minute per person (app/ratelimit.py)
    ratelimit.reset()
    rows = {}
    k = {"actor": "ava", "visibility": "crew", "crew_id": cid}
    rows["task"] = work.create_task(title="crew task", **k)["id"]
    rows["note"] = collab.save_note("topic", "body", author="ava", **k)["id"]
    rows["question"] = collab.ask_question("crew question?", "", **k)["id"]
    rows["decision"] = collab.record_decision("crew call", "x", decided_by="ava", **k)["id"]
    rows["blocker"] = blockers.raise_blocker(title="crew blocker", impact="high", **k)["id"]
    rows["promise"] = promises.add_promise("crew promise", due_date="2026-12-01", **k)["id"]
    rows["request"] = intake.submit_request("crew request", detail="x", **k)["id"]
    rows["memory"] = memory.remember("crew memory", topic="t", user="ava", **k)["id"]
    rows["event"] = schedule.schedule_event("crew event", "2026-12-01T10:00", **k)["id"]
    rows["absence"] = absences.add_absence("ava", "2026-12-01", "2026-12-02", **k)["id"]
    name = f"crew engagement {n}"
    rows["engagement"] = engagements.create_engagement(name, **k)["id"]
    rows["milestone"] = work.create_milestone("crew milestone", name, **k)["id"]
    return rows


def _mutations(r):
    """(label, callable) for every id-addressed mutation on a scoped table.
    The callable takes the actor, so the same list drives the refusal case and
    the author case."""
    return [
        ("work.update_task", lambda a: work.update_task(r["task"], title="x", actor=a)),
        (
            "work.update_milestone",
            lambda a: work.update_milestone(r["milestone"], title="x", actor=a),
        ),
        ("collab.update_note", lambda a: collab.update_note(r["note"], topic="x", actor=a)),
        ("collab.delete_note", lambda a: collab.delete_note(r["note"], actor=a)),
        ("collab.assign_question", lambda a: collab.assign_question(r["question"], "ava", actor=a)),
        ("collab.answer_question", lambda a: collab.answer_question(r["question"], "yes", actor=a)),
        (
            "collab.supersede_decision",
            lambda a: collab.supersede_decision(
                r["decision"], "new", "x", decided_by="ava", actor=a
            ),
        ),
        ("collab.reconfirm_decision", lambda a: collab.reconfirm_decision(r["decision"], actor=a)),
        (
            "blockers.edit_blocker",
            lambda a: blockers.edit_blocker(r["blocker"], title="x", actor=a),
        ),
        ("blockers.resolve_blocker", lambda a: blockers.resolve_blocker(r["blocker"], actor=a)),
        (
            "promises.update_promise",
            lambda a: promises.update_promise(r["promise"], "kept", actor=a),
        ),
        (
            "promises.edit_promise",
            lambda a: promises.edit_promise(r["promise"], promise="x", actor=a),
        ),
        ("intake.edit_request", lambda a: intake.edit_request(r["request"], title="x", actor=a)),
        (
            "intake.score_request",
            lambda a: intake.score_request(r["request"], 3, 3, 3, 3, actor=a),
        ),
        (
            "intake.disposition_request",
            lambda a: intake.disposition_request(
                r["request"], disposition="declined", reason="no", actor=a, origin="human"
            ),
        ),
        ("memory.forget", lambda a: memory.forget(r["memory"], actor=a)),
        ("schedule.cancel_event", lambda a: schedule.cancel_event(r["event"], actor=a)),
        ("absences.delete_absence", lambda a: absences.delete_absence(r["absence"], actor=a)),
        (
            "engagements.update_engagement",
            lambda a: engagements.update_engagement(r["engagement"], summary="x", actor=a),
        ),
        (
            "delegation.delegate_task",
            lambda a: delegation.delegate_task(r["task"], "helper", "ava", actor=a),
        ),
    ]


def test_a_non_reader_cannot_change_a_crew_row(crew_world):
    """bo is on the roster and in no crew, so every row here is one bo cannot
    read. Reading it is already refused; changing it must be too."""
    r = _seed(crew_world)
    refused = []
    for label, call in _mutations(r):
        try:
            call("bo")
        except db.NotFound:
            refused.append(label)
        except Exception as exc:  # the point is WHICH exception, so catch broadly
            pytest.fail(f"{label} raised {type(exc).__name__}: {exc} — expected NotFound")
        else:
            pytest.fail(f"{label} let a non-reader change a crew row")
    assert len(refused) == len(_mutations(r))


def test_the_author_can_still_change_their_own_crew_row(crew_world):
    """The guard must not lock the crew out of its own work — a refusal that
    catches everyone is not a check, it is an outage."""
    # one fresh set per call: resolve, settle and disposition are terminal, and
    # delete_note removes the row a later entry in the list would edit
    for i in range(len(_mutations(dict.fromkeys(_KINDS, 1)))):
        label, call = _mutations(_seed(crew_world, i))[i]
        try:
            call("ava")
        except (db.NotFound, ValueError) as exc:
            pytest.fail(f"{label} refused the author: {type(exc).__name__}: {exc}")


def test_a_private_row_is_editable_by_its_author_alone(fresh_db):
    users.ensure_user("ava")
    users.ensure_user("bo")
    nid = collab.save_note("secret", "body", author="ava", actor="ava", visibility="private")["id"]
    with pytest.raises(db.NotFound):
        collab.delete_note(nid, actor="bo")
    assert fresh_db.query_one("SELECT COUNT(*) AS n FROM notes")["n"] == 1
    collab.delete_note(nid, actor="ava")
    assert fresh_db.query_one("SELECT COUNT(*) AS n FROM notes")["n"] == 0


def test_the_refusal_does_not_confirm_the_row_exists(fresh_db):
    """NotFound, not a refusal message. "you cannot edit #12" tells a caller
    that #12 exists and is scoped, which is the one fact a private row must
    not carry. The caller cannot read it either, so absent is honest."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    nid = collab.save_note("secret", "body", author="ava", actor="ava", visibility="private")["id"]
    with pytest.raises(db.NotFound) as scoped:
        collab.update_note(nid, topic="x", actor="bo")
    with pytest.raises(db.NotFound) as absent:
        collab.update_note(9999, topic="x", actor="bo")
    # the MESSAGE, not just the class: ids are sequential integers, so any
    # wording only the scoped case produces lets a caller walk 1..n and read
    # off which rows exist. Compared with the id normalised out.
    assert str(scoped.value).replace(f"#{nid}", "#N") == str(absent.value).replace("#9999", "#N")
    assert "secret" not in str(scoped.value)


def test_a_deleted_scoped_row_leaves_no_body_in_the_ledger(fresh_db):
    """The chain is append-only and anchored off-box, so a body written to
    activity.detail is written for good — there is no later redaction."""
    users.ensure_user("ava")
    nid = collab.save_note("topic", "ZZBODYZZ", author="ava", actor="ava", visibility="private")[
        "id"
    ]
    collab.delete_note(nid, actor="ava")
    mid = memory.remember(
        "ZZBODYZZ memory", topic="t", user="ava", actor="ava", visibility="private"
    )["id"]
    memory.forget(mid, actor="ava")
    rows = fresh_db.query("SELECT detail FROM activity")
    assert not [r for r in rows if "ZZBODYZZ" in r["detail"]]


def test_a_scoped_request_does_not_become_a_workspace_engagement(fresh_db):
    """The engagement's name IS the request's title and its summary IS the
    detail, so an accepted crew request would republish itself in full."""
    from app.services import crews

    users.ensure_user("ava")
    cid = crews.create_crew("Platform", actor="ava")["id"]
    rid = intake.submit_request(
        "crew idea", detail="body", actor="ava", visibility="crew", crew_id=cid
    )["id"]
    intake.disposition_request(
        rid, disposition="accepted", reason="yes", actor="ava", origin="human"
    )
    row = fresh_db.query_one("SELECT visibility, crew_id FROM engagements WHERE name = 'crew idea'")
    assert row == {"visibility": "crew", "crew_id": cid}


def test_a_superseded_crew_decision_keeps_its_tier(fresh_db):
    """The successor's default context is "Supersedes #N: {old title}", so a
    workspace replacement copies a crew title into a row everyone reads."""
    from app.services import crews

    users.ensure_user("ava")
    cid = crews.create_crew("Platform", actor="ava")["id"]
    did = collab.record_decision(
        "crew call", "x", decided_by="ava", actor="ava", visibility="crew", crew_id=cid
    )["id"]
    new = collab.supersede_decision(did, "replacement", "y", decided_by="ava", actor="ava")
    row = fresh_db.query_one("SELECT visibility, crew_id FROM decisions WHERE id = ?", (new["id"],))
    assert row == {"visibility": "crew", "crew_id": cid}


def test_a_scoped_blocker_gets_no_team_wide_funeral(fresh_db):
    """The funeral is addressed to "team" — every person on the roster — and
    it quotes the blocker's own title."""
    from datetime import UTC, datetime, timedelta

    from app.services import crews

    users.ensure_user("ava")
    cid = crews.create_crew("Platform", actor="ava")["id"]
    bid = blockers.raise_blocker(
        title="ZZSECRETZZ blocker", impact="high", actor="ava", visibility="crew", crew_id=cid
    )["id"]
    old = (datetime.now(UTC) - timedelta(days=9)).isoformat()
    fresh_db.execute("UPDATE blockers SET created_at = ? WHERE id = ?", (old, bid))
    blockers.resolve_blocker(bid, actor="ava")
    team = fresh_db.query("SELECT message FROM notifications WHERE user = 'team'")
    assert not [n for n in team if "ZZSECRETZZ" in n["message"]]


def _sql_text(node) -> str:
    """The literal text of one AST node, with every f-string hole rendered as
    `{the source expression}`.

    Walking bare Constants instead splits `f"DELETE FROM {t} WHERE ..."` into
    "DELETE FROM " and " WHERE ...", so the table name vanishes and the
    statement matches nothing — the exact shape that would slip past.

    The hole keeps its braces AND gains its expression, because the two scans
    need different halves of it. The mutation scan matches `{` to catch a table
    name it cannot resolve (`FROM {t}`). The read scan matches the expression
    to tell a spliced tier filter (`WHERE {WORKSPACE_ONLY}`) from any other
    interpolation — rendered as a bare `{}` the two are the same string, and
    every filtered read looks exactly like every leaking one.

    `+` concatenation is folded here too. Python merges ADJACENT literals at
    parse time but not operands of `+`, so `"SELECT ... FROM " + "tasks"`
    reaches this function as a BinOp whose halves each match nothing.
    """
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
            else "{" + ast.unparse(v.value) + "}"
            if isinstance(v, ast.FormattedValue)
            else ""
            for v in node.values
        )
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _sql_text(node.left) + _sql_text(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


def _statements(tree) -> list[tuple[str, str]]:
    """Every string in a module, paired with the function that owns it.

    Module-level SQL is owned by `<module>`. A scan that only walked
    FunctionDef bodies read `portfolio._WAIT_SATISFIED` — three SELECTs held
    in a module constant and spliced into queries below — as zero SQL, so a
    leak parked in a constant was invisible to both scans.

    A nested function is owned by its OUTERMOST enclosing function, because
    that is the name an exemption key has to spell. ast.walk is breadth-first,
    so the outer FunctionDef claims the node before any inner one reaches it.
    """
    owner: dict[int, str] = {}
    # a docstring is prose, never a query. Skipped by IDENTITY rather than by
    # content: the docstrings worth writing here are the ones that quote the
    # leaking shape they forbid, so matching on the text would silence exactly
    # the explanations this codebase asks for.
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            docstrings.add(id(first.value))
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            for n in ast.walk(fn):
                owner.setdefault(id(n), fn.name)
    # a rendered node consumes its own children: the Constant halves of
    # `f"SELECT * FROM tasks WHERE {frag}"` are "SELECT * FROM tasks WHERE "
    # and "", so scanning them SEPARATELY reports the table with its filter
    # torn off, and every correctly filtered read fails. ast.walk is
    # breadth-first, so the whole node is always reached before its pieces.
    consumed: set[int] = set()
    out = []
    for n in ast.walk(tree):
        if id(n) in consumed or id(n) in docstrings:
            continue
        sql = _sql_text(n)
        if not sql:
            continue
        for child in ast.walk(n):
            consumed.add(id(child))
        out.append((owner.get(id(n), "<module>"), sql))
    return out


def test_every_id_addressed_mutation_is_listed(fresh_db):
    """The inventory above is only as good as CI checking it. Every service
    function that UPDATEs or DELETEs a CLASSIFIED table by a caller-supplied
    id must appear in _mutations, or a new one ships unguarded and silent."""
    import pathlib
    import re

    listed = {label.split(".")[1] for label, _ in _mutations(dict.fromkeys(_KINDS, 1))}
    # ast, not a regex over the source: a regex has to guess where a function
    # body ends, and it put create_engagement's name on update_engagement's
    # UPDATE. Every false positive here reads as a real unguarded write.
    # a bare {} too: `f"UPDATE {table} SET ..."` (the shape at users.py) names
    # no table here, and matching only literal names would wave it through
    names = "|".join(scope.CLASSIFIED)
    # Three shapes, because SQLite writes a row three ways. UPDATE and DELETE
    # were the only ones matched, so `INSERT OR REPLACE INTO tasks` (which
    # deletes the row and re-inserts it) and `INSERT INTO tasks ... ON
    # CONFLICT(id) DO UPDATE` both walked past. The upsert shape is already in
    # this codebase — crews.add_member and search.index_record use it — so it
    # is the one a next author reaches for by example.
    stmt = re.compile(rf"\b(?:UPDATE|DELETE FROM)(?:\s+OR\s+\w+)?\s+(?:{names}|\{{)", re.I)
    # An upsert carries no WHERE at all, so the id gate below cannot apply to
    # it — the row is addressed by the conflict target or by an explicit id
    # column. Gated on `id` appearing somewhere in the statement, so an
    # `ON CONFLICT(name)` upsert is not claimed as id-addressed.
    upsert = re.compile(
        rf"\bINSERT\s+OR\s+REPLACE\s+INTO\s+(?:{names}|\{{)[\s\S]*?\bid\b"
        rf"|\bINSERT\s+INTO\s+(?:{names}|\{{)[\s\S]*?\bON\s+CONFLICT\b[\s\S]*?\bid\b"
        rf"|\bINSERT\s+INTO\s+(?:{names}|\{{)[\s\S]*?\bid\b[\s\S]*?\bON\s+CONFLICT\b",
        re.I,
    )
    # "id-addressed" read off the SQL, not off the signature. The arg-name
    # test (`endswith('_id')`) was a property of what the author called the
    # parameter: `def edit(row: int)` with `UPDATE tasks ... WHERE id = ?`
    # took a caller-supplied id and matched nothing.
    # `\bid`, and only after WHERE. Any `*_id` column matched create_engagement's
    # `UPDATE milestones SET engagement_id = ? WHERE project = ?` — a write keyed
    # on a NAME, which is the one shape this test must not claim. A word
    # boundary before `id` cannot match inside `engagement_id`, because the
    # preceding underscore is a word character.
    addressed = re.compile(r"\bWHERE\b[\s\S]*?\bid\s*(?:=\s*\?|IN\s*\()", re.I)
    missing = []
    seen: set[str] = set()
    # anchored on this file, not the CWD: `pytest backend/tests/...` from the
    # repo root made the glob empty and the test vacuously green
    services = pathlib.Path(__file__).resolve().parent.parent / "app" / "services"
    assert services.is_dir(), services
    for path in sorted(services.glob("*.py")):
        if path.name in _EXEMPT_FILES:
            continue
        tree = ast.parse(path.read_text())
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                seen.add(f"{path.name}::{fn.name}")
        # _statements, not a walk of FunctionDef bodies: SQL held in a module
        # constant and spliced into a query below belongs to no function, and
        # the body walk read it as zero SQL (portfolio._WAIT_SATISFIED is
        # three real SELECTs that were invisible to both scans).
        for fname, sql in _statements(tree):
            if not (stmt.search(sql) and addressed.search(sql)) and not upsert.search(sql):
                continue
            key = f"{path.name}::{fname}"
            if fname not in listed and key not in _EXEMPT_FUNCTIONS:
                missing.append(key)
    assert not missing, (
        f"unlisted id-addressed mutations on a scoped table: {missing}."
        " Add scope.assert_editable and an entry in _mutations, or name it in"
        " _EXEMPT_FUNCTIONS with the reason."
    )
    # the other direction. An exemption that names no live function is a
    # carve-out nobody can audit — "submit_for_acceptance" named nothing at
    # all, and a bare "delete_note" exempted private_notes.delete_note too
    assert not set(_EXEMPT_FUNCTIONS) - seen, (
        f"exemptions naming no function: {sorted(set(_EXEMPT_FUNCTIONS) - seen)}."
        " Delete the entry, or fix the file::function key."
    )


_KINDS = (
    "task",
    "note",
    "question",
    "decision",
    "blocker",
    "promise",
    "request",
    "memory",
    "event",
    "absence",
    "engagement",
    "milestone",
)

# Files whose writes are never addressed by a caller-supplied id.
_EXEMPT_FILES = {
    "admin.py",  # the export/purge surface, gated on AdminUser and enumerated in admin.TABLES
    "digest.py",  # upserts its own artifact row, keyed on the file path
    "readout.py",
    "rituals.py",
}

# name -> why the guard does not belong. An absence with no reason reads as an
# oversight to the next reader (CLAUDE.md).
_EXEMPT_FUNCTIONS = {
    "work.py::_update_task_locked": (
        "the public update_task wrapper owns the transaction and inventory entry;"
        " this private implementation performs its guarded SQL inside that boundary"
    ),
    # the id is the row this call just created, not a row the caller named
    "insights.py::convert_finding": "sets source_finding_id on the row it just inserted",
    "blockers.py::raise_blocker": "flips the task it was given to blocked",
    # the delegated-agent identity is the gate, and it is stricter than the tier
    "delegation.py::claim_task": "refuses any actor that is not the task's delegated_agent",
    "delegation.py::accept_completion": "same check, on the sponsor's verdict path",
    "delegation.py::report_progress": "same delegated_agent check",
    # keyed on something other than a row id
    "ci.py::ci_event": "resolves the blocker from its own SELECT over a webhook payload",
    "weekly.py::apply_plan": "task_ids come from the caller, each routed through update_task",
    "blockers.py::_sweep_escalations_locked": (
        "the public sweep owns one transaction over every open blocker;"
        " this private implementation updates only rows selected by that job"
    ),
    "promises.py::_chase_received_locked": (
        "the public chase owns one transaction over every overdue promise;"
        " this private implementation updates only rows selected by that job"
    ),
    "schedule.py::record_outcome": (
        "guards with scope.assert_editable, and is a flag on the event the"
        " caller named rather than a content write"
    ),
    "collab.py::_assign_question_locked": (
        "the public assign_question wrapper owns the transaction; this private"
        " implementation performs its guarded SQL inside that boundary"
    ),
    "collab.py::_answer_question_locked": (
        "the public answer_question wrapper owns the transaction; this private"
        " implementation performs its guarded SQL inside that boundary"
    ),
    "collab.py::_sweep_stale_decisions_locked": (
        "the public sweep owns one transaction over every active decision"
    ),
    "engagements.py::_ship_it_locked": "keyed on the engagement update_engagement just closed",
    "engagements.py::create_engagement": (
        "adopts milestones that match the new engagement name; each updated id"
        " comes from the actor-visible candidate query in the same transaction"
    ),
    "engagements.py::_experiment_lesson": "same",
    "intake.py::_disposition": "the public disposition_request guards, then calls this",
    "private_notes.py::delete_note": "the author-private journal in its own private.db file",
    "handoff.py::generate_handoff": (
        "the engagement is filtered through the caller's viewer before anything"
        " runs, and the artifact row updated is the one this call resolved by"
        " PATH inside that engagement's own directory — not a row id a caller"
        " named. It is an upsert of a file this call just overwrote, so the row"
        " and the file stay one to one"
    ),
}


def test_recall_answers_one_person_out_of_their_own_memories(fresh_db):
    """memory_prompt injects whatever recall returns into a system prompt,
    where nothing later tells the asker's own memories from anyone else's.
    Both axes — the user and the tier — apply to every branch."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    memory.remember("ava likes rust", user="ava", actor="ava")
    memory.remember("bo likes go", user="bo", actor="bo")
    memory.remember("the standup is at 9", user="", actor="ava")

    # the query branch used to apply neither filter
    got = {m["content"] for m in memory.recall("likes", user="ava")}
    assert got == {"ava likes rust"}
    # the list branch keeps the team-wide memory
    got = {m["content"] for m in memory.recall(user="ava")}
    assert got == {"ava likes rust", "the standup is at 9"}


def test_a_private_memory_never_reaches_a_system_prompt(fresh_db):
    """The agent path passes NOBODY, so a private memory is not injected even
    for its own author — recall has no strong identity to check there."""
    users.ensure_user("ava")
    memory.remember("ZZSECRETZZ", user="ava", actor="ava", visibility="private")
    assert "ZZSECRETZZ" not in memory.memory_prompt("ava")


def test_a_private_capture_that_reads_as_a_blocker_still_lands(fresh_db):
    """capture.py hardcodes owner=actor, so without the author self-exemption
    in assert_readable_by every private capture that classified as a blocker
    was refused — naming a remedy ("leave the owner empty") the caller has no
    way to take."""
    from app.services import capture

    users.ensure_user("ava")
    out = capture.capture("blocked on the vendor contract", actor="ava", visibility="private")
    row = fresh_db.query_one("SELECT visibility, owner FROM blockers")
    assert out and row == {"visibility": "private", "owner": "ava"}


def test_a_private_standup_with_blockers_text_is_not_rolled_back(fresh_db):
    """post_standup forks its blockers text into a blocker with owner=author,
    inside the standup's own transaction — a refusal there loses the standup
    too, not just the blocker."""
    users.ensure_user("ava")
    collab.post_standup(
        "ava", today="shipped", blockers="vendor will not sign", actor="ava", visibility="private"
    )
    assert fresh_db.query_one("SELECT COUNT(*) AS n FROM standups")["n"] == 1
    assert fresh_db.query_one("SELECT visibility FROM blockers")["visibility"] == "private"


def test_a_private_task_can_be_assigned_to_its_own_author(fresh_db):
    users.ensure_user("ava")
    t = work.create_task(title="mine", assignee="ava", actor="ava", visibility="private")
    assert fresh_db.query_one("SELECT assignee FROM tasks WHERE id = ?", (t["id"],)) == {
        "assignee": "ava"
    }


def test_a_machine_actor_works_a_crew_row_but_never_a_private_one(fresh_db):
    """review.approve_change applies with actor=proposed_by (an agent slug,
    never the approving human) and the forge webhook passes actor="forge".
    Refusing those turned every agent proposal against a crew row into a
    permanent auto-reject that told the reviewer the row had vanished."""
    from app.services import crews

    users.ensure_user("ava")
    users.ensure_user("scout", kind="agent")
    cid = crews.create_crew("Platform", actor="ava")["id"]

    crew_task = work.create_task(title="crew work", actor="ava", visibility="crew", crew_id=cid)
    work.update_task(crew_task["id"], status="in_progress", actor="forge")
    work.update_task(crew_task["id"], priority="high", actor="scout")

    # private is the author alone, and nothing ever hands an agent private work
    mine = work.create_task(title="mine", actor="ava", visibility="private")
    with pytest.raises(db.NotFound):
        work.update_task(mine["id"], status="in_progress", actor="scout")
    with pytest.raises(db.NotFound):
        work.update_task(mine["id"], status="in_progress", actor="forge")


def test_a_scoped_absence_is_filed_for_a_person_who_can_read_it(fresh_db):
    """CLASSIFIED keys absences on `person`, not the filer — a private absence
    filed FOR someone else is readable by nobody and deletable by nobody,
    while it still moves that person's capacity."""
    users.ensure_user("ava")
    users.ensure_user("bo")
    absences.add_absence("ava", "2026-12-01", "2026-12-02", actor="ava", visibility="private")
    with pytest.raises(ValueError, match="means one reader"):
        absences.add_absence("bo", "2026-12-01", "2026-12-02", actor="ava", visibility="private")


# ---------------------------------------------------------------------------
# The read side. test_every_id_addressed_mutation_is_listed covers mutations,
# and it found almost nothing — the reads are where the leaks were: nine of
# them in one review round, every one a place somebody had to remember.
#
# A read of a CLASSIFIED table has to do one of three things: take a `viewer`,
# splice scope.WORKSPACE_ONLY, or appear below with a written reason.
# ---------------------------------------------------------------------------

# file::function -> why this read needs no tier filter.
_UNFILTERED_READS = {
    "policy_context.py::resource_row": (
        "loads one typed notification source inside the notification write"
        " transaction. The source-row builder and saved policy context use"
        " that snapshot; every public reader then requires saved and current"
        " policy permission before it returns the body"
    ),
    "policy_context.py::opaque_project_contexts": (
        "policy-only input inventory for aggregates that intentionally count"
        " hidden tiers. It returns no row to a caller; hidden attributes go"
        " only to the composed policy engine, which can fail the aggregate"
        " closed"
    ),
    "work.py::_visible_link": (
        "reads only tier metadata after the actor-visible id probe succeeds,"
        " inside the task write transaction; no row data is returned to the caller"
    ),
    "work.py::_assert_task_relationships": (
        "reads one validated milestone's parent id only to enforce audience"
        " containment; hidden parents receive the same missing-milestone refusal"
    ),
    "policy_context.py::existing": (
        "reads only classification and project type for one exact resource id."
        " The policy gate uses these values to restrict the operation and never"
        " returns them to the caller. Applying the caller visibility filter here"
        " would remove the data that a stronger workplace policy must inspect"
    ),
    "policy_context.py::_task_context": (
        "resolves only classification and coherent project context for one"
        " task policy decision; conflicting relationships fail closed and no"
        " row data is returned to the caller"
    ),
    "policy_context.py::_blocker_context": (
        "resolves only a blocker's classification and linked task id for the"
        " durable review revalidation path. Actor-facing REST and agent gates"
        " perform their own visible or editable probe before policy"
    ),
    "policy_context.py::_engagement_project_type": (
        "reads only the project class for one exact relationship target. The"
        " value goes only to policy evaluation and can only restrict the call"
    ),
    "policy_context.py::_milestone_engagement": (
        "resolves one exact milestone to an engagement id for target-state policy."
        " Neither id nor content is returned to the caller"
    ),
    "policy_context.py::_named_engagement": (
        "resolves the exact project name supplied to milestone creation. The id"
        " goes only to policy evaluation and is not returned"
    ),
    "policy_context.py::_target_engagement": (
        "reads only relationship ids for the exact resource being changed. It"
        " computes the target state for policy and returns no data to the caller"
    ),
    "promises.py::_chase_received_locked": (
        "a job, so no viewer exists. It reads every tier ON PURPOSE: the"
        " personal nudge goes to the row's own author and leaks nothing at any"
        " tier. What LEAVES is guarded twice — the team-wide escalation fires"
        " only for a workspace-tier row, and it names no party, because"
        " `to_whom` is free text that nothing stops from being a teammate"
    ),
    "playbooks.py::_exists": (
        "returns one BIT and never a column. close_out_diff uses it only to"
        " tell a deleted row from one hidden from this caller, because those"
        " two must not produce the same sentence — a hidden ritual reported as"
        " skipped is both a leak and a false claim. Split out under its own"
        " name so this entry cannot excuse close_out_diff's real reads"
    ),
    "playbooks.py::_snapshot": (
        "reads back the rows instantiate JUST created, by id, inside its own"
        " transaction — there is no viewer at kickoff, and the tier of a row"
        " this function itself wrote cannot exclude it from its own plan"
    ),
    "playbooks.py::snapshot_for": (
        "reads one artifacts row for a PATH and nothing else, and the path is"
        " useless on its own. close_out_diff is the caller that opens it, and"
        " it re-reads every id through a viewer filter AND refuses the whole"
        " diff when any row comes back hidden — a snapshot title is never"
        " emitted on the strength of the snapshot alone"
    ),
    "engagements.py::_playbook_lesson": (
        "reads the engagement the caller is CLOSING, which update_engagement"
        " already proved editable by this actor. It takes name, project_class"
        " and the tier itself, and copies that tier onto the drafted proposal"
    ),
    # --- aggregates and counts: no row's own text leaves the function ---
    "delegation.py::mission_control": "COUNT per agent, plus a MAX(created_at)",
    "pulse.py::standup_chain": "counts standup days, never their text",
    "pulse.py::blocker_speedrun": "resolution times by impact, no titles",
    "pulse.py::pulse": "season counters over the same aggregates",
    "onboarding.py::checklist": "COUNT per entity, to decide which step is done",
    "delegation.py::list_worklog": (
        "the `party` branch only, and it is gated per task on that task's own"
        " delegated_agent/sponsor columns — the same two identities"
        " report_progress lets WRITE there. It exists because an agent holds"
        " no crew membership, so the tier filter refused a crew worklog the"
        " agent was writing. A private task can never carry a delegate"
        " (delegate_task refuses one), so this reaches crew and workspace"
        " rows only. The non-party branch below is filtered."
    ),
    "portfolio.py::capacity_ahead": (
        "TWO reads, and this scanner keys on the FUNCTION, so both need the"
        " reason here. Allocations: percent per person per week plus the"
        " engagement NAME, masked by scope.visible_name against the caller's"
        " viewer — the same treatment allocation_conflicts gives that column."
        " Absences: person and dates are the honest core of unavailability,"
        " and the KIND is masked to 'away' on any non-workspace row, matching"
        " absences.away_today. Reading only the first query is how the"
        " absence leak got here — visible_name anywhere in a body satisfies"
        " this scan for every other query in it."
    ),
    "insights.py::forecast_calibration": (
        "julianday differences between a snapshot's forecast_date and a"
        " milestone's completed_at. No title, no id, no name — the same shape"
        " as slip_forecast below, which reads the same table"
    ),
    "portfolio.py::slip_forecast": (
        "the slip HISTORY: one julianday difference per done milestone, no"
        " title and no id. The open-milestone list beside it is filtered."
    ),
    "portfolio.py::what_if": "SUM(percent) per person, to project capacity",
    "portfolio.py::<module>": (
        "_WAIT_SATISFIED asks whether ids the caller ALREADY holds have"
        " cleared. It returns those same ids and no other column, and"
        " slip_forecast now only ever hands it workspace ones."
    ),
    "insights.py::automation_ratio": "counts rows per month per origin, over `{t}`",
    "fieldguide.py::<module>": (
        "the knot probes: SELECT 1 ... WHERE <the reader's own name>. Each"
        " answers 'have you done this yet' about the reader, and returns no"
        " column from the row that proves it."
    ),
    # --- the row's OWN reader: this is the person or agent the row is for ---
    "review.py::_sponsor_of": "reads the one column that names who reviews it",
    "review.py::_governing_tier": (
        "reads the tier that decides who may see or judge a proposal. It IS"
        " the filter for pending_changes, which carries no tier of its own"
    ),
    "blockers.py::resolve_blocker": (
        "the tasks waiting on this blocker, to tell their assignees it cleared."
        " An assignee is a name work.py:186 and work.py:348 already checked as"
        " a reader (assert_readable_by), so the title goes to somebody who can"
        " open it — the same rule sweep_escalations follows."
    ),
    "search.py::visible_hits": (
        "reads the tier columns for a page of hits, batched by table, and then"
        " applies scope.can_read to each — it IS the filter for FTS results,"
        " which carry no tier of their own"
    ),
    "search.py::_tier_of": "reads the tier itself — the thing every filter asks for",
    "search.py::_is_private": "same, and it is the guard that keeps a private row unindexed",
    # --- write paths: the SELECT feeds the guard, not a response ---
    "engagements.py::create_engagement": "reads its own name, NOCASE, to refuse a duplicate",
    "engagements.py::update_engagement": "same duplicate-name check, excluding itself",
    "context_pack.py::_crew_section": "filters on `visibility = 'crew' AND crew_id = ?` itself",
    # --- keyed on a row the caller did not name ---
    "ci.py::ci_event": "resolves its blocker from a webhook payload, not an id",
    "forge.py::forge_event": "resolves its task from a branch or PR string",
    "delegation.py::claim_task": "the actor must BE the task's delegated_agent",
    "delegation.py::report_progress": "the actor must be the delegate or the sponsor",
    "delegation.py::accept_completion": "same delegated_agent check",
    "delegation.py::submit_completion": "same delegated_agent check",
    "engagements.py::_ship_it_locked": "keyed on the engagement update_engagement just closed",
    "engagements.py::_experiment_lesson": "same",
    # --- jobs that carry their own rule ---
    "blockers.py::_sweep_escalations_locked": (
        "escalates every tier on purpose — a crew blocker that silently never"
        " escalates is worse than one nobody is told about. The notify and the"
        " ledger line inside it ARE tier-gated."
    ),
    "collab.py::_sweep_stale_decisions_locked": "same rule, same two gates inside",
    "digest.py::publish_digest": "upserts its own artifact row, keyed on the file path",
    "rituals.py::_write_artifact": "same",
    "readout.py::exec_readout": "same — and its body is built from filtered readers",
    # --- deliberate carve-outs, argued where the code lives ---
    "absences.py::away_today": "capacity must be honest — see the comment there",
    "absences.py::weekday_overlap": "same",
    "retention.py::prune": (
        "its orphan-reaping NOT IN subqueries decide what to DELETE, so a"
        " filter there does not hide rows — it deletes live ones"
    ),
}


def test_every_read_of_a_scoped_table_is_filtered_or_excused(fresh_db):
    """The mirror of test_every_id_addressed_mutation_is_listed, for reads.

    Nine leaks in one review round were all this shape: a SELECT on a scoped
    table in a function with no viewer and no WORKSPACE_ONLY. Four of them sat
    under a comment claiming the table carried no settable tier — true when
    written, false by the time it shipped. A comment cannot hold this; CI can.
    """
    import pathlib
    import re

    tables = "|".join(scope.CLASSIFIED)
    # `|\{` for the same reason the mutation scan carries it: `FROM {t}` names
    # no table here, and matching literal names only waves the whole shape past
    reads = re.compile(rf"\b(?:FROM|JOIN)\s+(?:{tables}|\{{)", re.I)
    # prose says "from" too. "Auto-extracted from {author}'s standup" matched
    # `FROM {` and reported post_standup as an unfiltered read of every table.
    # A false positive here costs more than a miss: it teaches the next author
    # that an entry in _UNFILTERED_READS is the way to make this test quiet.
    is_sql = re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE)\b", re.I)
    # the tier filter as it appears in SQL: the WORKSPACE_ONLY constant, a
    # visible_filter call spliced inline, or the name a caller bound its
    # fragment to. Bare `viewer` is NOT proof — a function can take the
    # parameter and never use it, which is a leak that reads as filtered.
    #
    # visible_name counts, and it is the one entry here that MASKS rather than
    # excludes: the rows all stay and one column is replaced. It is accepted
    # because the capacity surfaces have to sum every tier to stay honest (see
    # scope.visible_name). Reach for it only when the row's existence is
    # already public and its NAME is the secret — on any other query it
    # answers this scan while leaking every other column.
    filtered = re.compile(r"\b(?:WORKSPACE_ONLY|visible_filter|visible_name)\b")
    by_id = re.compile(r"\bWHERE\s+\w*\.?id\s*=\s*\?")
    services = pathlib.Path(__file__).resolve().parent.parent / "app" / "services"
    assert services.is_dir(), services
    unfiltered: list[str] = []
    still_needed: set[str] = set()
    for path in sorted(services.glob("*.py")):
        # NOT _EXEMPT_FILES: that set excuses digest.py and rituals.py from the
        # MUTATION scan, because their only writes are artifact upserts keyed
        # on a file path. Their reads are the egress builders this test exists
        # for. admin.py is the one file excused here — it is the whole-database
        # export surface, gated on AdminUser and enumerated in admin.TABLES.
        if path.name == "admin.py":
            continue
        tree = ast.parse(path.read_text())
        # the names a tier fragment was bound to, per function. `frag, vp =
        # scope.visible_filter(...)` and the dict comprehension in
        # private_notes.one_on_one_brief both splice by NAME, so the statement
        # reads `WHERE {frag}` and the call itself is nowhere in it.
        bound: dict[str, set[str]] = {}
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            names: set[str] = set()

            def carries(src: str, names: set[str] = names) -> bool:
                return bool(filtered.search(src)) or any(re.search(rf"\b{n}\b", src) for n in names)

            # to a fixpoint, because the fragment reaches the query through
            # intermediates: list_decisions does `frag, vp = visible_filter(...)`
            # and then `where, params = [frag], list(vp)`, so the statement
            # splices `where` and the call appears nowhere near it. One hop is
            # not enough — the second assignment names only the first.
            changed = True
            while changed:
                changed = False
                for node in ast.walk(fn):
                    if isinstance(node, ast.Assign) and carries(ast.unparse(node.value)):
                        for t in node.targets:
                            new = {n.id for n in ast.walk(t) if isinstance(n, ast.Name)}
                            if new - names:
                                names |= new
                                changed = True
                    elif (
                        isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("append", "extend", "add", "insert")
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id not in names
                        and carries(" ".join(ast.unparse(a) for a in node.args))
                    ):
                        names.add(node.func.value.id)
                        changed = True
            bound.setdefault(fn.name, set()).update(names)
        # a function that guards with assert_editable reads its row to feed the
        # guard. That escape is per STATEMENT and only for a read keyed on the
        # id the guard then checks — held at function level it excused every
        # other query beside it, which is how the leaks got in.
        guarded = {
            fn.name
            for fn in ast.walk(tree)
            if isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef)
            and "assert_editable" in {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
        }
        for fname, sql in _statements(tree):
            if not reads.search(sql) or not is_sql.search(sql):
                continue
            key = f"{path.name}::{fname}"
            if filtered.search(sql) or any(
                re.search(rf"\b{n}\b", sql) for n in bound.get(fname, ())
            ):
                continue
            if fname in guarded and by_id.search(sql):
                continue
            if key not in _UNFILTERED_READS:
                unfiltered.append(key)
            still_needed.add(key)
    assert not unfiltered, (
        f"reads of a scoped table with no viewer and no WORKSPACE_ONLY: {unfiltered}."
        " Take a viewer, splice scope.WORKSPACE_ONLY, or add a written reason"
        " to _UNFILTERED_READS."
    )
    # An excuse for a read that IS filtered now is the same rot this test
    # exists to catch, one level up: it reads as a live carve-out and the next
    # author trusts it. list_artifacts and generate_handoff both sat here until
    # they took a viewer.
    assert not set(_UNFILTERED_READS) - still_needed, (
        f"excuses no longer needed: {sorted(set(_UNFILTERED_READS) - still_needed)}."
        " The function is filtered now, or it is gone. Delete the entry."
    )


def test_a_crew_pack_carries_the_crew_rows_and_reaches_members_only(fresh_db):
    """The shared body stays a PREFIX of the crew pack, so a reader
    can tell which half the whole roster already has."""
    from app.services import context_pack, crews

    users.ensure_user("ava")
    users.ensure_user("bo")
    cid = crews.create_crew("Platform", actor="ava")["id"]
    collab.record_decision(
        "ZZCREWZZ call", "x", decided_by="ava", actor="ava", visibility="crew", crew_id=cid
    )
    collab.record_decision("open call", "y", decided_by="ava", actor="ava")

    ava, bo = scope.Viewer("ava", True), scope.Viewer("bo", True)
    team = context_pack.get_pack(actor="ava", viewer=ava)["content"]
    crew_pack = context_pack.get_pack(actor="ava", crew_id=cid, viewer=ava)["content"]
    assert "ZZCREWZZ" not in team and "open call" in team
    assert "ZZCREWZZ" in crew_pack and "Platform only" in crew_pack

    # a non-member gets the same answer as somebody naming a crew that is gone
    with pytest.raises(db.NotFound):
        context_pack.get_pack(actor="bo", crew_id=cid, viewer=bo)

    # and so does a member the server cannot identify. In trusted-header mode
    # the name is whatever the caller typed, so gating on `actor` alone let a
    # rewritten X-User header read any crew's decisions and conventions.
    with pytest.raises(db.NotFound):
        context_pack.get_pack(actor="ava", crew_id=cid, viewer=scope.Viewer("ava", False))
    # the TEAM pack is workspace content and stays open to a weak caller
    assert context_pack.get_pack(actor="ava", viewer=scope.NOBODY)["content"]


def test_publishing_a_crew_pack_is_no_weaker_a_door_than_reading_one(fresh_db):
    """Publish takes the crew id off a query string, writes that crew's rows
    to an artifact file and bumps the version every member cites. Gated on a
    self-asserted name while the READ was gated on strong identity, it was
    simply the weaker door to the same rows."""
    from app.services import context_pack, crews

    users.ensure_user("ava")
    users.ensure_user("bo")
    cid = crews.create_crew("Platform", actor="ava")["id"]
    ava = scope.Viewer("ava", True)

    with pytest.raises(db.NotFound):  # a member the server cannot identify
        context_pack.publish_pack(actor="ava", crew_id=cid, viewer=scope.Viewer("ava", False))
    with pytest.raises(db.NotFound):  # and a non-member
        context_pack.publish_pack(actor="bo", crew_id=cid, viewer=scope.Viewer("bo", True))
    assert context_pack.publish_pack(actor="ava", crew_id=cid, viewer=ava)["version"] == 1
    # the TEAM pack stays open — it carries workspace rows only
    assert context_pack.publish_pack(actor="scheduler")["version"] >= 1


def test_each_crew_versions_its_pack_independently(fresh_db):
    """A shared counter would bump every crew's version whenever any one of
    them published, and the version is what a reader cites."""
    from app.services import context_pack, crews

    users.ensure_user("ava")
    a = crews.create_crew("Alpha", actor="ava")["id"]
    b = crews.create_crew("Beta", actor="ava")["id"]
    ava = scope.Viewer("ava", True)
    assert context_pack.get_pack(actor="ava", viewer=ava)["version"] == 1
    assert context_pack.get_pack(actor="ava", crew_id=a, viewer=ava)["version"] == 1
    assert context_pack.get_pack(actor="ava", crew_id=b, viewer=ava)["version"] == 1
    collab.record_decision("new", "x", decided_by="ava", actor="ava", visibility="crew", crew_id=a)
    assert context_pack.publish_pack(actor="ava", crew_id=a, viewer=ava)["version"] == 2
    assert context_pack.latest_pack(b)["version"] == 1
    assert context_pack.latest_pack(0)["version"] == 1


def test_a_crew_pack_is_a_separate_artifact_file(fresh_db):
    """Two crews at v3 would otherwise overwrite one file, and the artifact
    row would name the wrong pack."""
    import pathlib

    from app import config
    from app.services import context_pack, crews

    users.ensure_user("ava")
    a = crews.create_crew("Alpha", actor="ava")["id"]
    context_pack.publish_pack(actor="ava")
    context_pack.publish_pack(actor="ava", crew_id=a, viewer=scope.Viewer("ava", True))
    names = {
        p.name for p in (pathlib.Path(config.DATA_DIR) / "artifacts" / "context-pack").glob("*")
    }
    assert names == {"context-pack-v1.md", f"context-pack-crew{a}-v1.md"}


def test_a_crew_member_can_open_the_pack_for_their_own_engagement(fresh_db):
    """Locked to the workspace tier, a member saw their engagement in
    GET /api/engagements and got "not found" asking for its pack — a correct
    refusal with a misleading sentence."""
    from app.services import context_pack, crews, engagements

    users.ensure_user("ava")
    users.ensure_user("bo")
    cid = crews.create_crew("Platform", actor="ava")["id"]
    crews.add_member(cid, "bo", actor="ava")
    eng = engagements.create_engagement("ZZCREWENGZZ", actor="ava", visibility="crew", crew_id=cid)
    member, outsider = scope.Viewer("bo", True), scope.Viewer("cass", True)

    assert "ZZCREWENGZZ" in context_pack.build_engagement_pack(eng["id"], member)
    with pytest.raises(db.NotFound):
        context_pack.build_engagement_pack(eng["id"], outsider)
    # and a surface with no human behind it still reads the workspace tier
    with pytest.raises(db.NotFound):
        context_pack.build_engagement_pack(eng["id"])


def test_a_handoff_artifact_carries_its_engagements_tier(fresh_db):
    """The row holds a PATH to a markdown file of the engagement's work, and
    list_artifacts serves rows to everyone."""
    from app.services import crews, engagements, handoff

    users.ensure_user("ava")
    cid = crews.create_crew("Platform", actor="ava")["id"]
    eng = engagements.create_engagement("scoped", actor="ava", visibility="crew", crew_id=cid)
    open_eng = engagements.create_engagement("open", actor="ava")
    author = scope.Viewer("ava", True)
    handoff.generate_handoff(eng["id"], actor="ava", viewer=author)
    handoff.generate_handoff(open_eng["id"], actor="ava", viewer=author)

    row = fresh_db.query_one(
        "SELECT visibility, crew_id FROM artifacts WHERE title LIKE '%scoped%'"
    )
    assert row == {"visibility": "crew", "crew_id": cid}
    titles = lambda v: sorted(a["title"] for a in handoff.list_artifacts(viewer=v))  # noqa: E731
    assert titles(author) == ["Handoff — open", "Handoff — scoped"]
    assert titles(scope.Viewer("bo", True)) == ["Handoff — open"]
    assert titles(scope.NOBODY) == ["Handoff — open"]


def _key(owner):
    from app.services.api_keys import create_key

    users.ensure_user(owner)
    return {"Authorization": f"Bearer {create_key(owner, 'k')['key']}"}


def test_a_milestone_and_an_event_take_a_tier_over_rest(client, fresh_db):
    """Both services accepted one before any surface offered it. Neither has a
    create form in this UI — REST, the CLI and the agent tools are the whole
    surface."""
    users.ensure_user("ava")
    from app.services import crews

    cid = crews.create_crew("Platform", actor="ava")["id"]
    ava = _key("ava")
    client.post(
        "/api/milestones",
        json={"title": "scoped milestone", "visibility": "crew", "crew_id": cid},
        headers=ava,
    )
    client.post(
        "/api/events",
        json={
            "title": "scoped event",
            "starts_at": "2026-12-01T10:00",
            "visibility": "private",
        },
        headers=ava,
    )
    assert fresh_db.query_one("SELECT visibility, crew_id FROM milestones") == {
        "visibility": "crew",
        "crew_id": cid,
    }
    assert fresh_db.query_one("SELECT visibility FROM events") == {"visibility": "private"}
