"""Task collection state filters apply before the Browse collection limit."""


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
