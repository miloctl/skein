"""Task collection state filters apply before the Browse collection limit."""

import json

import pytest


def test_mcp_pages_past_the_cap_and_denials_without_skipping_visible_tasks(fresh_db, monkeypatch):
    from app import mcp_server
    from app.extensions import ExtensionRegistry, PolicyContribution, SkeinModule
    from app.extensions.policy import PolicyDecision, PolicyEffect
    from app.services import engagements, users, work

    users.ensure_user("owner")
    regulated = engagements.create_engagement("Restricted", project_class="regulated")["id"]
    standard = engagements.create_engagement("Shared", project_class="standard")["id"]
    with fresh_db.transaction():
        for n in range(520):
            work.create_task(f"Private {n}", actor="owner", visibility="private", priority="urgent")
            work.create_task(f"Denied {n}", engagement_id=regulated, priority="urgent")
        expected = [
            work.create_task(f"Visible {n}", engagement_id=standard, assignee="owner")["id"]
            for n in range(511)
        ]

    def deny_regulated(request):
        if request.resource.project_type == "regulated":
            return PolicyDecision(PolicyEffect.DENY)
        return None

    registry = ExtensionRegistry.build(
        (
            SkeinModule(
                module_id="test.task-pages",
                version="1.0.0",
                extension_api="1.0",
                minimum_core="0.2.0",
                maximum_core_exclusive="0.6.0",
                policies=(PolicyContribution("test.task-pages.read", deny_regulated),),
            ),
        )
    )
    monkeypatch.setattr(mcp_server, "current_policy_engine", lambda: registry.policy_engine)
    query = fresh_db.query
    query_one = fresh_db.query_one
    queries = []
    row_counts = []

    def counted_query(sql, params=()):
        rows = query(sql, params)
        queries.append(sql)
        row_counts.append(len(rows))
        return rows

    def counted_query_one(sql, params=()):
        queries.append(sql)
        return query_one(sql, params)

    monkeypatch.setattr(fresh_db, "query", counted_query)
    monkeypatch.setattr(fresh_db, "query_one", counted_query_one)
    seen = []
    for offset in (0, 200, 400, 511):
        queries.clear()
        row_counts.clear()
        page = json.loads(mcp_server.list_tasks(limit=200, offset=offset))
        assert [row["id"] for row in page] == expected[offset : offset + 200]
        assert all("milestone_title" not in row for row in page)
        seen.extend(row["id"] for row in page)
        assert len(queries) <= 24, queries
        assert max(row_counts, default=0) <= work.TASK_LIST_LIMIT
    assert seen == expected
    assert len(set(seen)) == len(seen)
    assert len(work.list_tasks()) == work.TASK_LIST_LIMIT
    assert json.loads(mcp_server.list_tasks(status="unknown")) == []
    assert json.loads(mcp_server.list_tasks(status="open")) == []
    assert json.loads(mcp_server.list_tasks(assignee="unknown")) == []
    filtered = json.loads(mcp_server.list_tasks(status="todo", assignee="owner", offset=500))
    assert [row["id"] for row in filtered] == expected[500:]
    from app.tools import work as work_tools

    monkeypatch.setattr(work_tools, "current_policy_engine", lambda: registry.policy_engine)
    tool_rows = json.loads(work_tools.list_tasks())
    assert [row["id"] for row in tool_rows] == expected[: work.TASK_LIST_LIMIT]


def test_task_state_filters_run_before_the_collection_limit(client, monkeypatch):
    from app.services import work

    monkeypatch.setattr(work, "TASK_LIST_LIMIT", 2)
    first = work.create_task(title="older finished", priority="urgent", actor="tester")
    work.update_task(first["id"], status="done", actor="tester")
    second = work.create_task(title="newer finished", priority="urgent", actor="tester")
    work.update_task(second["id"], status="done", actor="tester")
    active = work.create_task(title="active low-priority work", priority="low", actor="tester")

    default = client.get("/api/tasks").json()
    assert isinstance(default, list)
    assert [row["id"] for row in default] == [first["id"], second["id"]]

    open_rows = client.get("/api/tasks?status=open").json()
    assert [row["id"] for row in open_rows] == [active["id"]]

    done_rows = client.get("/api/tasks?status=done&order=completed").json()
    assert [row["id"] for row in done_rows] == [second["id"], first["id"]]
    assert client.get("/api/tasks?status=todo").status_code == 422


def test_workplace_denials_do_not_consume_the_task_limit(client, monkeypatch):
    from app.services import projection_policy, work

    monkeypatch.setattr(work, "TASK_LIST_LIMIT", 2)
    denied = {
        work.create_task(title="denied urgent one", priority="urgent", actor="tester")["id"],
        work.create_task(title="denied urgent two", priority="urgent", actor="tester")["id"],
    }
    visible = work.create_task(title="visible low task", priority="low", actor="tester")["id"]
    original = projection_policy.ProjectionPolicy.permits

    def permits(self, entity, entity_id, attributes):
        if self.action == "skein.rest.get.tasks" and entity == "task" and entity_id in denied:
            return False
        return original(self, entity, entity_id, attributes)

    monkeypatch.setattr(projection_policy.ProjectionPolicy, "permits", permits)
    assert [row["id"] for row in client.get("/api/tasks?status=open").json()] == [visible]


def test_browse_task_slices_share_one_snapshot(client, monkeypatch):
    from threading import Event, Thread

    from app.services import work

    task_id = work.create_task(title="changes during browse", actor="tester")["id"]
    first_read = Event()
    writer_done = Event()
    original = work.list_tasks_joined
    paused = False

    def list_rows(*args, **kwargs):
        nonlocal paused
        rows = original(*args, **kwargs)
        if kwargs.get("status") == "open" and not paused:
            paused = True
            first_read.set()
            assert writer_done.wait(5)
        return rows

    def finish():
        assert first_read.wait(5)
        work.update_task(task_id, status="done", actor="tester")
        writer_done.set()

    monkeypatch.setattr(work, "list_tasks_joined", list_rows)
    writer = Thread(target=finish)
    writer.start()
    browse = client.get("/api/tasks/browse").json()
    writer.join(5)

    seen = [row["id"] for row in [*browse["open"], *browse["done"]]]
    assert seen.count(task_id) == 1


def test_policy_filtered_task_pages_stop_at_the_scan_budget(fresh_db, monkeypatch):
    from app.services import work

    with fresh_db.transaction():
        for n in range(work.TASK_LIST_LIMIT + 60):
            work.create_task(f"Denied {n}", actor="owner", assignee="owner")
    monkeypatch.setattr(work, "TASK_SCAN_LIMIT", work.TASK_LIST_LIMIT + 40)

    def deny_all(_kind, _task_id, _context):
        return False

    with pytest.raises(ValueError, match="smaller offset"):
        work.list_tasks(resource_filter=deny_all)
    with pytest.raises(ValueError, match="smaller offset"):
        work.list_tasks(offset=10**9, resource_filter=lambda *_args: True)
    assert len(work.list_tasks(limit=20, resource_filter=lambda *_args: True)) == 20
