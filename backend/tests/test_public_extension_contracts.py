"""Commands, events, and data boundaries used by private packages."""

from dataclasses import replace

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.extensions import (
    AppSettings,
    EventContribution,
    ExtensionMigration,
    ExtensionRegistry,
    ExtensionValidationError,
    MigrationContribution,
    PolicyContribution,
    PolicyDecision,
    PolicyEffect,
    RouteContribution,
    SkeinModule,
)
from app.extensions.data import ExtensionStore
from app.extensions.policy import PolicyInput, PolicySubject
from app.main import create_app
from app.public import CommandContext, CreateTaskCommand, PublicError, UpdateTaskCommand, WorkItems
from app.public.events import dispatch_events


def _context(**changes) -> CommandContext:
    values = {
        "subject": PolicySubject("atlas-sync", kind="service"),
        "origin": "atlas-integration",
        "correlation_id": "sync-42",
        "project_type": "regulated",
    }
    values.update(changes)
    return CommandContext(**values)


def test_public_work_commands_keep_service_invariants_and_emit_safe_events(fresh_db):
    registry = ExtensionRegistry.build(())
    work = WorkItems(registry.policy_engine)

    created = work.create_task(
        CreateTaskCommand(title="Secret launch", description="Do not publish this body"),
        _context(),
    )
    updated = work.update_task(
        UpdateTaskCommand(task_id=created.id, status="in_progress", priority="high"),
        _context(),
    )

    assert updated.status == "in_progress"
    assert updated.priority == "high"
    assert updated.origin == "atlas-integration"
    activity = fresh_db.query("SELECT action, actor FROM activity ORDER BY id")
    assert activity == [
        {"action": "create_task", "actor": "atlas-sync"},
        {"action": "update_task", "actor": "atlas-sync"},
    ]
    outbox = fresh_db.query("SELECT event_type, payload FROM extension_outbox ORDER BY created_at")
    assert [row["event_type"] for row in outbox] == [
        "skein.task.created",
        "skein.task.updated",
    ]
    assert all("Secret launch" not in row["payload"] for row in outbox)
    assert all("Do not publish" not in row["payload"] for row in outbox)


def test_public_command_and_event_share_the_transaction(fresh_db, monkeypatch):
    from app.services import work as service_work

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("outbox unavailable")

    monkeypatch.setattr(service_work, "_emit_task_event", fail_event)
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    with pytest.raises(RuntimeError, match="outbox unavailable"):
        facade.create_task(CreateTaskCommand(title="rolled back"), _context())
    assert fresh_db.query_one("SELECT 1 AS present FROM tasks") is None
    assert fresh_db.query_one("SELECT 1 AS present FROM activity") is None


def test_rest_and_agent_service_writes_emit_the_same_public_events(fresh_db):
    from app.services import work as service_work

    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    with TestClient(create_app(settings), headers={"X-User": "mira"}) as client:
        created = client.post("/api/tasks", json={"title": "Human task"})
        assert created.status_code == 200
    service_work.update_task(
        created.json()["id"],
        status="in_progress",
        actor="scout",
        origin="agent",
    )

    events = fresh_db.query("SELECT event_type, payload FROM extension_outbox ORDER BY created_at")
    assert [item["event_type"] for item in events] == [
        "skein.task.created",
        "skein.task.updated",
    ]
    assert '"origin":"human"' in events[0]["payload"]
    assert '"origin":"agent"' in events[1]["payload"]


def test_a_workplace_policy_can_require_a_manager_before_the_write(fresh_db):
    def manager_review(request: PolicyInput):
        if (
            request.action == "work.task.create"
            and request.resource.project_type == "regulated"
            and request.subject.kind == "service"
        ):
            return PolicyDecision(
                PolicyEffect.REVIEW,
                ("Regulated work needs a manager.",),
                approver_groups=("delivery-managers",),
            )
        return None

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        policies=(PolicyContribution("atlas.workplace.manager-review", manager_review),),
    )
    facade = WorkItems(ExtensionRegistry.build((module,)).policy_engine)
    with pytest.raises(PublicError) as raised:
        facade.create_task(CreateTaskCommand(title="needs review"), _context())
    assert raised.value.code == "REVIEW_REQUIRED"
    assert raised.value.obligations == ("approver-group:delivery-managers",)
    assert fresh_db.query_one("SELECT 1 AS present FROM tasks") is None


def test_event_delivery_retries_and_uses_event_id_as_the_receipt(fresh_db):
    calls: list[str] = []

    def subscriber(event):
        calls.append(event.event_id)
        if len(calls) == 1:
            raise RuntimeError("temporary remote error")

    contribution = EventContribution(
        "atlas.workplace.sync",
        subscriber,
        ("skein.task.created",),
    )
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    event_task = facade.create_task(CreateTaskCommand(title="delivery"), _context())

    assert dispatch_events((contribution,)) == {"delivered": 0, "failed": 1, "dead": 0}
    assert dispatch_events((contribution,)) == {"delivered": 1, "failed": 0, "dead": 0}
    assert dispatch_events((contribution,)) == {"delivered": 0, "failed": 0, "dead": 0}
    assert len(calls) == 2
    assert calls[0] == calls[1]
    delivery = fresh_db.query_one("SELECT * FROM extension_event_deliveries")
    assert delivery["event_id"] == calls[0]
    assert event_task.id > 0


def test_workspace_subscribers_do_not_receive_private_events(fresh_db):
    calls = []
    contribution = EventContribution(
        "atlas.workplace.sync",
        calls.append,
        ("skein.task.created",),
    )
    facade = WorkItems(ExtensionRegistry.build(()).policy_engine)
    facade.create_task(
        CreateTaskCommand(title="private", visibility="private"),
        _context(subject=PolicySubject("mira")),
    )
    assert dispatch_events((contribution,))["delivered"] == 1
    assert calls == []


def test_extension_store_owns_its_schema_and_migrations(fresh_db, tmp_path):
    store = ExtensionStore(tmp_path / "atlas.db")
    migrations = (
        ExtensionMigration(
            1,
            "create-links",
            (
                "CREATE TABLE work_links"
                " (skein_task_id INTEGER PRIMARY KEY, external_id TEXT NOT NULL UNIQUE)",
            ),
        ),
        ExtensionMigration(
            2,
            "add-classification",
            ("ALTER TABLE work_links ADD COLUMN classification TEXT NOT NULL DEFAULT ''",),
        ),
    )
    store.migrate(migrations)
    store.migrate(migrations)
    store.execute(
        "INSERT INTO work_links (skein_task_id, external_id, classification) VALUES (?, ?, ?)",
        (41, "ATLAS-9", "internal"),
    )
    assert store.query_one("SELECT * FROM work_links WHERE skein_task_id = ?", (41,)) == {
        "skein_task_id": 41,
        "external_id": "ATLAS-9",
        "classification": "internal",
    }
    versions = store.query("SELECT version, name FROM extension_schema_version ORDER BY version")
    assert versions == [
        {"version": 1, "name": "create-links"},
        {"version": 2, "name": "add-classification"},
    ]
    assert (
        fresh_db.query_one("SELECT 1 AS present FROM sqlite_master WHERE name = 'work_links'")
        is None
    )


def test_extension_store_refuses_both_core_database_paths(fresh_db):
    from app import config, db

    for path in (db.DB_PATH, config.PRIVATE_DB_PATH):
        with pytest.raises(ValueError, match="core database path"):
            ExtensionStore(path).migrate(())


def test_composition_applies_extension_migrations_before_routes(fresh_db, tmp_path):
    store = ExtensionStore(tmp_path / "atlas.db")
    router = APIRouter(prefix="/api/extensions/atlas.workplace")

    @router.get("/links")
    def links():
        return store.query("SELECT external_id FROM work_links")

    module = SkeinModule(
        module_id="atlas.workplace",
        version="1.0.0",
        migrations=(
            MigrationContribution(
                "atlas.workplace.data",
                store,
                (
                    ExtensionMigration(
                        1,
                        "create-links",
                        ("CREATE TABLE work_links (external_id TEXT NOT NULL)",),
                    ),
                ),
            ),
        ),
        routes=(RouteContribution("atlas.workplace.routes", router),),
    )
    settings = replace(AppSettings.from_config(), scheduler_enabled=False)
    with TestClient(create_app(settings, (module,)), headers={"X-User": "tester"}) as client:
        assert client.get("/api/extensions/atlas.workplace/links").json() == []


def test_invalid_event_and_migration_contracts_are_rejected(tmp_path):
    store = ExtensionStore(tmp_path / "atlas.db")
    with pytest.raises(ExtensionValidationError, match="event type"):
        ExtensionRegistry.build(
            (
                SkeinModule(
                    module_id="atlas.workplace",
                    version="1.0.0",
                    events=(EventContribution("atlas.workplace.empty", lambda _event: None, ()),),
                ),
            )
        )
    with pytest.raises(ExtensionValidationError, match="ascending versions"):
        ExtensionRegistry.build(
            (
                SkeinModule(
                    module_id="atlas.workplace",
                    version="1.0.0",
                    migrations=(
                        MigrationContribution(
                            "atlas.workplace.data",
                            store,
                            (
                                ExtensionMigration(2, "second", ("SELECT 1",)),
                                ExtensionMigration(1, "first", ("SELECT 1",)),
                            ),
                        ),
                    ),
                ),
            )
        )
