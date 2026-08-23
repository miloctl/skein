"""Coverage assertion over the agent write path: every tool call that writes
the database must leave a receipt.

"Provenance on every write" is sampled by the golden traces; this file proves
it. The seam is instrumented, not enumerated: db.execute / db.execute_rowcount
are wrapped to observe mutating SQL, every tool in the registry is invoked,
and a call that wrote with zero receipts fails — whether it bypassed the gate
or the gate grew an exit that forgot to record. Coverage breach is
load-bearing: without it, the receipts are decorative logging.

Two deliberate properties:
- Tools are enumerated from ALL_TOOLS, never a hand list, so a new tool is
  covered by default and cannot quietly opt out. A tool the arg heuristics
  cannot call fails the suite until it gets an entry in ARGS.
- Receipt kinds are asserted as literal strings. Importing the gate's own
  constants would let a bug in the gate hide from both sides.
"""

import inspect
import json
import re

import pytest

from app import db, ratelimit
from app.agents import receipts
from app.tools import ALL_TOOLS, CORE_WRITE_TOOLS

_KINDS = {"wrote", "queued", "refused", "failed"}  # literal, not imported

# Derived-cache tables a READ tool may lazily fill. Not a receipts gap: the
# write is a projection of existing records, attributed via created_by and its
# own activity row at the service layer (context_pack.publish_pack), and a
# "wrote" receipt on a read tool would tell the user their question mutated
# the workspace. Every entry here needs that justification to hold.
DERIVED_TABLES = {"context_packs"}

# per-tool args where the name/type heuristics below are not enough.
# This list feeds ARGUMENTS only — inclusion always comes from ALL_TOOLS.
ARGS: dict[str, dict] = {
    "start_engagement_from_playbook": {
        "playbook_slug": "prototype",
        "engagement_name": "coverage probe engagement",
    },
    "update_task": {"task_id": 1, "description": "coverage probe"},
    # question 2, not 1: answer_question runs earlier in registry order and
    # answers question 1, and assigning an answered question refuses pre-gate
    "assign_question": {"question_id": 2, "assigned_to": "tester"},
    # a dedicated undelegated task and a real active human sponsor — the
    # heuristic sponsor string refuses before the gate
    "delegate_task": {"task_id": 3, "agent": "probe-agent", "sponsor": "tester"},
    # non-empty payloads, or the empty-update bounce fires BEFORE the gate and
    # a bypass inside these tools passes the harness without ever writing
    "edit_note": {"note_id": 1, "content": "coverage probe edit"},
    # blocker 2, not 1: resolve_blocker runs EARLIER in registry order and
    # resolves blocker 1, and editing a resolved blocker refuses before the
    # gate — the same ordering disturbance the trio's dedicated task avoids
    "edit_blocker": {"blocker_id": 2, "title": "coverage probe edit"},
    "edit_intake_request": {"request_id": 1, "title": "coverage probe edit"},
    # the exact run _seed wrote: edit_document refuses a match it cannot
    # find, and refuses one it finds twice
    "edit_document": {
        "artifact_id": 1,
        "old_text": "probe body",
        "new_text": "coverage probe edit",
    },
    "edit_promise": {"promise_id": 1, "promise": "coverage probe edit"},
    # the delegation trio uses its own task so no earlier tool can disturb it
    "claim_delegated_task": {"task_id": 2},
    "report_progress": {"task_id": 2, "note": "coverage probe progress"},
    "submit_for_acceptance": {"task_id": 2, "summary": "coverage probe done"},
    "update_milestone": {"milestone_id": 1, "title": "coverage probe rename"},
    "update_engagement": {"engagement_id": 1, "summary": "coverage probe"},
    "add_absence": {
        "person": "tester",
        "starts_on": "2026-08-10",
        "ends_on": "2026-08-11",
    },
    "mark_promise": {"promise_id": 1, "status": "kept"},
    "supersede_decision": {
        "decision_id": 1,
        "title": "coverage probe successor",
        "decision": "supersede it",
    },
}

_STR_BY_NAME = {
    "due_date": "2026-08-20",
    "review_by": "2026-08-20",
    "starts_on": "2026-08-10",
    "ends_on": "2026-08-11",
    "starts_at": "2026-08-20T10:00:00",
    "date": "2026-08-20",
    "audience": "team",
    "impact": "low",
}


def _kwargs_for(fn) -> dict:
    """Type-and-name driven args. A parameter this cannot fill raises, which
    fails the suite loudly — the fix is an ARGS entry, never a skip."""
    if fn.__name__ in ARGS:
        return ARGS[fn.__name__]
    kwargs = {}
    for name, param in inspect.signature(fn).parameters.items():
        if param.default is not inspect.Parameter.empty:
            continue
        if param.annotation is int or name.endswith("_id"):
            kwargs[name] = 1
        elif param.annotation is str or param.annotation is inspect.Parameter.empty:
            kwargs[name] = _STR_BY_NAME.get(name, f"coverage probe {name}")
        else:
            raise AssertionError(
                f"{fn.__name__}: no heuristic for parameter {name!r} — add an ARGS entry"
            )
    return kwargs


def _seed(fresh_db):
    """One row of everything, so id=1 resolves for the update/verb tools.
    Seeded through the services as a human, before instrumentation."""
    from app.services import (
        blockers,
        collab,
        delegation,
        documents,
        engagements,
        intake,
        memory,
        promises,
        schedule,
        users,
        work,
    )

    users.ensure_user("tester")  # the sponsor must be an active human
    users.ensure_user("probe-agent", kind="agent")  # delegation target; the
    # calling identity is "agent" and self-delegation is refused pre-gate
    users._reserve_core_agent_identity("agent")  # application startup owns this row
    # the FIRST artifact, so it is id 1 for edit_document below — generate_handoff
    # writes its own later, inside the loop
    documents.create_document("probe document", "probe body", actor="tester")
    engagements.create_engagement("probe engagement", actor="tester")
    work.create_milestone("probe milestone", actor="tester")
    work.create_task("probe task", actor="tester")
    collab.ask_question("probe question?", asked_by="tester")
    collab.ask_question("probe question for assigning?", asked_by="tester")  # id 2
    collab.record_decision("probe decision", "we probe", actor="tester")
    collab.save_note("probe", "probe note", author="tester")
    schedule.schedule_event("probe event", "2026-08-20T10:00:00", actor="tester")
    blockers.raise_blocker("probe blocker", actor="tester")
    blockers.raise_blocker("probe blocker for editing", actor="tester")  # id 2
    promises.add_promise("probe promise", actor="tester")
    memory.remember("probe memory", "probe", actor="tester")
    intake.submit_request("probe request", requester="tester", actor="tester")
    work.create_task("probe delegated task", actor="tester")  # id 2, the trio's own
    delegation.delegate_task(2, "agent", sponsor="tester", actor="tester")
    work.create_task("probe undelegated task", actor="tester")  # id 3, delegate_task's own


def _unwrap(tool):
    """The plain function under the strands decorator, whatever its shape."""
    for attr in ("original_function", "_tool_func", "func", "__wrapped__"):
        fn = getattr(tool, attr, None)
        if callable(fn):
            return fn
    return tool


def test_every_tool_that_writes_leaves_a_receipt(fresh_db, monkeypatch):
    from app.services import notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    _seed(fresh_db)

    writes: list[str] = []
    real_execute, real_rowcount = db.execute, db.execute_rowcount
    table = re.compile(
        r"^\s*(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_]+)", re.I
    )

    def spy_execute(sql, params=()):
        if m := table.match(sql):
            writes.append(m.group(1))
        return real_execute(sql, params)

    def spy_rowcount(sql, params=()):
        if m := table.match(sql):
            writes.append(m.group(1))
        return real_rowcount(sql, params)

    monkeypatch.setattr(db, "execute", spy_execute)
    monkeypatch.setattr(db, "execute_rowcount", spy_rowcount)

    # which tools reached the gate at all. UNGATED_WRITERS is asserted against
    # this below, so the list stops being a claim someone has to remember to
    # update: a new writer that skips gated_write needs a flock guard too
    # (identity.refuse_when_consultative), and tests/test_flock_turns.py derives its
    # cases from the same map.
    # patched per importing module, not on _gate: each tool module does
    # `from ._gate import gated_write`, so it holds its own reference and a
    # patch on the source module would never be seen
    import importlib

    from app.tools import _gate

    gated: set[str] = set()
    real_gated_write = _gate.gated_write
    current: list[str] = []

    def spy_gate(*a, **kw):
        if current:
            gated.add(current[0])
        return real_gated_write(*a, **kw)

    for name in ("collab", "files", "memory", "platform", "portfolio", "schedule", "work"):
        mod = importlib.import_module(f"app.tools.{name}")
        assert hasattr(mod, "gated_write"), f"app.tools.{name} no longer imports gated_write"
        monkeypatch.setattr(mod, "gated_write", spy_gate)

    silent_writers = []
    uncallable = []
    covered = set()
    for tool in ALL_TOOLS:
        fn = _unwrap(tool)
        try:
            kwargs = _kwargs_for(fn)
        except AssertionError as exc:
            uncallable.append(str(exc))
            continue
        ratelimit.reset()
        receipts.start()
        writes.clear()
        current[:] = [fn.__name__]
        try:
            out = fn(**kwargs)
        except Exception as exc:  # a tool must never raise into the agent loop
            silent_writers.append(f"{fn.__name__}: raised {type(exc).__name__}: {exc}")
            continue
        got = receipts.drain()
        assert isinstance(out, str), f"{fn.__name__} returned {type(out).__name__}, not str"
        json.loads(out)  # every tool speaks JSON
        for r in got:
            assert r["kind"] in _KINDS, f"{fn.__name__} emitted unknown receipt kind {r['kind']!r}"
        real_writes = sorted(set(writes) - DERIVED_TABLES)
        if real_writes and not got:
            silent_writers.append(f"{fn.__name__}: wrote {real_writes} with no receipt")
        if real_writes and got:
            covered.add(fn.__name__)

    assert not uncallable, "tools the heuristics cannot call:\n" + "\n".join(uncallable)
    assert not silent_writers, (
        "tool calls that mutated the database without leaving a receipt —"
        " either they bypassed the gate or a gate exit forgot to record:\n"
        + "\n".join(silent_writers)
    )
    # the FULL snapshot of writers, not a floor: a floor of 20 would let 14
    # tools silently degrade to error paths before anything went loud. One
    # line of maintenance per new writing tool, which is the point — a new
    # tool declares itself here or fails the suite.
    expected_writers = {
        "add_absence",
        "add_promise",
        "answer_question",
        "ask_question",
        "assign_question",
        "cancel_event",
        "claim_delegated_task",
        "create_document",
        "create_milestone",
        "create_task",
        "delegate_task",
        "delete_note",
        "edit_blocker",
        "edit_document",
        "edit_promise",
        "edit_intake_request",
        "edit_note",
        "forget_memory",
        "generate_handoff",
        "mark_promise",
        "post_standup",
        "raise_blocker",
        "record_decision",
        "record_lesson",
        "remember",
        "report_progress",
        "resolve_blocker",
        "save_note",
        "schedule_event",
        "start_engagement_from_playbook",
        "submit_for_acceptance",
        "submit_intake_request",
        "supersede_decision",
        "update_engagement",
        "update_milestone",
        "update_task",
    }
    assert expected_writers == CORE_WRITE_TOOLS
    assert covered == expected_writers, (
        f"degraded to error paths: {sorted(expected_writers - covered)};"
        f" new unlisted writers: {sorted(covered - expected_writers)}"
    )
    # DERIVED, not declared: every writer that never reached gated_write also
    # never sees identity.force_review, so it owes its own refuse_when_consultative
    # guard. Adding one here without that guard is how a flock member gets an
    # ungoverned write path back.
    assert covered - gated == set(UNGATED_WRITERS), (
        "the set of writers that bypass the gate changed —"
        f" derived {sorted(covered - gated)}, declared {sorted(UNGATED_WRITERS)}."
        " Give the new one a refuse_when_consultative guard, then list it."
    )


def test_the_registry_is_the_only_inclusion_source():
    """ARGS entries must name real tools — a renamed tool with a stale ARGS
    key would silently fall back to heuristics that may not fit."""
    names = {_unwrap(t).__name__ for t in ALL_TOOLS}
    stale = set(ARGS) - names
    assert not stale, f"ARGS names tools that are not in the registry: {sorted(stale)}"


# The writers that bypass gated_write on purpose. Shared, not repeated: every
# one of them also needs a refuse_when_consultative guard (tests/test_flock_turns.py
# derives its cases from this map), because force_review only reaches the gate.
UNGATED_WRITERS = {
    "claim_delegated_task": "wrote",
    "report_progress": "wrote",
    "submit_for_acceptance": "queued",
    "generate_handoff": "wrote",
}


@pytest.mark.parametrize(("tool_name", "expected_kind"), sorted(UNGATED_WRITERS.items()))
def test_the_ungated_writers_report_themselves(fresh_db, monkeypatch, tool_name, expected_kind):
    """The delegation loop and the handoff generator bypass the generic gate
    on purpose — sponsor-bound verdicts and artifact projection have their own
    rules — so they record their own receipts. Before this, submitting a task
    for acceptance filed a proposal and the chat UI stated nothing."""
    from app import tools as tools_pkg
    from app.services import notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    _seed(fresh_db)
    args = {
        "claim_delegated_task": {"task_id": 2},
        "report_progress": {"task_id": 2, "note": "probe progress"},
        "submit_for_acceptance": {"task_id": 2, "summary": "probe done"},
        "generate_handoff": {"engagement_id": 1},
    }[tool_name]
    if tool_name in ("report_progress", "submit_for_acceptance"):
        _unwrap(tools_pkg.claim_delegated_task)(task_id=2)

    receipts.start()
    out = json.loads(_unwrap(getattr(tools_pkg, tool_name))(**args))
    got = receipts.drain()
    assert "error" not in out, out
    assert [r["kind"] for r in got] == [expected_kind]
    if expected_kind == "queued":
        # the ref IS the proposal id — the transcript renders "#N", and 0
        # silently drops it, unlike every gate-queued receipt
        assert got[0]["ref"] == out["proposal_id"] > 0


def test_a_failing_ungated_writer_reports_the_failure(fresh_db, monkeypatch):
    from app.services import notifications
    from app.tools import claim_delegated_task

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    receipts.start()
    out = json.loads(_unwrap(claim_delegated_task)(task_id=999))
    got = receipts.drain()
    assert "error" in out
    assert [r["kind"] for r in got] == ["failed"]


def test_the_shipped_default_holds_agent_writes_for_review():
    """Gate ON with the variable unset — the fail-closed posture every other
    trust boundary already has (an unset auth mode refuses every request).
    A fresh process, because conftest pins the suite's copy to "0" and
    monkeypatching an attribute never exercises the parse. "" means the
    default, not off: the conftest idiom pins settings empty to keep
    backend/.env out of the suite."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    # "" and never unset: config's load_dotenv() re-fills an ABSENT var from
    # backend/.env, so an unset probe reads the developer's overlay instead of
    # the shipped default — the same reason conftest pins with "" above.
    env = {**os.environ, "SKEIN_AGENT_REVIEW": ""}
    code = "from app import config; print(int(config.AGENT_REVIEW))"
    out = subprocess.run(  # noqa: S603 — fixed argv, this interpreter, literal source
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=True,
    )
    assert out.stdout.strip() == "1"
    example = (Path(__file__).resolve().parents[1] / ".env.example").read_text()
    assert "SKEIN_AGENT_REVIEW=1" in example
    assert "SKEIN_AGENT_REVIEW=0" not in example
