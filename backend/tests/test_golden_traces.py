"""Golden-trace scenarios: fixture + tool-call sequence → expected final DB
state + policy compliance, run through the REAL agent tool layer (the same
gate chat and MCP use). Keyless and deterministic — these
guard the tool registry, the authority gate, and the services underneath
against prompt/SDK/schema drift. What a model would CHOOSE to call is
untestable without a model; what happens when it calls is pinned here.

Scenario contract:
  steps: [(module, tool_name, kwargs)]  — executed in order
  expect_tables: {table: rowcount}      — exact counts after the run
  expect_pending: int                   — pending proposals after the run
  forbid: [table]                       — tables that must stay EMPTY
"""

import json

import pytest

SCENARIOS = [
    {
        "name": "plan-project-from-playbook",
        "review": False,
        "steps": [
            (
                "platform",
                "start_engagement_from_playbook",
                {"playbook_slug": "prototype", "engagement_name": "Golden Proto"},
            ),
        ],
        "expect_tables": {"engagements": 1},
        "expect_min": {"milestones": 2, "tasks": 3, "events": 1},
        "expect_pending": 0,
    },
    {
        "name": "capture-routes-every-kind",
        "review": False,
        "steps": [
            ("collab", "ask_question", {"question": "who owns staging?", "asked_by": "agent"}),
            ("work", "create_task", {"title": "golden task"}),
            ("platform", "raise_blocker", {"title": "golden blocker"}),
            ("collab", "record_decision", {"title": "golden", "decision": "we pin traces"}),
        ],
        "expect_tables": {"questions": 1, "tasks": 1, "blockers": 1, "decisions": 1},
        "expect_pending": 0,
    },
    {
        "name": "review-mode-queues-instead-of-writing",
        "review": True,
        "steps": [
            ("work", "create_task", {"title": "should be queued"}),
            ("collab", "record_decision", {"title": "queued too", "decision": "x"}),
        ],
        "expect_tables": {},
        "expect_pending": 2,
        "forbid": ["tasks", "decisions"],
    },
    {
        "name": "forbidden-refuses-and-writes-nothing",
        "review": False,
        "authority": [("agent", "task", "forbidden")],
        "steps": [
            ("work", "create_task", {"title": "must be refused"}),
        ],
        "expect_tables": {},
        "expect_pending": 0,
        "forbid": ["tasks", "pending_changes"],
        "expect_error": "forbidden",
    },
    {
        "name": "waiting-on-through-the-tool-layer",
        "review": False,
        "steps": [
            ("work", "create_task", {"title": "downstream"}),
            ("platform", "raise_blocker", {"title": "upstream dep"}),
            ("work", "update_task", {"task_id": 1, "waiting_on": "blocker:1"}),
            ("platform", "resolve_blocker", {"blocker_id": 1, "resolution": "unblocked"}),
        ],
        "expect_tables": {"tasks": 1, "blockers": 1},
        "expect_pending": 0,
        "expect_sql": [
            ("SELECT waiting_on_type FROM tasks WHERE id = 1", "waiting_on_type", "blocker"),
            ("SELECT status FROM blockers WHERE id = 1", "status", "resolved"),
        ],
    },
]


def _tool_fn(module: str, name: str):
    from app.tools import collab, work
    from app.tools import platform as platform_tools

    mod = {"collab": collab, "work": work, "platform": platform_tools}[module]
    return getattr(mod, name)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s["name"])
def test_golden_trace(scenario, fresh_db, monkeypatch):
    from app import config
    from app.services import notifications
    from app.services.delegation import set_authority

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    monkeypatch.setattr(config, "AGENT_REVIEW", scenario["review"])
    for agent, entity, level in scenario.get("authority", []):
        set_authority(agent, entity, level, actor="golden-human")

    last_error = ""
    for module, tool_name, kwargs in scenario["steps"]:
        out = _tool_fn(module, tool_name)(**kwargs)
        try:
            parsed = json.loads(out)
            if isinstance(parsed, dict) and parsed.get("error"):
                last_error = parsed["error"]
        except (json.JSONDecodeError, TypeError):
            pass

    if "expect_error" in scenario:
        assert scenario["expect_error"] in last_error, f"wanted refusal, got: {last_error!r}"

    for table, count in scenario.get("expect_tables", {}).items():
        n = fresh_db.query_row(f"SELECT COUNT(*) AS n FROM {table}")["n"]  # noqa: S608
        assert n == count, f"{table}: expected {count}, got {n}"
    for table, at_least in scenario.get("expect_min", {}).items():
        n = fresh_db.query_row(f"SELECT COUNT(*) AS n FROM {table}")["n"]  # noqa: S608
        assert n >= at_least, f"{table}: expected >= {at_least}, got {n}"
    for table in scenario.get("forbid", []):
        rows = fresh_db.query(f"SELECT * FROM {table}")  # noqa: S608
        assert rows == [], f"{table} must stay empty in scenario {scenario['name']}: {rows}"
    pending = fresh_db.query_row(
        "SELECT COUNT(*) AS n FROM pending_changes WHERE status = 'pending'"
    )["n"]
    assert pending == scenario["expect_pending"]
    for sql, col, expected in scenario.get("expect_sql", []):
        assert fresh_db.query_row(sql)[col] == expected


def test_golden_review_roundtrip(fresh_db, monkeypatch):
    """The full trajectory: agent proposes under review mode, human approves,
    the write lands with origin=agent_verified — the trust flywheel's one loop."""
    from app import config
    from app.services import notifications, review
    from app.tools import work as tw

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    monkeypatch.setattr(config, "AGENT_REVIEW", True)
    out = json.loads(tw.create_task(title="proposed by agent"))
    # pin the actual tool-output contract (gated_write review path)
    assert out.get("status") == "pending"
    assert out.get("note") == "queued for human review"
    assert fresh_db.query("SELECT * FROM tasks") == []
    p = fresh_db.query_row("SELECT id FROM pending_changes WHERE status = 'pending'")
    review.approve_change(p["id"], actor="human-reviewer")
    task = fresh_db.query_row("SELECT * FROM tasks")
    assert task["origin"] == "agent_verified"
    assert task["title"] == "proposed by agent"
