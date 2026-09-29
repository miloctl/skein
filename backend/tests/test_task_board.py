"""The board read: open work and the last week of Done, under the same
visibility and workplace policy as Browse (docs/intent/board-view.md)."""

from datetime import UTC, datetime, timedelta

BOARD_FIELDS = {
    "id",
    "title",
    "status",
    "priority",
    "assignee",
    "due_date",
    "completed_at",
    "forge_url",
    "visibility",
    "crew_id",
    "committed_week",
    "delegated_agent",
    "waiting_on_type",
    "waiting_on_id",
    "quiet_days",
    "blockers",
}


def _set(db, task_id, column, value):
    db.execute(f"UPDATE tasks SET {column} = ? WHERE id = ?", (value, task_id))  # noqa: S608 — test-controlled column


def _ids(rows):
    return [row["id"] for row in rows]


def test_board_projects_the_card_fields_and_nothing_else(client, fresh_db):
    from app.services import blockers, work

    task = work.create_task("Card", actor="tester")
    blocker = blockers.raise_blocker("Vendor key", task_id=task["id"], actor="tester")
    body = client.get("/api/tasks/board").json()
    assert body["scope"] is None
    assert body["limit"] == work.TASK_LIST_LIMIT and body["done_days"] == work.BOARD_DONE_DAYS
    card = body["open"][0]
    assert set(card) == BOARD_FIELDS
    assert card["blockers"] == [{"id": blocker["id"], "title": "Vendor key"}]
    assert card["status"] == "blocked"


def test_engagement_board_holds_direct_and_milestone_tasks(client):
    from app.services import engagements, work

    milestone = work.create_milestone("Cutover", project="Atlas", actor="tester")
    atlas = engagements.create_engagement("Atlas")
    other = engagements.create_engagement("Borealis")
    direct = work.create_task("Direct", engagement_id=atlas["id"], actor="tester")
    through = work.create_task("Through", milestone_id=milestone["id"], actor="tester")
    elsewhere = work.create_task("Elsewhere", engagement_id=other["id"], actor="tester")
    loose = work.create_task("Loose", actor="tester")

    body = client.get(f"/api/tasks/board?engagement_id={atlas['id']}").json()
    assert body["scope"] == {"kind": "engagement", "id": atlas["id"], "title": "Atlas"}
    assert sorted(_ids(body["open"])) == sorted([direct["id"], through["id"]])
    assert elsewhere["id"] not in _ids(body["open"]) and loose["id"] not in _ids(body["open"])

    body = client.get(f"/api/tasks/board?milestone_id={milestone['id']}").json()
    assert body["scope"] == {"kind": "milestone", "id": milestone["id"], "title": "Cutover"}
    assert _ids(body["open"]) == [through["id"]]


def test_hidden_scope_answers_like_an_absent_one(client):
    from app.services import engagements, users, work

    users.ensure_user("ava")
    hidden = engagements.create_engagement("Private plan", actor="ava", visibility="private")
    milestone = work.create_milestone("Secret", actor="ava", visibility="private")
    for kind, row_id, absent in (
        ("engagement_id", hidden["id"], 999_999),
        ("milestone_id", milestone["id"], 999_998),
    ):
        seen = client.get(f"/api/tasks/board?{kind}={row_id}")
        gone = client.get(f"/api/tasks/board?{kind}={absent}")
        assert seen.status_code == gone.status_code == 404
        assert seen.json()["detail"].replace(str(row_id), "#") == gone.json()["detail"].replace(
            str(absent), "#"
        )
    both = client.get(f"/api/tasks/board?engagement_id=1&milestone_id={milestone['id']}")
    assert both.status_code == 400


def test_done_holds_the_last_seven_days(client, fresh_db):
    from app.services import work

    inside = work.create_task("Inside", actor="tester")
    outside = work.create_task("Outside", actor="tester")
    for task in (inside, outside):
        work.update_task(task["id"], status="done", actor="tester")
    now = datetime.now(UTC)
    _set(
        fresh_db,
        inside["id"],
        "completed_at",
        (now - timedelta(days=6, hours=23)).isoformat(timespec="seconds"),
    )
    _set(
        fresh_db,
        outside["id"],
        "completed_at",
        (now - timedelta(days=7, hours=1)).isoformat(timespec="seconds"),
    )
    assert _ids(client.get("/api/tasks/board").json()["done"]) == [inside["id"]]


def test_quiet_days_marks_only_in_progress_past_the_stale_cutoff(client, fresh_db):
    from app.services import slas, work

    cutoff = datetime.fromisoformat(
        fresh_db.local_midnight_utc(fresh_db.today() - timedelta(days=slas.STALE_WIP_DAYS))
    )
    stale = work.create_task("Stale", actor="tester")
    fresh = work.create_task("Fresh", actor="tester")
    old_todo = work.create_task("Old todo", actor="tester")
    for task in (stale, fresh):
        work.update_task(task["id"], status="in_progress", actor="tester")
    _set(
        fresh_db,
        stale["id"],
        "updated_at",
        (cutoff - timedelta(hours=1)).isoformat(timespec="seconds"),
    )
    _set(
        fresh_db,
        fresh["id"],
        "updated_at",
        (cutoff + timedelta(hours=1)).isoformat(timespec="seconds"),
    )
    _set(
        fresh_db,
        old_todo["id"],
        "updated_at",
        (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="seconds"),
    )
    cards = {row["id"]: row for row in client.get("/api/tasks/board").json()["open"]}
    expected = (datetime.now(UTC) - (cutoff - timedelta(hours=1))).days
    assert cards[stale["id"]]["quiet_days"] == expected >= slas.STALE_WIP_DAYS
    assert cards[fresh["id"]]["quiet_days"] is None
    assert cards[old_todo["id"]]["quiet_days"] is None


def test_another_persons_private_blocker_stays_off_the_card(client):
    from app.services import blockers, users, work

    users.ensure_user("ava")
    task = work.create_task("Shared", actor="tester")
    blockers.raise_blocker("Ava's secret", task_id=task["id"], actor="ava", visibility="private")
    shown = blockers.raise_blocker("Vendor key", task_id=task["id"], actor="tester")
    card = client.get("/api/tasks/board").json()["open"][0]
    assert card["blockers"] == [{"id": shown["id"], "title": "Vendor key"}]


def test_workplace_policy_removes_a_row_and_a_blocker(client, monkeypatch):
    from app.services import blockers, engagements, projection_policy, work

    regulated = engagements.create_engagement("Restricted project", project_class="regulated")
    work.create_task("Denied", engagement_id=regulated["id"], actor="tester")
    visible = work.create_task("Permitted", actor="tester")
    denied_blocker = blockers.raise_blocker("Denied blocker", task_id=visible["id"], actor="tester")
    original = projection_policy.ProjectionPolicy.permits

    def permits(self, entity, entity_id, attributes):
        assert self.action == "skein.rest.get.tasks"
        if attributes.get("project_type") == "regulated":
            return False
        if entity == "blocker" and entity_id == denied_blocker["id"]:
            return False
        return original(self, entity, entity_id, attributes)

    monkeypatch.setattr(projection_policy.ProjectionPolicy, "permits", permits)
    body = client.get("/api/tasks/board").json()
    assert _ids(body["open"]) == [visible["id"]]
    assert body["open"][0]["blockers"] == []
    denied_scope = client.get(f"/api/tasks/board?engagement_id={regulated['id']}")
    assert denied_scope.status_code == 404


def test_mine_holds_only_the_callers_tasks(client):
    from app.services import users, work

    users.ensure_user("ava")
    users.ensure_user("tester")
    mine = work.create_task("Mine", assignee="tester", actor="tester")
    work.create_task("Hers", assignee="ava", actor="tester")
    work.create_task("Nobody's", actor="tester")
    assert _ids(client.get("/api/tasks/board?mine=true").json()["open"]) == [mine["id"]]
    assert len(client.get("/api/tasks/board").json()["open"]) == 3


def test_a_move_compares_the_status_the_board_loaded(client, fresh_db):
    from app.services import work

    task = work.create_task("Card", actor="tester")
    work.update_task(task["id"], status="done", actor="tester")
    ledger = fresh_db.query_one("SELECT count(*) AS n FROM activity")["n"]
    stale = client.patch(
        f"/api/tasks/{task['id']}", json={"status": "in_progress", "expected_status": "todo"}
    )
    assert stale.status_code == 409
    assert stale.json()["detail"] == (
        f"Task #{task['id']} changed after you loaded the board. It is now done."
        " Move it again from the current board."
    )
    assert work.get_task(task["id"])["status"] == "done"
    assert fresh_db.query_one("SELECT count(*) AS n FROM activity")["n"] == ledger

    moved = client.patch(
        f"/api/tasks/{task['id']}", json={"status": "in_progress", "expected_status": "done"}
    )
    assert moved.status_code == 200
    assert work.get_task(task["id"])["status"] == "in_progress"
    # a status reads as words, never as the stored identifier
    again = client.patch(
        f"/api/tasks/{task['id']}", json={"status": "done", "expected_status": "todo"}
    )
    assert "It is now in progress." in again.json()["detail"]
    typo = client.patch(
        f"/api/tasks/{task['id']}", json={"status": "todo", "expected_status": "in-progress"}
    )
    assert typo.status_code == 400 and "in-progress" not in typo.json()["detail"]


def test_the_agent_tool_takes_no_expected_status():
    import inspect

    from app.tools import work as work_tools

    fn = getattr(work_tools.update_task, "_tool_func", work_tools.update_task)
    assert "expected_status" not in inspect.signature(fn).parameters


def test_resolving_says_whether_the_task_moved(client):
    from app.services import blockers, work

    task = work.create_task("Card", actor="tester")
    first = blockers.raise_blocker("One", task_id=task["id"], actor="tester")
    last = blockers.raise_blocker("Two", task_id=task["id"], actor="tester")
    one = client.post(f"/api/blockers/{first['id']}/resolve", json={"resolution": "done"})
    assert one.json()["task_unblocked"] is False
    two = client.post(f"/api/blockers/{last['id']}/resolve", json={"resolution": "done"})
    assert two.json()["task_unblocked"] is True
    assert work.get_task(task["id"])["status"] == "in_progress"


def test_two_last_resolves_at_once_release_the_task(fresh_db):
    import threading

    from app.services import blockers, work

    task = work.create_task("Card", actor="tester")
    first = blockers.raise_blocker("One", task_id=task["id"], actor="tester")
    last = blockers.raise_blocker("Two", task_id=task["id"], actor="tester")
    first_resolved = threading.Event()
    released: dict[str, bool] = {}
    errors: list[Exception] = []

    def resolve_last():
        try:
            first_resolved.wait(timeout=3)
            released["last"] = blockers.resolve_blocker(last["id"], actor="tester")[
                "task_unblocked"
            ]
        except Exception as exc:
            errors.append(exc)

    other = threading.Thread(target=resolve_last)
    other.start()
    with fresh_db.transaction():
        released["first"] = blockers.resolve_blocker(first["id"], actor="tester")["task_unblocked"]
        first_resolved.set()
        # the other resolve starts while this one is not yet committed
        other.join(timeout=0.5)
    other.join(timeout=5)
    assert errors == []
    assert sorted(released.values()) == [False, True]
    assert work.get_task(task["id"])["status"] == "in_progress"


def test_a_malformed_stored_moment_does_not_fail_the_board(client, fresh_db):
    from app.services import work

    naive = work.create_task("Naive", actor="tester")
    broken = work.create_task("Broken", actor="tester")
    for task in (naive, broken):
        work.update_task(task["id"], status="in_progress", actor="tester")
    _set(fresh_db, naive["id"], "updated_at", "2026-01-05T10:00:00")
    _set(fresh_db, broken["id"], "updated_at", "not-a-date")
    response = client.get("/api/tasks/board")
    assert response.status_code == 200
    cards = {row["id"]: row for row in response.json()["open"]}
    assert cards[naive["id"]]["quiet_days"] >= 7
    assert cards[broken["id"]]["quiet_days"] is None
