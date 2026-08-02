"""Write receipts state what a chat turn actually filed, and stay inert outside a turn."""


def test_write_receipts_state_what_actually_happened(fresh_db, monkeypatch):
    """The gate reports the outcome, so the UI states a fact instead of
    repeating the model's claim."""
    import json as j

    from app import config
    from app.agents import receipts
    from app.agents.identity import reset_agent_identity, set_agent_identity
    from app.services import delegation, users
    from app.tools.work import create_task as create_task_tool

    users.ensure_user("scribe", kind="agent")
    users.ensure_user("mira")
    receipts.start()
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    token = set_agent_identity("scribe")
    try:
        out = j.loads(create_task_tool(title="receipt probe"))
        assert out.get("note") == "queued for human review"
        queued = receipts.drain()
        assert len(queued) == 1
        assert queued[0]["kind"] == "queued"
        assert queued[0]["entity"] == "task"
        assert queued[0]["ref"] == out["id"]  # the proposal to open in Inbox
        assert receipts.drain() == []  # drained exactly once

        # a refusal is a receipt too — silence is what we're fixing
        delegation.set_authority("scribe", "task", "forbidden", actor="mira")
        j.loads(create_task_tool(title="blocked"))
        refused = receipts.drain()
        assert refused and refused[0]["kind"] == "refused"

        # and a direct write reports the row it created
        delegation.set_authority("scribe", "task", "autonomous", actor="mira")
        wrote = j.loads(create_task_tool(title="direct write"))
        rec = receipts.drain()
        assert rec and rec[0]["kind"] == "wrote" and rec[0]["ref"] == wrote["id"]
    finally:
        reset_agent_identity(token)


def test_receipts_are_inert_outside_a_chat_turn(fresh_db):
    """REST, MCP and scheduler writes must not accumulate in a contextvar
    nobody drains."""
    from app.agents import receipts
    from app.services import work

    work.create_task(title="rest write", actor="mira")
    assert receipts.drain() == []
