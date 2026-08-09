"""The findings engine, reachable from a conversation.

The deterministic insight engine and the chat agent did not know about each
other: 19 rules fired daily with receipts, and no tool exposed them — so
"what should worry me this week" could not reach the system's own best answer,
and an agent that tried assembled one out of raw task lists instead.

Both tools are READS. They take no authority level and pass no gate, which is
why `tests/test_gate_coverage.py` has nothing to say about them.
"""

import json
from datetime import UTC, datetime, timedelta

from app.agents import identity
from app.services import insights, scope
from app.tools import ALL_TOOLS
from app.tools.portfolio import get_attention, get_findings


def _unwrap(tool):
    """The plain function under the strands decorator, whatever its shape —
    the same walk tests/test_gate_coverage.py uses."""
    for attr in ("original_function", "_tool_func", "func", "__wrapped__"):
        fn = getattr(tool, attr, None)
        if callable(fn):
            return fn
    return tool


def _call(tool, **kw):
    return json.loads(_unwrap(tool)(**kw))


def test_both_tools_are_in_the_registry():
    """ALL_TOOLS is what build_agent hands the model. A tool that exists and
    is not registered is a tool no agent can ever call."""
    names = {getattr(t, "tool_name", getattr(t, "__name__", "")) for t in ALL_TOOLS}
    assert {"get_findings", "get_attention"} <= names


def test_findings_come_back_with_their_receipts(client, fresh_db):
    # an aged open question fires question_aging — the fixture the insights
    # suite already proves fires, rather than a row invented here
    q = client.post("/api/questions", json={"question": "still open?"}).json()
    fresh_db.execute(
        "UPDATE questions SET created_at = ? WHERE id = ?",
        ((datetime.now(UTC) - timedelta(days=6)).isoformat(), q["id"]),
    )
    assert insights.run_findings()["new"] >= 1
    out = _call(get_findings)
    assert out, "the engine fired nothing to report"
    assert {"rule_id", "severity", "message"} <= set(out[0])
    # the receipt is the reason to cite a finding rather than restate it
    assert "receipt" in out[0]


def test_findings_bounds_are_clamped_not_trusted(client, fresh_db):
    """Seeded PAST the cap first. With an empty table every assertion here
    passed against the clamp deleted — `0 <= 50` and `isinstance([], list)`
    are true either way, and `weeks=-5` returning `[]` IS the silent "all
    clear" the comment says must never be invented."""
    from app.services import collab

    # through the service, not the REST door: 60 posts trip the write cap, and
    # the cap is not what this test is about
    for i in range(60):
        collab.ask_question(question=f"open {i}?", asked_by="ava", actor="ava")
    fresh_db.execute(
        "UPDATE questions SET created_at = ?",
        ((datetime.now(UTC) - timedelta(days=6)).isoformat(),),
    )
    insights.run_findings()
    stored = fresh_db.query_one("SELECT COUNT(*) AS n FROM findings")["n"]
    assert stored > 50, f"the fixture did not clear the cap ({stored} findings)"

    assert len(_call(get_findings, limit=10_000)) == 50
    # a zero or negative window must read as the smallest real window, never
    # as an empty result
    assert _call(get_findings, weeks=0) == _call(get_findings, weeks=1)
    assert _call(get_findings, weeks=-5) == _call(get_findings, weeks=1)
    assert _call(get_findings, weeks=0), "a clamped window still has to return rows"


def test_attention_answers_for_the_requester_and_takes_no_name(client):
    """No argument names a person: the answer is the REQUESTER's day, resolved
    from the turn's own identity. Same shape as my_agent_inbox, and for the
    same reason (tests/test_privacy.py)."""
    import inspect

    assert not inspect.signature(_unwrap(get_attention)).parameters

    client.post("/api/questions", json={"question": "Who owns infra?", "assigned_to": "ava"})
    token = identity.set_requester_viewer(scope.Viewer("ava", True))
    try:
        out = _call(get_attention)
    finally:
        identity.reset_requester_viewer(token)
    assert any(item.get("kind") == "question" for item in out)
    # `reason`, and a NON-EMPTY one: the docstring tells the model to say it,
    # so an empty string here would make the tool promise something the data
    # does not carry. Key-presence alone passes against exactly that bug.
    assert out and all(item.get("reason") for item in out)
    # the groups the docstring names, which are not the item kinds
    assert {i["group"] for i in out} <= {"decide", "unblock", "commit", "review", "notice"}


def test_attention_says_so_when_no_requester_is_in_scope(client):
    """A scheduled or MCP turn has no person to answer for. Returning an empty
    list there would read as "nothing needs you", which is a claim."""
    out = _call(get_attention)
    assert out.get("error")
