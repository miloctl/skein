"""Notification triggers: the actions that file an unread row, and for whom."""

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
        "The task changed.",
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
        "Restricted historical notification",
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


def test_blocker_resolution_notifies_waiting_task_owner(fresh_db):
    from app.services import blockers, work

    b = blockers.raise_blocker("vendor key missing", actor="tomas", owner="tomas")
    t = work.create_task("integrate vendor", assignee="mira", actor="mira")
    work.update_task(t["id"], waiting_on=f"blocker:{b['id']}", actor="mira")
    blockers.resolve_blocker(b["id"], resolution="key arrived", actor="tomas")
    assert _unread_for(fresh_db, "mira", "%can move again%")
