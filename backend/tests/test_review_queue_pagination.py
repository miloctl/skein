"""The pending review queue is FIFO, cursor-reachable, and viewer-scoped."""


def test_pending_review_pages_match_the_my_day_preview(client):
    from app.services import review, users

    users.ensure_user("queue-agent", kind="agent")
    expected = [
        review.propose_change(
            "note",
            "create",
            {"topic": f"queue {i}", "content": "review me"},
            actor="queue-agent",
            notify_team=False,
        )["id"]
        for i in range(75)
    ]

    first = client.get("/api/review?status=pending").json()
    first_ids = [row["id"] for row in first if row["id"] in expected]
    assert first_ids == expected[:50]

    second = client.get(f"/api/review?status=pending&after={first[-1]['id']}").json()
    second_ids = [row["id"] for row in second if row["id"] in expected]
    assert second_ids == expected[50:]
    assert len(first_ids + second_ids) == len(set(first_ids + second_ids)) == 75

    day = client.get("/api/briefing").json()
    preview = [row["id"] for row in day["needs_you"]["pending_reviews"] if row["id"] in expected]
    assert preview == first_ids
    assert day["pending_reviews_total"] == 75
    assert client.get("/api/attention").json()["inbox"] == 75
    assert client.get("/api/review?after=-1").status_code == 422


def test_pending_review_page_scans_past_private_rows(client):
    from app.services import review, users

    users.ensure_user("queue-agent", kind="agent")
    for i in range(55):
        review.propose_change(
            "note",
            "create",
            {
                "topic": f"private queue {i}",
                "content": "private",
                "visibility": "private",
                "author": "other",
            },
            actor="queue-agent",
            notify_team=False,
            review_visibility="private",
            review_owner="other",
        )
    visible = review.propose_change(
        "note",
        "create",
        {"topic": "visible after private rows", "content": "workspace"},
        actor="queue-agent",
        notify_team=False,
    )["id"]

    rows = client.get("/api/review?status=pending&limit=1").json()
    assert [row["id"] for row in rows] == [visible]


def test_review_route_keeps_legacy_status_limits(client, monkeypatch):
    from app.services import review

    calls = []

    def list_changes(status, viewer, **kwargs):
        calls.append((status, kwargs["limit"]))
        return []

    monkeypatch.setattr(review, "list_changes", list_changes)
    assert client.get("/api/review").status_code == 200
    assert client.get("/api/review?status=approved").status_code == 200
    assert client.get("/api/review?status=").status_code == 200
    assert client.get("/api/review?status=rejected&limit=7").status_code == 200
    assert calls == [("pending", 50), ("approved", 200), ("", 100), ("rejected", 7)]


def test_settled_history_scans_past_workplace_denials(client, fresh_db, monkeypatch):
    from app.services import projection_policy, review, users, work

    users.ensure_user("history-agent", kind="agent")
    proposals = []
    tasks = []
    for index in range(51):
        task_id = work.create_task(title=f"history task {index}", actor="tester")["id"]
        tasks.append(task_id)
        proposals.append(
            review.propose_change(
                "task",
                "update",
                {"title": f"reviewed task {index}"},
                entity_id=task_id,
                actor="history-agent",
                notify_team=False,
            )["id"]
        )
    fresh_db.execute(
        "UPDATE pending_changes SET status = 'approved' WHERE proposed_by = ?", ("history-agent",)
    )
    denied = set(tasks[1:])
    original = projection_policy.ProjectionPolicy.permits

    def permits(self, entity, entity_id, attributes):
        if self.action == "skein.rest.get.review" and entity == "task" and entity_id in denied:
            return False
        return original(self, entity, entity_id, attributes)

    monkeypatch.setattr(projection_policy.ProjectionPolicy, "permits", permits)
    rows = client.get("/api/review?status=approved").json()
    assert [row["id"] for row in rows if row["proposed_by"] == "history-agent"] == [proposals[0]]


def test_attention_and_briefing_use_the_same_review_policy(client, monkeypatch):
    from app.services import projection_policy, review, users, work

    users.ensure_user("policy-agent", kind="agent")
    task_id = work.create_task(title="briefing-only denial", actor="tester")["id"]
    review.propose_change(
        "task",
        "update",
        {"title": "denied from the briefing"},
        entity_id=task_id,
        actor="policy-agent",
        requested_by="tester",
        notify_team=False,
    )
    original = projection_policy.ProjectionPolicy.permits

    def permits(self, entity, entity_id, attributes):
        if self.action == "skein.rest.get.briefing" and entity == "task" and entity_id == task_id:
            return False
        return original(self, entity, entity_id, attributes)

    monkeypatch.setattr(projection_policy.ProjectionPolicy, "permits", permits)
    assert client.get("/api/briefing").json()["attention_total"] == 0
    assert client.get("/api/attention").json()["count"] == 0


def test_review_summary_batches_target_tier_reads(fresh_db, monkeypatch):
    from app.services import review, scope, users, work

    users.ensure_user("batch-agent", kind="agent")
    for index in range(10):
        task_id = work.create_task(title=f"batch target {index}", actor="tester")["id"]
        review.propose_change(
            "task",
            "update",
            {"title": f"batch proposal {index}"},
            entity_id=task_id,
            actor="batch-agent",
            notify_team=False,
        )

    queries = []
    original = fresh_db.query

    def counted(sql, params=()):
        queries.append(sql)
        return original(sql, params)

    monkeypatch.setattr(fresh_db, "query", counted)
    first, total = review.pending_changes_summary(scope.Viewer("tester", True), limit=5)

    assert len(first) == 5 and total == 10
    assert sum("FROM tasks WHERE id IN" in sql for sql in queries) == 1
    assert not [sql for sql in queries if "FROM tasks WHERE id = ?" in sql]
