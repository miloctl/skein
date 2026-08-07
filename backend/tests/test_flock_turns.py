"""A flock turn in chat: fan-out, sections, forced review, and the trace."""

import json

import pytest

from app import config
from app.agents.identity import (
    agent_identity,
    reset_agent_identity,
    set_agent_identity,
    set_force_review,
)
from app.services import flocks


def _read_chat(client, message, thread="f"):
    with client.stream("POST", "/api/chat", json={"thread_id": thread, "message": message}) as resp:
        assert resp.status_code == 200
        return resp.read().decode()


def test_flocks_command_lists_the_roster(client):
    out = _read_chat(client, "/flocks")
    assert "engineering" in out and "/flock" in out
    assert "Backend Architect" in out  # members are named, not just counted


def test_flock_command_is_registered_for_the_route(client):
    """dispatch() must NOT answer /flock: an unregistered slash command gets a
    did-you-mean line, and /flocks is its closest match."""
    from app.agents import commands

    assert commands.dispatch("/flock engineering hello", "tester") is None
    assert "flock" in [c["name"] for c in client.get("/api/chat/commands").json()]


def test_usage_and_unknown_flock_are_deterministic(client):
    assert "Usage" in _read_chat(client, "/flock")
    assert "Usage" in _read_chat(client, "/flock engineering")
    assert "no flock 'ghost'" in _read_chat(client, "/flock ghost do things")


def test_off_charset_slug_is_not_echoed_in_chat(client):
    out = _read_chat(client, "/flock DROP'TABLE do things")
    assert "DROP'TABLE" not in out
    assert "not a flock slug" in out


def test_every_member_answers_in_declared_order(client):
    out = _read_chat(client, "/flock engineering should we shard the database")
    order = [out.index(n) for n in ("Backend Architect", "Code Reviewer", "Minimal Change")]
    assert order == sorted(order), out
    assert out.count('"type": "done"') == 1


def test_mock_members_write_nothing(client, fresh_db):
    """MockAgent smart-captures freeform text outside the gate. A flock member
    must never take that path, or one question files N records."""
    before = len(client.get("/api/tasks").json())
    _read_chat(client, "/flock engineering todo: rewrite the scheduler")
    assert len(client.get("/api/tasks").json()) == before
    assert fresh_db.query_row("SELECT COUNT(*) AS n FROM pending_changes")["n"] == 0


def test_the_turn_logs_one_assistant_message(client):
    _read_chat(client, "/flock engineering what breaks first", thread="one")
    rows = client.get("/api/chats/one/messages").json()
    assert [r["role"] for r in rows] == ["user", "assistant"]
    # mastheads are h3, not bold: one bubble holds every member, so the seam
    # has to be a heading that .prose-chat can size (frontend/app/globals.css)
    content = rows[1]["content"]
    assert content.count("\n### ") == 3
    # a rule BETWEEN members, so one fewer than the members themselves
    assert content.count("\n---\n\n### ") == 2
    assert not content.lstrip().startswith("---")


def test_a_trace_row_lands_with_every_member(client, fresh_db):
    _read_chat(client, "/flock engineering trace me", thread="traced")
    row = fresh_db.query_row("SELECT * FROM flock_traces ORDER BY id DESC")
    assert row["flock"] == "engineering" and row["thread_id"] == "traced"
    members = json.loads(row["members"])
    assert [m["slug"] for m in members] == flocks.get_flock("engineering")["members"]
    assert all(m["status"] == "ok" for m in members)
    assert row["synthesis"] is None  # engineering does not synthesize


def test_synthesis_runs_last_and_is_traced(client, fresh_db):
    out = _read_chat(client, "/flock delivery what should we cut", thread="syn")
    assert "together" in out
    assert "members answered" in out  # keyless merge states the count
    row = fresh_db.query_row("SELECT * FROM flock_traces ORDER BY id DESC")
    synth = json.loads(row["synthesis"])
    assert synth["status"] == "ok"


def test_traces_rest_filters(client):
    _read_chat(client, "/flock engineering one", thread="ta")
    _read_chat(client, "/flock delivery two", thread="tb")
    assert len(client.get("/api/flocks/traces").json()) == 2
    only = client.get("/api/flocks/traces?thread=ta").json()
    assert len(only) == 1 and only[0]["flock"] == "engineering"
    assert only[0]["members"][0]["name"]  # decoded, not a JSON string


def test_flock_cannot_smuggle_fb_past_the_guard(client):
    out = _read_chat(client, "/flock engineering fb: mira — private thing")
    assert "Feedback notes are private" in out


@pytest.mark.parametrize("review_flag", [False, True])
def test_force_review_queues_a_write_the_matrix_would_apply(fresh_db, monkeypatch, review_flag):
    """The headline guarantee: a member that earned `autonomous` still files a
    proposal, with SKEIN_AGENT_REVIEW either way."""
    from app.services.delegation import set_authority
    from app.tools._gate import gated_write

    monkeypatch.setattr(config, "AGENT_REVIEW", review_flag)
    set_authority("code-reviewer", "task", "autonomous", actor="tester")
    token = set_agent_identity("code-reviewer")
    force_token = set_force_review(True)
    try:
        gated_write("task", "create", {"title": "from a flock"}, direct=lambda: {"id": 0})
    finally:
        set_force_review(False)
        reset_agent_identity(token)
        del force_token
    row = fresh_db.query_row("SELECT * FROM pending_changes ORDER BY id DESC")
    assert row["proposed_by"] == "code-reviewer"
    assert (
        fresh_db.query_row("SELECT COUNT(*) AS n FROM tasks WHERE title = 'from a flock'")["n"] == 0
    )


def test_forbidden_still_refuses_under_force_review(fresh_db, monkeypatch):
    """A kill switch must not soften into a proposal."""
    from app.services.delegation import set_authority
    from app.tools._gate import gated_write

    set_authority("code-reviewer", "task", "forbidden", actor="tester")
    token = set_agent_identity("code-reviewer")
    set_force_review(True)
    try:
        out = json.loads(gated_write("task", "create", {"title": "no"}, direct=lambda: {"id": 0}))
    finally:
        set_force_review(False)
        reset_agent_identity(token)
    assert "forbidden" in out["error"]
    assert fresh_db.query_row("SELECT COUNT(*) AS n FROM pending_changes")["n"] == 0


def test_force_review_is_off_for_a_normal_turn():
    from app.agents.identity import force_review

    assert force_review() is False
    assert agent_identity() == "agent"


def test_a_stopped_turn_still_records_every_member(client, fresh_db):
    """Stop button / closed tab. task.cancel() lands on a LATER loop
    iteration, so a cancelled member never delivers its own entry, and the
    generator is finalized in a foreign context where the requester reset
    raises. Either bug alone loses the whole turn record."""
    import asyncio

    from app.routes.chat import _flock_stream

    fdef = flocks.get_flock("engineering")

    async def drive():
        agen = _flock_stream(fdef, "stopped", "tester", "hello", "/flock engineering hello")
        await agen.__anext__()  # first frame, then abandon it like a closed tab
        # aclose from ANOTHER task: that is the foreign context an abandoned
        # SSE stream is finalized in
        await asyncio.create_task(agen.aclose())

    asyncio.run(drive())
    row = fresh_db.query_row("SELECT * FROM flock_traces WHERE thread_id = 'stopped'")
    members = json.loads(row["members"])
    assert [m["slug"] for m in members] == fdef["members"]
    assert all(m["status"] == "cancelled" for m in members)


def test_a_failing_member_leaves_the_other_sections_intact(client, fresh_db, monkeypatch):
    from app.routes import chat as chat_route

    real = chat_route.build_agent

    def flaky(thread_id, user="anonymous", persona="", stateless=False):
        if persona == "code-reviewer":
            raise RuntimeError("401 token sk-live-abcd request_id=42")
        return real(thread_id, user, persona=persona, stateless=stateless)

    monkeypatch.setattr(chat_route, "build_agent", flaky)
    out = _read_chat(client, "/flock engineering keep going", thread="flaky")
    assert "Code Reviewer did not answer (RuntimeError)" in out
    # a provider error carries its raw HTTP body — request ids, key prefixes.
    # It goes to the log, never to the chat window or the transcript.
    assert "sk-live-abcd" not in out and "request_id" not in out
    members = json.loads(
        fresh_db.query_row("SELECT * FROM flock_traces ORDER BY id DESC")["members"]
    )
    by_slug = {m["slug"]: m["status"] for m in members}
    assert by_slug["code-reviewer"] == "failed"
    assert by_slug["backend-architect"] == "ok" and by_slug["minimal-change-engineer"] == "ok"


def test_every_ungated_writer_refuses_in_a_flock(client, fresh_db):
    """Derived from test_gate_coverage.py's list, NOT hand-written: those four
    tools skip tools/_gate.py by design, so force_review never reaches them and
    each needs its own guard. A hand-written list here passed while
    submit_for_acceptance was missing one, which is the drift the shared list
    exists to catch. A fifth ungated writer fails this test until it decides."""
    from test_gate_coverage import UNGATED_WRITERS

    from app.services import delegation, handoff

    task = client.post("/api/tasks", json={"title": "delegated work"}).json()
    delegation.delegate_task(task["id"], "code-reviewer", "tester", actor="tester")
    calls = {
        "claim_delegated_task": lambda: delegation.claim_task(task["id"], actor="code-reviewer"),
        "report_progress": lambda: delegation.report_progress(
            task["id"], "note", actor="code-reviewer"
        ),
        "submit_for_acceptance": lambda: delegation.submit_completion(
            task["id"], "done", actor="code-reviewer"
        ),
        "generate_handoff": lambda: handoff.generate_handoff(1, actor="code-reviewer"),
    }
    assert set(calls) == set(UNGATED_WRITERS), "an ungated writer has no flock case here"

    set_force_review(True)
    try:
        for name, call in calls.items():
            with pytest.raises(ValueError, match="flock member") as exc:
                call()
            assert name  # the failing case is identifiable in the report
            assert "ask this agent directly" in str(exc.value)
    finally:
        set_force_review(False)
    assert (
        fresh_db.query_row("SELECT status FROM tasks WHERE id = ?", (task["id"],))["status"]
        == "todo"
    )
    assert fresh_db.query_row("SELECT COUNT(*) AS n FROM task_worklog")["n"] == 0
    assert fresh_db.query_row("SELECT COUNT(*) AS n FROM pending_changes")["n"] == 0


def test_a_flock_turn_costs_one_chat_slot_per_member(client, monkeypatch):
    """One turn is an agent loop per member plus the merge. Charged as a single
    chat turn, one message bought several turns of model spend."""
    from app import ratelimit

    ratelimit.reset()
    monkeypatch.setitem(ratelimit.LIMITS, "chat", 6)
    # engineering: 3 members, no synthesis -> 3 slots
    assert "Backend Architect" in _read_chat(client, "/flock engineering one", thread="c1")
    # delivery: 3 members + synthesis -> 4 slots, over the remaining 3
    with client.stream(
        "POST", "/api/chat", json={"thread_id": "c2", "message": "/flock delivery two"}
    ) as resp:
        assert resp.status_code == 429
        assert "The limit for chat" in resp.read().decode()
    # the refused CALL took no slots, but the turn's top-of-route charge
    # already landed, so 2 remain — enough for a plain turn
    assert _read_chat(client, "hello", thread="c3")
    ratelimit.reset()


def test_a_member_keeps_the_delegation_tools_outside_a_flock(client, fresh_db):
    """The guard must not break the normal path: an agent asked directly still
    works its own delegation."""
    from app.services import delegation

    task = client.post("/api/tasks", json={"title": "direct work"}).json()
    delegation.delegate_task(task["id"], "code-reviewer", "tester", actor="tester")
    assert delegation.claim_task(task["id"], actor="code-reviewer")["status"] == "in_progress"
    assert delegation.report_progress(task["id"], "made progress", actor="code-reviewer")["id"]


def test_a_flock_slug_is_reserved_like_a_persona_slug(fresh_db):
    """Synthesis logs its spend under the FLOCK slug, so a human holding that
    name collects a bill for model calls they never made."""
    from app.services import users

    with pytest.raises(ValueError, match="reserved"):
        users.ensure_user("engineering", kind="human")
    # and the reverse direction still holds for personas
    with pytest.raises(ValueError, match="reserved"):
        users.ensure_user("code-reviewer", kind="human")


def test_a_hung_member_does_not_hold_the_turn(client, fresh_db, monkeypatch):
    """No deadline meant a provider that accepts and never answers pinned the
    task, a threadpool worker and the SSE stream — four of them per flock."""
    import asyncio as aio

    from app.routes import chat as chat_route

    class Hung:
        async def stream_async(self, message):
            await aio.sleep(3600)
            yield {"data": "never"}

    real = chat_route.build_agent

    def maybe_hang(thread_id, user="anonymous", persona="", stateless=False):
        if persona == "code-reviewer":
            return Hung()
        return real(thread_id, user, persona=persona, stateless=stateless)

    monkeypatch.setattr(chat_route, "build_agent", maybe_hang)
    monkeypatch.setattr(chat_route, "MEMBER_TIMEOUT_S", 0.4)
    out = _read_chat(client, "/flock engineering who is slow", thread="hung")
    assert "Code Reviewer did not answer" in out
    # the members that DID answer are still delivered
    assert "Backend Architect" in out and "Minimal Change Engineer" in out
    members = json.loads(
        fresh_db.query_row("SELECT * FROM flock_traces ORDER BY id DESC")["members"]
    )
    by_slug = {m["slug"]: m["status"] for m in members}
    assert by_slug["code-reviewer"] == "failed"
    assert by_slug["backend-architect"] == "ok"


def test_a_hung_merge_does_not_hold_the_turn(client, fresh_db, monkeypatch):
    """The members have all answered and the reader has nothing left to render,
    so an unbounded merge holds the SSE stream open on a finished screen. The
    member deadline covers the merge for the reason it covers a member: both are
    agent turns against a provider that can accept and never answer."""
    import asyncio as aio

    from app.routes import chat as chat_route

    class Hung:
        async def stream_async(self, message):
            await aio.sleep(3600)
            yield {"data": "never"}

    monkeypatch.setattr(chat_route, "build_synthesizer", lambda answered: Hung())
    monkeypatch.setattr(chat_route, "MEMBER_TIMEOUT_S", 0.4)
    out = _read_chat(client, "/flock delivery what shipped this week", thread="hungmerge")
    # "did not finish", not "did not run": a merge that hit the deadline
    # DID run, and whatever it streamed first is already on screen above
    assert "The merge step did not finish (TimeoutError)" in out
    # the members are still delivered — only the merge is lost
    assert "Project Shepherd" in out
    row = fresh_db.query_row("SELECT * FROM flock_traces ORDER BY id DESC")
    assert json.loads(row["synthesis"])["status"] == "failed"


def test_a_timed_out_member_still_reports_the_write_it_filed(client, fresh_db, monkeypatch):
    """A receipt for a tool call that already finished must not die with the
    member. The in-loop drain runs on the NEXT stream event, which a hung member
    never sends, and the drain after the loop is skipped by the raise — so
    before the drain in the finally, this proposal sat in the review inbox while
    the chat window and the trace row both reported none."""
    import asyncio as aio

    from app.agents import receipts
    from app.routes import chat as chat_route

    class WroteThenHung:
        async def stream_async(self, message):
            receipts.record("queued", "task", "add error tracking", ref=7)
            await aio.sleep(3600)
            yield {"data": "never"}

    real = chat_route.build_agent

    def maybe_hang(thread_id, user="anonymous", persona="", stateless=False):
        if persona == "code-reviewer":
            return WroteThenHung()
        return real(thread_id, user, persona=persona, stateless=stateless)

    monkeypatch.setattr(chat_route, "build_agent", maybe_hang)
    monkeypatch.setattr(chat_route, "MEMBER_TIMEOUT_S", 0.4)
    out = _read_chat(client, "/flock engineering file something", thread="lostreceipt")
    assert '"type": "receipt"' in out and "add error tracking" in out
    members = json.loads(
        fresh_db.query_row("SELECT * FROM flock_traces ORDER BY id DESC")["members"]
    )
    assert {m["slug"]: m["receipts"] for m in members}["code-reviewer"] == 1
