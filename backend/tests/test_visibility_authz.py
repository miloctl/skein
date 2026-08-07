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
    `{}`. Walking bare Constants instead splits `f"DELETE FROM {t} WHERE ..."`
    into "DELETE FROM " and " WHERE ...", so the table name vanishes and the
    statement matches nothing — the exact shape that would slip past."""
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{}"
            for v in node.values
        )
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return ""


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
    stmt = re.compile(rf"\b(?:UPDATE|DELETE FROM)(?:\s+OR\s+\w+)?\s+(?:{names}|\{{)", re.I)
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
        for fn in [
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
        ]:
            args = fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
            seen.add(f"{path.name}::{fn.name}")
            # crew_id is the tier the WRITE sets, not a row the caller names
            if not any(a.arg.endswith("_id") and a.arg != "crew_id" for a in args):
                continue  # takes no row id from its caller
            # per NODE, never joined: Python merges adjacent string literals
            # into one Constant (or one JoinedStr) at parse time, so a whole
            # statement is always a single node. Joining them instead let
            # prose in one string ("... create or update") run into an f-string
            # hole in another and match as `UPDATE {`.
            hit = any(stmt.search(_sql_text(n)) for n in ast.walk(fn))
            key = f"{path.name}::{fn.name}"
            if hit and fn.name not in listed and key not in _EXEMPT_FUNCTIONS:
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
    "blockers.py::sweep_escalations": "a job over every open blocker, not one a caller named",
    "collab.py::sweep_stale_decisions": "a job over every active decision",
    "engagements.py::_ship_it": "keyed on the engagement update_engagement just closed",
    "engagements.py::_experiment_lesson": "same",
    "intake.py::_disposition": "the public disposition_request guards, then calls this",
    "private_notes.py::delete_note": "the author-private journal in its own private.db file",
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
    with pytest.raises(ValueError, match="readable by nobody else"):
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
    # --- aggregates and counts: no row's own text leaves the function ---
    "delegation.py::mission_control": "COUNT per agent, plus a MAX(created_at)",
    "pulse.py::standup_chain": "counts standup days, never their text",
    "pulse.py::blocker_speedrun": "resolution times by impact, no titles",
    "pulse.py::pulse": "season counters over the same aggregates",
    "onboarding.py::checklist": "COUNT per entity, to decide which step is done",
    "engagements.py::capacity": "percent math over allocations",
    "engagements.py::allocate": "percent math, and the overlap check it refuses on",
    "engagements.py::list_allocations": "allocations rows — the table is UNSCOPED",
    "usage.py::engagement_costs": "token spend per engagement id",
    "portfolio.py::allocation_conflicts": "SUM(percent) per person",
    # --- the row's OWN reader: this is the person or agent the row is for ---
    "delegation.py::agent_inbox": (
        "one agent's own delegated tasks and assigned questions. Nothing hands"
        " an agent private work (assert_readable_by refuses a private assignee),"
        " and a crew task delegated to an agent is work that agent must see."
    ),
    "review.py::_sponsor_of": "reads the one column that names who reviews it",
    # --- write paths: the SELECT feeds the guard, not a response ---
    "work.py::create_task": "reads the milestone/engagement it links to, to refuse a bad id",
    "work.py::create_milestone": "reads the engagement it links to, by name",
    "promises.py::add_promise": "reads the engagement it links to, to refuse a bad id",
    "blockers.py::raise_blocker": "reads the task it links to, to refuse a bad id",
    "engagements.py::create_engagement": "reads its own name, NOCASE, to refuse a duplicate",
    "engagements.py::record_lesson": "reads the engagement it links to, to refuse a bad id",
    "context_pack.py::_crew_section": "filters on `visibility = 'crew' AND crew_id = ?` itself",
    # --- keyed on a row the caller did not name ---
    "ci.py::ci_event": "resolves its blocker from a webhook payload, not an id",
    "forge.py::forge_event": "resolves its task from a branch or PR string",
    "chat_threads.py::update_thread": "owner-scoped by primary key, see scope.UNSCOPED",
    "delegation.py::claim_task": "the actor must BE the task's delegated_agent",
    "delegation.py::report_progress": "the actor must be the delegate or the sponsor",
    "delegation.py::accept_completion": "same delegated_agent check",
    "delegation.py::submit_completion": "same delegated_agent check",
    "engagements.py::_ship_it": "keyed on the engagement update_engagement just closed",
    "engagements.py::_experiment_lesson": "same",
    # --- jobs that carry their own rule ---
    "blockers.py::sweep_escalations": (
        "escalates every tier on purpose — a crew blocker that silently never"
        " escalates is worse than one nobody is told about. The notify and the"
        " ledger line inside it ARE tier-gated."
    ),
    "collab.py::sweep_stale_decisions": "same rule, same two gates inside",
    "digest.py::publish_digest": "upserts its own artifact row, keyed on the file path",
    "rituals.py::_write_artifact": "same",
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
    reads = re.compile(rf"\b(?:FROM|JOIN)\s+(?:{tables})\b", re.I)
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
        for fn in ast.walk(ast.parse(path.read_text())):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            key = f"{path.name}::{fn.name}"
            if not any(reads.search(_sql_text(n)) for n in ast.walk(fn)):
                continue
            # a Name or an Attribute, not the rendered text: WORKSPACE_ONLY and
            # assert_editable are interpolations, so they render as `{}`
            refs = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)} | {
                n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)
            }
            if "WORKSPACE_ONLY" in refs or "assert_editable" in refs:
                continue
            if any(
                a.arg == "viewer" for a in fn.args.posonlyargs + fn.args.args + fn.args.kwonlyargs
            ):
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
    """Phase 6. The shared body stays a PREFIX of the crew pack, so a reader
    can tell which half the whole roster already has."""
    from app.services import context_pack, crews

    users.ensure_user("ava")
    users.ensure_user("bo")
    cid = crews.create_crew("Platform", actor="ava")["id"]
    collab.record_decision(
        "ZZCREWZZ call", "x", decided_by="ava", actor="ava", visibility="crew", crew_id=cid
    )
    collab.record_decision("open call", "y", decided_by="ava", actor="ava")

    team = context_pack.get_pack(actor="ava")["content"]
    crew_pack = context_pack.get_pack(actor="ava", crew_id=cid)["content"]
    assert "ZZCREWZZ" not in team and "open call" in team
    assert "ZZCREWZZ" in crew_pack and "Platform only" in crew_pack

    # a non-member gets the same answer as somebody naming a crew that is gone
    with pytest.raises(db.NotFound):
        context_pack.get_pack(actor="bo", crew_id=cid)


def test_each_crew_versions_its_pack_independently(fresh_db):
    """A shared counter would bump every crew's version whenever any one of
    them published, and the version is what a reader cites."""
    from app.services import context_pack, crews

    users.ensure_user("ava")
    a = crews.create_crew("Alpha", actor="ava")["id"]
    b = crews.create_crew("Beta", actor="ava")["id"]
    assert context_pack.get_pack(actor="ava")["version"] == 1
    assert context_pack.get_pack(actor="ava", crew_id=a)["version"] == 1
    assert context_pack.get_pack(actor="ava", crew_id=b)["version"] == 1
    collab.record_decision("new", "x", decided_by="ava", actor="ava", visibility="crew", crew_id=a)
    assert context_pack.publish_pack(actor="ava", crew_id=a)["version"] == 2
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
    context_pack.publish_pack(actor="ava", crew_id=a)
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
