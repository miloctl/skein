"""Test-image entrypoint. Production images never import this module."""

# ruff: noqa: S101, S310 — assertions and fixed HTTP targets in a test-only image
import json
import os
import urllib.request
from dataclasses import replace

from psycopg.conninfo import conninfo_to_dict

from app import config, db

# This runs before main imports or startup SQL. An inherited full URL otherwise
# outranks the owned split credentials and can migrate a deployment database.
_selected_database = conninfo_to_dict(config.DATABASE_URL) if not config.DATABASE_ERROR else {}
_owned_database = {
    key: os.environ[variable]
    for key, variable in (
        ("host", "SKEIN_DB_HOST"),
        ("port", "SKEIN_DB_PORT"),
        ("user", "SKEIN_DB_USER"),
        ("password", "SKEIN_DB_PASSWORD"),
        ("dbname", "SKEIN_DB_NAME"),
    )
}
if (
    any(_selected_database.get(key) != value for key, value in _owned_database.items())
    or _selected_database.get("hostaddr")
    or _selected_database.get("service")
):
    raise RuntimeError(
        "The test database configuration is not allowed. Check the test image environment."
    )

from app.agents import receipts, session_log, team_agent  # noqa: E402 — database ownership first
from app.services import artifact_files, forge, jobs  # noqa: E402 — database ownership first

UPSTREAM = os.environ["DURABILITY_UPSTREAM"]


def remote(path, payload=None):
    request = urllib.request.Request(
        UPSTREAM + path,
        data=json.dumps(payload or {}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=240) as response:
        return json.load(response)


class BlockingWorker:
    def __init__(self, thread, agent):
        self.thread = thread
        self.agent = agent

    def __call__(self, message, **kwargs):
        from app.tools.portfolio import claim_delegated_task, report_progress

        task = db.query_row(
            "SELECT trigger_task_id FROM agent_wakeups WHERE agent = ?", (self.agent,)
        )["trigger_task_id"]
        receipts.start()
        claimed = json.loads(claim_delegated_task(task))
        assert "error" not in claimed, claimed
        before = json.loads(report_progress(task, "durability before provider"))
        assert "error" not in before, before
        session_log._append_exchange(self.thread, "before provider", "started")
        remote(
            f"/gate/{task}",
            {
                "thread": self.thread,
                "pod": os.environ["DURABILITY_POD"],
                "receipts": receipts.drain(),
            },
        )
        results = {}
        for name, action in (
            ("tool", lambda: report_progress(task, "durability after provider")),
            (
                "session",
                lambda: session_log._append_exchange(self.thread, "after provider", "finished"),
            ),
        ):
            try:
                results[name] = action()
            except Exception as exc:
                results[name] = type(exc).__name__
        remote(f"/observed/{task}", {"results": results, "receipts": receipts.drain()})
        if "LeaseLost" in results.values():
            raise RuntimeError("test worker lost its lease")
        return "Deterministic work finished."

    def cancel(self):
        # A remote provider is not required to honor cancellation. The resumed
        # worker must still meet the production lease fence before every write.
        pass


__all__ = ["app"]


def build_agent(thread_id, *args, **kwargs):
    if thread_id.startswith("wake:"):
        return BlockingWorker(thread_id, kwargs["user"])
    from app.agents.mock_agent import MockAgent

    return MockAgent(thread_id, user=kwargs.get("user", ""))


# Native mock refuses unattended work. Only this test image substitutes a
# deterministic provider, leaving run_one, tools, policy, leases and storage real.
config.EFFECTIVE_PROVIDER = "openai_compatible"
team_agent.build_agent = build_agent
original_forge_event = forge.forge_event


def forge_event(*args, **kwargs):
    result = original_forge_event(*args, **kwargs)
    if "before-commit" in kwargs.get("branch", ""):
        remote("/before-commit", {"result": result})
    return result


forge.forge_event = forge_event
original_publish = artifact_files.publish


def publish(path, content, **kwargs):
    try:
        return original_publish(path, content, **kwargs)
    except OSError as exc:
        # An unrelated DB failure also returns 500 before publication. Only
        # this observation proves the intended filesystem fault was reached.
        remote(
            "/observed/storage",
            {
                "errno": exc.errno,
                "directory": str(path.parent),
                "pod": os.environ["DURABILITY_POD"],
            },
        )
        raise


artifact_files.publish = publish
jobs.JOBS = (
    replace(
        jobs.JOBS[0],
        fn=lambda: remote("/effect/job", {"pid": os.getpid()}),
        trigger={"trigger": "interval", "seconds": 5},
        period_hours=24,
        catch_up=False,
        retry_safe=False,
    ),
)

from app.main import app  # noqa: E402 — fixtures must be installed before composition
