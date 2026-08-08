"""An agent resuming multi-day delegated work must be able to read what it
already recorded. report_progress wrote the worklog from day one and nothing
in the tool registry read it back, so day 3 restarted from the task title."""

import json

from app.services import delegation, work
from app.tools import ALL_TOOLS
from app.tools.portfolio import read_worklog


def _delegated(fresh_db, agent="research-agent", sponsor="tester"):
    from app.services.users import ensure_user

    ensure_user(agent, kind="agent")
    ensure_user(sponsor, kind="human")  # delegate_task refuses a sponsor who is not one
    task = work.create_task("chase the vendor", actor=sponsor)
    delegation.delegate_task(task["id"], agent=agent, sponsor=sponsor, actor=sponsor)
    return task["id"]


def test_the_tool_is_registered(fresh_db):
    """ALL_TOOLS is what the agent is built with — a tool defined and not
    registered is invisible to the model, which is the state this fixes."""
    assert read_worklog in ALL_TOOLS


def test_an_agent_reads_back_its_own_notes(fresh_db):
    task_id = _delegated(fresh_db)
    delegation.claim_task(task_id, actor="research-agent")
    delegation.report_progress(
        task_id, "vendor emailed, waiting on the schema", actor="research-agent"
    )
    delegation.report_progress(
        task_id, "schema arrived, it is missing the price field", actor="research-agent"
    )

    out = json.loads(read_worklog(task_id))
    notes = [n["note"] for n in out["worklog"]]
    assert "schema arrived, it is missing the price field" in notes
    assert "vendor emailed, waiting on the schema" in notes


def test_newest_first_so_a_truncated_read_keeps_the_latest(fresh_db):
    """The limit exists, so the order decides WHICH notes survive it. Oldest
    first would hand a resuming agent the notes it least needs."""
    task_id = _delegated(fresh_db)
    delegation.claim_task(task_id, actor="research-agent")
    for i in range(5):
        delegation.report_progress(task_id, f"note {i}", actor="research-agent")
    out = json.loads(read_worklog(task_id, limit=2))
    assert [n["note"] for n in out["worklog"]] == ["note 4", "note 3"]


def test_a_missing_task_is_an_error_not_an_empty_worklog(fresh_db):
    """An empty list would read as "you logged nothing", which sends the agent
    off to redo work it may already have done."""
    out = json.loads(read_worklog(9999))
    assert out.get("error")


def test_the_limit_is_bounded(fresh_db):
    """A model-supplied limit reaches SQL. Unbounded, one bad turn pulls
    every note on the task into the context window."""
    task_id = _delegated(fresh_db)
    delegation.claim_task(task_id, actor="research-agent")
    delegation.report_progress(task_id, "only note", actor="research-agent")
    assert len(json.loads(read_worklog(task_id, limit=10_000))["worklog"]) == 1
    # 0 and negatives must not become "no rows" — they clamp to at least one
    assert len(json.loads(read_worklog(task_id, limit=0))["worklog"]) == 1


def test_the_inbox_carries_the_last_note_per_open_task(fresh_db):
    """The wake-up view is where continuity belongs: an agent that must call a
    second tool to learn it was mid-task will sometimes not call it."""
    task_id = _delegated(fresh_db)
    delegation.claim_task(task_id, actor="research-agent")
    delegation.report_progress(task_id, "waiting on the schema", actor="research-agent")

    inbox = delegation.agent_inbox("research-agent")
    assert [n["note"] for n in inbox["last_progress"]] == ["waiting on the schema"]
    assert inbox["last_progress"][0]["task_id"] == task_id


def test_the_rest_door_never_serves_worklog_text(fresh_db):
    """GET /api/agents/{agent}/inbox takes the agent name off the URL and
    answers any CurrentUser — the same reason notification bodies are stripped
    there. A worklog note quotes the task's own text."""
    from app.services import scope

    task_id = _delegated(fresh_db)
    delegation.claim_task(task_id, actor="research-agent")
    delegation.report_progress(task_id, "commercially sensitive detail", actor="research-agent")

    viewed = delegation.agent_inbox("research-agent", viewer=scope.NOBODY)
    assert viewed["last_progress"] == []
    assert "commercially sensitive detail" not in json.dumps(viewed)
