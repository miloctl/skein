"""Atlas work-system adapter built only on Skein public contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.extensions import ExtensionStore, PolicySubject
from app.public import CommandContext, CreateTaskCommand, UpdateTaskCommand, WorkItems


@dataclass(frozen=True)
class AtlasItem:
    external_id: str
    title: str
    status: str = "todo"
    classification: str = "internal"


class AtlasClient(Protocol):
    def list_items(self) -> tuple[AtlasItem, ...]: ...

    def update_status(self, external_id: str, status: str, event_id: str = "") -> None: ...


class MemoryAtlasClient:
    """A deterministic fake for contract tests and keyless deployments."""

    def __init__(self, items: tuple[AtlasItem, ...] = ()) -> None:
        self.items = items
        self.updates: list[tuple[str, str, str]] = []

    def list_items(self) -> tuple[AtlasItem, ...]:
        return self.items

    def update_status(self, external_id: str, status: str, event_id: str = "") -> None:
        self.updates.append((external_id, status, event_id))


class AtlasIntegration:
    def __init__(self, client: AtlasClient, store: ExtensionStore) -> None:
        self.client = client
        self.store = store

    def sync(self, work: WorkItems, subject: PolicySubject) -> dict[str, int]:
        context = CommandContext(
            subject,
            "atlas-integration",
            project_type="standard",
        )
        created = updated = 0
        for item in self.client.list_items():
            link = self.store.query_one(
                "SELECT skein_task_id FROM work_links WHERE external_id = ?",
                (item.external_id,),
            )
            if link:
                task = work.update_task(
                    UpdateTaskCommand(
                        task_id=int(link["skein_task_id"]),
                        title=item.title,
                        status=item.status,
                    ),
                    context,
                )
                updated += 1
            else:
                task = work.create_task(
                    CreateTaskCommand(
                        title=item.title,
                        status=item.status,
                        idempotency_key=f"atlas-item:{item.external_id}",
                    ),
                    context,
                )
                self.store.execute(
                    "INSERT INTO work_links"
                    " (external_id, skein_task_id, classification) VALUES (?, ?, ?)",
                    (item.external_id, task.id, item.classification),
                )
                created += 1
            self.client.update_status(item.external_id, task.status)
        self.store.execute(
            "INSERT INTO sync_runs (created_count, updated_count, finished_at)"
            " VALUES (?, ?, datetime('now'))",
            (created, updated),
        )
        return {"created": created, "updated": updated}

    def deliver_task_event(
        self,
        event,
        work: WorkItems,
        subject: PolicySubject | None,
        delivery_id: str,
    ) -> None:
        link = self.store.query_one(
            "SELECT external_id FROM work_links WHERE skein_task_id = ?",
            (int(event.resource.id),),
        )
        if not link:
            return
        context = CommandContext(
            subject or PolicySubject("atlas-events", kind="service"),
            "atlas-event",
            correlation_id=event.event_id,
        )
        task = work.get_task(int(event.resource.id), context)
        self.client.update_status(link["external_id"], task.status, delivery_id)

    def metrics(self) -> dict[str, int]:
        links = self.store.query_one("SELECT COUNT(*) AS count FROM work_links")
        runs = self.store.query_one("SELECT COUNT(*) AS count FROM sync_runs")
        return {"linked_items": int(links["count"]), "sync_runs": int(runs["count"])}
