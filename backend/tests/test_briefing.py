"""My Day: attention grouping and reasons, due-soon scoping, notice coalescing
and caps, the _ellipsize/_coalesce digest helpers, and the standup suggestion
derived from your own activity."""

from datetime import UTC, datetime


def _utc_today():
    return datetime.now(UTC).date()


def _notice_items(client):
    b = client.get("/api/briefing").json()
    return [a for a in b["attention"] if a["kind"] == "notification"]


def test_due_soon_excludes_other_peoples_tasks(fresh_db):
    from app.services import briefing, work

    today = _utc_today().isoformat()
    work.create_task("mine", assignee="ava", due_date=today)
    work.create_task("unassigned", due_date=today)
    work.create_task("bobs", assignee="bob", due_date=today)

    titles = {t["title"] for t in briefing.my_day("ava")["your_work"]["due_soon"]}
    assert titles == {"mine", "unassigned"}


def test_attention_groups_and_reasons(client, fresh_db):
    client.post("/api/questions", json={"question": "who owns infra?", "assigned_to": "tester"})
    client.post("/api/blockers", json={"title": "stuck on vendor", "owner": "tester"})
    client.post("/api/commitments", json={"promise": "beta date", "due_date": "2020-01-01"})
    b = client.get("/api/briefing").json()
    groups = {a["group"] for a in b["attention"]}
    assert {"unblock", "commit"} <= groups
    assert all(a["reason"] for a in b["attention"])
    overdue = [a for a in b["attention"] if a["group"] == "commit"]
    assert "OVERDUE" in overdue[0]["reason"]


def test_ellipsize_short_and_exact_strings_untouched():
    from app.services.briefing import _ellipsize

    assert _ellipsize("hello", 100) == "hello"
    exact = "x" * 100
    assert _ellipsize(exact, 100) == exact


def test_ellipsize_cuts_at_word_boundary():
    from app.services.briefing import _ellipsize

    text = ("word " * 40).strip()
    out = _ellipsize(text, 100)
    assert out.endswith("…") and len(out) <= 100
    assert set(out[:-1].split(" ")) == {"word"}  # never a partial word


def test_ellipsize_strips_dangling_separators():
    from app.services.briefing import _ellipsize

    text = "x" * 95 + " — trailing tail"
    assert _ellipsize(text, 100) == "x" * 95 + "…"


def test_ellipsize_single_long_word_hard_cuts():
    from app.services.briefing import _ellipsize

    assert _ellipsize("y" * 150, 100) == "y" * 99 + "…"


def test_coalesce_stacks_near_duplicates():
    from app.services.briefing import _coalesce

    n1 = {"id": 3, "message": "claude ingested notes: standup", "link": "/ingest"}
    n2 = {"id": 2, "message": "claude ingested notes: retro", "link": "/ingest"}
    n3 = {"id": 1, "message": "blocker resolved: CI", "link": "/dashboard"}
    assert _coalesce([n1, n2, n3]) == [(n1, 1), (n3, 0)]


def test_coalesce_same_prefix_different_link_stays_separate():
    from app.services.briefing import _coalesce

    a = {"id": 2, "message": "ingested: a", "link": "/x"}
    b = {"id": 1, "message": "ingested: b", "link": "/y"}
    assert _coalesce([a, b]) == [(a, 0), (b, 0)]


def test_briefing_coalesces_and_resurfaces_on_dismiss(client):
    from app.services import notifications

    for suffix in ("standup", "retro", "planning"):
        notifications.notify("tester", f"agent ingested notes: {suffix}", link="/ingest")

    items = _notice_items(client)
    assert len(items) == 1
    assert items[0]["label"].endswith("(+2 similar)")
    assert items[0]["reason"] == "for you — dismiss when read"

    client.post("/api/notifications/read", json={"notification_id": items[0]["ref_id"]})
    items = _notice_items(client)
    assert len(items) == 1
    assert items[0]["label"].endswith("(+1 similar)")


def test_briefing_notice_cap_applies_after_coalescing(client):
    from app.services import notifications

    for i in range(6):
        notifications.notify("tester", f"distinct thing {i} happened", link=f"/l{i}")
    for suffix in ("a", "b", "c"):
        notifications.notify("tester", f"agent ingested notes: {suffix}", link="/ingest")

    items = _notice_items(client)
    assert len(items) == 5
    assert items[0]["label"].endswith("(+2 similar)")  # dupes consume one slot, not three
    assert sum("distinct thing" in i["label"] for i in items) == 4


def test_briefing_team_notification_reason_label(client):
    from app.services import notifications

    notifications.notify("team", "all hands moved to Friday", link="/")
    items = _notice_items(client)
    assert items[0]["reason"] == "for the whole team — dismiss when read"


def test_standup_suggestion_derives_from_activity(fresh_db):
    from app.services import briefing

    uuid = "123e4567-e89b-12d3-a456-426614174000"
    fresh_db.log_activity("dana", "create_task", "#12 ship the API")
    fresh_db.log_activity("dana", "capture", f"note {uuid}")
    fresh_db.log_activity("dana", "delete_chat", "old thread")  # housekeeping: excluded
    fresh_db.log_activity("dana", "request_key", "asked for a personal API key")  # excluded

    s = briefing.my_day("dana")["your_work"]["standup_suggestion"]
    assert s == "capture note …; create task #12 ship the API"


def test_standup_suggestion_empty_without_activity(fresh_db):
    from app.services import briefing

    assert briefing.my_day("ghost")["your_work"]["standup_suggestion"] == ""


def test_standup_suggestion_caps_at_three_items(fresh_db):
    from app.services import briefing

    for i in range(5):
        fresh_db.log_activity("dana", "capture", f"item {i}")
    s = briefing.my_day("dana")["your_work"]["standup_suggestion"]
    assert s.count(";") == 2
    assert s.startswith("capture item 4")


def test_your_work_lists_are_capped(fresh_db):
    """The dashboard home path. Unbounded, a team with thousands of stale
    overdue rows was served every one as SELECT * on every load, for every
    user. due_soon caps at 50, the task list at 200."""
    from app.services import briefing

    today = _utc_today().isoformat()
    for i in range(260):
        fresh_db.execute(
            "INSERT INTO tasks (title, assignee, due_date, created_at, updated_at)"
            " VALUES (?, 'ava', ?, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')",
            (f"t{i}", today if i < 55 else None),
        )
    work = briefing.my_day("ava")["your_work"]
    assert len(work["due_soon"]) == 50
    assert len(work["tasks"]) == 200


def test_due_soon_reads_the_assignee_index(fresh_db):
    """Migration 044 exists for this query shape; without the index the plan
    is SCAN tasks, measured live on the finding this pins. The SQL here
    mirrors briefing.my_day's due_soon — if that query drifts, this still
    holds the index to its purpose."""
    plan = " ".join(
        r["detail"]
        for r in fresh_db.query(
            "EXPLAIN QUERY PLAN SELECT * FROM tasks WHERE status != 'done'"
            " AND due_date IS NOT NULL AND due_date <= ? AND assignee IN (?, '')"
            " ORDER BY due_date LIMIT 50",
            ("2026-01-01", "ava"),
        )
    )
    assert "idx_tasks_assignee_due" in plan
    assert "SCAN tasks" not in plan
