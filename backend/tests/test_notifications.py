"""Notification triggers: the actions that file an unread row, and for whom."""

import json

import pytest
from conftest import _unread_for


def test_team_notifications_dismissable(client, fresh_db, monkeypatch):
    from app.services import notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    n = notifications.notify("team", "shared thing", tier="immediate")
    out = client.post("/api/notifications/read", json={"notification_id": n["id"]}).json()
    assert out["marked"] == 1
    assert client.get("/api/notifications").json() == []


def test_team_notifications_visible_in_inbox(client, monkeypatch):
    from app.services import notifications

    monkeypatch.setattr(notifications, "_post_slack", lambda *_: None)
    notifications.notify("team", "ship recap here", tier="immediate")
    inbox = client.get("/api/notifications").json()
    assert any("ship recap" in n["message"] for n in inbox)


def test_notification_records_its_policy_source(fresh_db):
    from app.services import notifications, work

    task = work.create_task("Typed notification source")["id"]
    row = notifications.notify(
        "mira",
        lambda source: f"Task #{source['id']} changed.",
        source_entity="task",
        source_id=task,
    )

    assert fresh_db.query_one(
        "SELECT source_entity, source_id, source_policy_context FROM notifications WHERE id = ?",
        (row["id"],),
    ) == {
        "source_entity": "task",
        "source_id": task,
        "source_policy_context": '{"classification":"workspace","crew_id":"","project_type":""}',
    }


def test_typed_notification_requires_a_source_row_builder(fresh_db):
    from app.services import notifications, work

    task = work.create_task("Typed notification builder")
    with pytest.raises(ValueError, match="source-row message builder"):
        notifications.notify(
            "mira",
            "Text composed before the source snapshot",
            source_entity="task",
            source_id=task["id"],
        )


def test_unsupported_notification_source_is_unclassified(fresh_db):
    from app.services import notifications

    row = notifications.notify(
        "mira",
        "A legacy source changed.",
        source_entity="standup",
        source_id=123,
    )
    stored = fresh_db.query_one("SELECT * FROM notifications WHERE id = ?", (row["id"],))

    permitted = notifications.policy_filter(
        [stored],
        lambda *_args: True,
        allow_unclassified=True,
    )
    assert len(permitted) == 1
    assert permitted[0]["message"] == "A legacy source changed."
    assert "source_entity" not in permitted[0]
    assert (
        notifications.policy_filter(
            [stored],
            lambda *_args: True,
            allow_unclassified=False,
        )
        == []
    )


def test_notification_keeps_its_creation_policy_context_after_relink(fresh_db):
    from app.services import engagements, notifications, scope, work

    regulated = engagements.create_engagement(
        "Notification source regulated",
        project_class="regulated",
    )["id"]
    standard = engagements.create_engagement(
        "Notification source standard",
        project_class="standard",
    )["id"]
    task = work.create_task("Restricted historical notification", engagement_id=regulated)["id"]
    notice = notifications.notify(
        "mira",
        lambda _source: "Restricted historical notification",
        source_entity="task",
        source_id=task,
    )
    work.update_task(task, engagement_id=standard)
    stored = fresh_db.query_one("SELECT * FROM notifications WHERE id = ?", (notice["id"],))

    assert (
        notifications.policy_filter(
            [stored],
            lambda _entity, _entity_id, attributes: attributes.get("project_type") != "regulated",
            allow_unclassified=False,
            viewer=scope.NOBODY,
        )
        == []
    )


def test_notification_checks_scoped_current_context_before_saved_context(fresh_db):
    from app.services import crews, engagements, notifications, scope, users, work

    users.ensure_user("owner")
    users.ensure_user("outsider")
    crew = crews.create_crew("Hidden notification parent", actor="owner")["id"]
    regulated = engagements.create_engagement(
        "Hidden regulated notification parent",
        project_class="regulated",
        actor="owner",
        visibility="crew",
        crew_id=crew,
    )["id"]
    task = work.create_task("Visible legacy notification child", actor="owner")["id"]
    fresh_db.execute("UPDATE tasks SET engagement_id = ? WHERE id = ?", (regulated, task))
    notice = notifications.notify(
        "outsider",
        lambda _source: "A hidden-parent task changed.",
        source_entity="task",
        source_id=task,
    )
    stored = fresh_db.query_one("SELECT * FROM notifications WHERE id = ?", (notice["id"],))
    observed = []

    def inspect(_entity, _entity_id, attributes):
        observed.append(dict(attributes))
        return True

    assert (
        notifications.policy_filter(
            [stored],
            inspect,
            allow_unclassified=False,
            viewer=scope.Viewer("outsider", True),
        )
        == []
    )
    assert observed == []


@pytest.mark.parametrize("operation", ["delegate", "claim", "submit"])
def test_task_notification_text_and_policy_snapshot_share_one_write(
    fresh_db, monkeypatch, operation
):
    from threading import Event, Thread
    from time import sleep

    from app.services import delegation, engagements, notifications, policy_context, users, work

    users.ensure_user("sponsor")
    standard = engagements.create_engagement(
        f"{operation} standard",
        project_class="standard",
    )["id"]
    regulated = engagements.create_engagement(
        f"{operation} regulated",
        project_class="regulated",
    )["id"]
    task = work.create_task("SNAPSHOT TITLE BEFORE", engagement_id=standard, actor="sponsor")["id"]
    if operation in {"claim", "submit"}:
        delegation.delegate_task(task, "research-agent", "sponsor", actor="sponsor")
    if operation == "submit":
        delegation.claim_task(task, actor="research-agent")
    fresh_db.execute("DELETE FROM notifications")

    entered = Event()
    writer_attempted = Event()
    writer_done = Event()
    original_row = policy_context.resource_row

    def coordinated_row(entity, entity_id):
        entered.set()
        assert writer_attempted.wait(5)
        sleep(0.05)
        assert not writer_done.is_set()
        return original_row(entity, entity_id)

    monkeypatch.setattr(policy_context, "resource_row", coordinated_row)
    monkeypatch.setattr(notifications, "_post_slack", lambda *_args: None)

    def relink() -> None:
        assert entered.wait(5)
        writer_attempted.set()
        work.update_task(
            task,
            title="SNAPSHOT TITLE AFTER",
            engagement_id=regulated,
            actor="sponsor",
        )
        writer_done.set()

    writer = Thread(target=relink)
    writer.start()
    if operation == "delegate":
        delegation.delegate_task(task, "research-agent", "sponsor", actor="sponsor")
    elif operation == "claim":
        delegation.claim_task(task, actor="research-agent")
    else:
        delegation.submit_completion(task, "ready", actor="research-agent")
    writer.join(5)

    assert writer_done.is_set()
    stored = fresh_db.query_one("SELECT * FROM notifications ORDER BY id DESC LIMIT 1")
    assert "SNAPSHOT TITLE BEFORE" in stored["message"]
    assert "SNAPSHOT TITLE AFTER" not in stored["message"]
    assert json.loads(stored["source_policy_context"])["project_type"] == "standard"
    assert (
        notifications.policy_filter(
            [stored],
            lambda _entity, _entity_id, attributes: attributes.get("project_type") != "regulated",
            allow_unclassified=False,
        )
        == []
    )


def test_blocker_sweep_and_notification_share_one_write(fresh_db, monkeypatch):
    from threading import Event, Thread
    from time import sleep

    from app.services import blockers, engagements, notifications, policy_context, users, work

    users.ensure_user("mira")
    standard = engagements.create_engagement(
        "Sweep standard",
        project_class="standard",
    )["id"]
    regulated = engagements.create_engagement(
        "Sweep regulated",
        project_class="regulated",
    )["id"]
    task = work.create_task(
        "Sweep task",
        engagement_id=standard,
        assignee="mira",
        actor="mira",
    )["id"]
    blocker = blockers.raise_blocker(
        "Sweep blocker",
        task_id=task,
        owner="mira",
        impact="high",
        actor="mira",
    )["id"]
    fresh_db.execute(
        "UPDATE blockers SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
        (blocker,),
    )

    entered = Event()
    writer_attempted = Event()
    writer_done = Event()
    original_row = policy_context.resource_row

    def coordinated_row(entity, entity_id):
        entered.set()
        assert writer_attempted.wait(5)
        sleep(0.05)
        assert not writer_done.is_set()
        return original_row(entity, entity_id)

    monkeypatch.setattr(policy_context, "resource_row", coordinated_row)
    monkeypatch.setattr(notifications, "_post_slack", lambda *_args: None)

    def relink() -> None:
        assert entered.wait(5)
        writer_attempted.set()
        work.update_task(task, engagement_id=regulated, actor="mira")
        writer_done.set()

    writer = Thread(target=relink)
    writer.start()
    assert len(blockers.sweep_escalations()) == 1
    writer.join(5)

    assert writer_done.is_set()
    stored = fresh_db.query_one(
        "SELECT * FROM notifications WHERE source_entity = 'blocker' AND source_id = ?",
        (blocker,),
    )
    assert stored is not None
    assert json.loads(stored["source_policy_context"])["project_type"] == "standard"
    assert (
        notifications.policy_filter(
            [stored],
            lambda _entity, _entity_id, attributes: attributes.get("project_type") != "regulated",
            allow_unclassified=False,
        )
        == []
    )


def test_ship_notification_is_plain_text(client, fresh_db):
    from app.services import engagements

    eng = engagements.create_engagement("Plain notify check", actor="tester", origin="human")
    engagements.update_engagement(
        eng["id"], status="closed", conclusion="achieved", actor="tester", origin="human"
    )
    row = fresh_db.query_one(
        "SELECT message FROM notifications WHERE message LIKE '%Plain notify check%'"
    )
    assert row and "**" not in row["message"]
    note = fresh_db.query_one(
        "SELECT content FROM notes WHERE topic = 'shipped-Plain notify check'"
    )
    assert note and "**" in note["content"]  # the note keeps markdown


def test_engagement_close_and_notification_share_one_write(fresh_db, monkeypatch):
    from threading import Event, Thread
    from time import sleep

    from app.services import engagements, notifications, users

    users.ensure_user("mira")
    engagement = engagements.create_engagement("CLOSE NAME BEFORE", actor="mira")["id"]
    entered = Event()
    writer_attempted = Event()
    writer_done = Event()
    original_ship = engagements._ship_it

    def coordinated_ship(engagement_id, *, actor, origin="human"):
        entered.set()
        assert writer_attempted.wait(5)
        sleep(0.05)
        assert not writer_done.is_set()
        return original_ship(engagement_id, actor=actor, origin=origin)

    monkeypatch.setattr(engagements, "_ship_it", coordinated_ship)
    monkeypatch.setattr(notifications, "_post_slack", lambda *_args: None)

    def rename() -> None:
        assert entered.wait(5)
        writer_attempted.set()
        engagements.update_engagement(engagement, name="CLOSE NAME AFTER", actor="mira")
        writer_done.set()

    writer = Thread(target=rename)
    writer.start()
    engagements.update_engagement(
        engagement,
        status="closed",
        conclusion="achieved",
        actor="mira",
    )
    writer.join(5)

    assert writer_done.is_set()
    stored = fresh_db.query_one(
        "SELECT * FROM notifications WHERE source_entity = 'engagement' AND source_id = ?",
        (engagement,),
    )
    assert stored is not None
    assert "CLOSE NAME BEFORE" in stored["message"]
    assert "CLOSE NAME AFTER" not in stored["message"]


def test_blocker_resolution_notifies_waiting_task_owner(fresh_db):
    from app.services import blockers, work

    b = blockers.raise_blocker("vendor key missing", actor="tomas", owner="tomas")
    t = work.create_task("integrate vendor", assignee="mira", actor="mira")
    work.update_task(t["id"], waiting_on=f"blocker:{b['id']}", actor="mira")
    blockers.resolve_blocker(b["id"], resolution="key arrived", actor="tomas")
    assert _unread_for(fresh_db, "mira", "%can move again%")


def test_blocker_resolution_notification_has_one_task_source(fresh_db, monkeypatch):
    from app.services import blockers, notifications, work

    monkeypatch.setattr(notifications, "_post_slack", lambda *_args: None)
    blocker = blockers.raise_blocker("vendor key missing", actor="tomas", owner="tomas")
    task = work.create_task("integrate private vendor", assignee="mira", actor="mira")
    work.update_task(task["id"], waiting_on=f"blocker:{blocker['id']}", actor="mira")

    blockers.resolve_blocker(blocker["id"], resolution="key arrived", actor="tomas")
    stored = fresh_db.query_one(
        "SELECT * FROM notifications WHERE \"user\" = 'mira' ORDER BY id DESC LIMIT 1"
    )

    assert stored["source_entity"] == "task"
    assert stored["source_id"] == task["id"]
    assert f"Task #{task['id']}" in stored["message"]
    assert "integrate private vendor" in stored["message"]
    assert "Blocker #" not in stored["message"]
    assert (
        notifications.policy_filter(
            [stored],
            lambda entity, entity_id, _attributes: (
                not (entity == "task" and entity_id == task["id"])
            ),
            allow_unclassified=False,
        )
        == []
    )


def test_denied_rows_cannot_starve_a_permitted_notification(fresh_db):
    """The SQL LIMIT ran before the policy filter, so 50 denied rows at the
    head of the inbox returned an empty list while an older permitted
    notification stayed unreachable behind them."""
    from app.services import notifications, work

    permitted = work.create_task("the one readable task")
    notifications.notify(
        "mira",
        lambda row: f"Task #{row['id']} needs you",
        source_entity="task",
        source_id=permitted["id"],
    )
    for n in range(55):
        task = work.create_task(f"denied source {n}")
        notifications.notify(
            "mira",
            lambda row: f"Task #{row['id']} moved",
            source_entity="task",
            source_id=task["id"],
        )

    rows = notifications.list_notifications_filtered(
        "mira",
        True,
        lambda _entity, entity_id, _attributes: entity_id == permitted["id"],
        allow_unclassified=False,
    )
    assert [row["message"] for row in rows] == [f"Task #{permitted['id']} needs you"]


def test_migration_005_retargets_old_row_links_and_nothing_else(fresh_db):
    """Notification links are stored at write time, so rows from before the
    row-level deep links still pointed at a page top — a question notice
    pointed at "/", My Day itself. The repair touches only the two retargeted
    kinds and only the two old page-top links."""
    from pathlib import Path

    from app import db

    def old_row(link, entity, source_id):
        return db.execute(
            'INSERT INTO notifications ("user", tier, message, link, created_at,'
            " source_entity, source_id) VALUES ('ava', 'digest', 'm', ?, ?, ?, ?)"
            " RETURNING id",
            (link, db.now(), entity, source_id),
        )

    q = old_row("/", "question", 7)
    b = old_row("/dashboard", "blocker", 3)
    deliberate = old_row("/review", "question", 7)  # a service chose this link
    untyped = old_row("/", "", None)  # no source row to point at

    sql = Path(__file__).parent.parent / "app/core_migrations/005_notification_row_links.sql"
    for stmt in sql.read_text().split(";"):
        if stmt.strip():
            db.execute(stmt)

    links = {
        r["id"]: r["link"]
        for r in db.query(
            "SELECT id, link FROM notifications WHERE id IN (?, ?, ?, ?)",
            (q, b, deliberate, untyped),
        )
    }
    assert links[q] == "/dashboard#question-7"
    assert links[b] == "/dashboard#blocker-3"
    assert links[deliberate] == "/review"
    assert links[untyped] == "/"
