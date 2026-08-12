"""Atlas work-system adapter built only on Skein public contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from app.extensions import EventExecutionContext, ExtensionStore
from app.public import (
    CommandContext,
    CreateTaskCommand,
    DomainEvent,
    UpdateTaskCommand,
    WorkItems,
)


@dataclass(frozen=True)
class AtlasItem:
    external_id: str
    title: str
    status: str = "todo"
    classification: str = "internal"


class AtlasClient(Protocol):
    def list_items(self) -> tuple[AtlasItem, ...]: ...

    def update_status(self, external_id: str, status: str, event_id: str = "") -> None: ...

    def notify_manager(
        self,
        channel: str,
        message: str,
        event_id: str = "",
    ) -> None: ...


class MemoryAtlasClient:
    """A deterministic fake for contract tests and keyless deployments."""

    def __init__(self, items: tuple[AtlasItem, ...] = ()) -> None:
        self.items = items
        self.updates: list[tuple[str, str, str]] = []
        self.notifications: list[tuple[str, str, str]] = []
        self._seen_events: set[str] = set()

    def list_items(self) -> tuple[AtlasItem, ...]:
        return self.items

    def update_status(self, external_id: str, status: str, event_id: str = "") -> None:
        if event_id and event_id in self._seen_events:
            return
        if event_id:
            self._seen_events.add(event_id)
        self.updates.append((external_id, status, event_id))

    def notify_manager(self, channel: str, message: str, event_id: str = "") -> None:
        receipt = f"notification:{event_id}" if event_id else ""
        if receipt and receipt in self._seen_events:
            return
        if receipt:
            self._seen_events.add(receipt)
        self.notifications.append((channel, message, event_id))


class AtlasHttpClient:
    """Small fictional HTTP adapter used by the deployment example."""

    def __init__(self, endpoint: str, token: str, timeout_seconds: float = 10) -> None:
        self.endpoint = endpoint.rstrip("/")
        if urlsplit(self.endpoint).scheme not in ("http", "https"):
            raise ValueError("The Atlas API URL must use HTTP or HTTPS.")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> object:
        body = None if payload is None else json.dumps(payload).encode()
        request = Request(  # noqa: S310 -- constructor receives the validated HTTP(S) endpoint
            f"{self.endpoint}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                raw = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("The Atlas API request failed.") from exc
        if not raw:
            return None
        try:
            parsed: object = json.loads(raw)
            return parsed
        except (TypeError, ValueError) as exc:
            raise RuntimeError("The Atlas API returned invalid JSON.") from exc

    def list_items(self) -> tuple[AtlasItem, ...]:
        result = self._request("GET", "/items")
        rows = result.get("items", []) if isinstance(result, dict) else result
        if not isinstance(rows, list):
            raise RuntimeError("The Atlas API returned an invalid item list.")
        try:
            return tuple(
                AtlasItem(
                    external_id=str(row["external_id"]),
                    title=str(row["title"]),
                    status=str(row.get("status") or "todo"),
                    classification=str(row.get("classification") or "internal"),
                )
                for row in rows
                if isinstance(row, dict)
            )
        except KeyError as exc:
            raise RuntimeError("The Atlas API returned an invalid item.") from exc

    def update_status(self, external_id: str, status: str, event_id: str = "") -> None:
        self._request(
            "PATCH",
            f"/items/{quote(external_id, safe='')}",
            {"status": status, "idempotency_key": event_id},
        )

    def notify_manager(self, channel: str, message: str, event_id: str = "") -> None:
        self._request(
            "POST",
            "/notifications",
            {"channel": channel, "message": message, "idempotency_key": event_id},
        )


class AtlasIntegration:
    def __init__(self, client: AtlasClient, store: ExtensionStore) -> None:
        self.client = client
        self.store = store

    def sync(
        self,
        work: WorkItems,
        context: CommandContext,
    ) -> dict[str, int]:
        # The route and scheduled job have separate core receipt namespaces.
        # Serialize their shared business key in the extension-owned store so
        # both entry points cannot create a different task for one Atlas item.
        with self.store.transaction():
            result = self._sync_locked(work, context)
        # The core and extension stores cannot share one transaction. Commit
        # the durable core mapping and outbound intent before network I/O.
        # A retry from a different contribution then reuses the mapping and
        # the same remote idempotency key instead of creating another task.
        self._deliver_pending_statuses()
        return result

    def _sync_locked(
        self,
        work: WorkItems,
        context: CommandContext,
    ) -> dict[str, int]:
        created = updated = 0
        for item in self.client.list_items():
            link = self.store.query_one(
                "SELECT skein_task_id FROM work_links WHERE external_id = ?",
                (item.external_id,),
            )
            if link:
                task = work.get_task(int(link["skein_task_id"]), context)
                if task.title != item.title or task.status != item.status:
                    task = work.update_task(
                        UpdateTaskCommand(
                            task_id=task.id,
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
                inserted = self.store.execute(
                    "INSERT OR IGNORE INTO work_links"
                    " (external_id, skein_task_id, classification) VALUES (?, ?, ?)",
                    (item.external_id, task.id, item.classification),
                )
                stored = self.store.query_one(
                    "SELECT skein_task_id FROM work_links WHERE external_id = ?",
                    (item.external_id,),
                )
                if stored is None or int(stored["skein_task_id"]) != task.id:
                    raise RuntimeError("The Atlas work mapping conflicts with the Skein task.")
                if inserted:
                    created += 1
            event_id = f"atlas-status:{item.external_id}:{task.updated_at}:{task.status}"
            self.store.execute(
                "INSERT OR IGNORE INTO status_outbox"
                " (event_id, external_id, status, delivered) VALUES (?, ?, ?, 0)",
                (event_id, item.external_id, task.status),
            )
        self.store.execute(
            "INSERT INTO sync_runs (created_count, updated_count, finished_at)"
            " VALUES (?, ?, datetime('now'))",
            (created, updated),
        )
        return {"created": created, "updated": updated}

    def _deliver_pending_statuses(self) -> None:
        with self.store.transaction():
            pending = self.store.query(
                "SELECT event_id, external_id, status FROM status_outbox"
                " WHERE delivered = 0 ORDER BY event_id"
            )
            for delivery in pending:
                self.client.update_status(
                    str(delivery["external_id"]),
                    str(delivery["status"]),
                    str(delivery["event_id"]),
                )
                self.store.execute(
                    "UPDATE status_outbox SET delivered = 1 WHERE event_id = ?",
                    (str(delivery["event_id"]),),
                )

    def deliver_task_event(
        self,
        event: DomainEvent,
        context: EventExecutionContext,
    ) -> None:
        link = self.store.query_one(
            "SELECT external_id FROM work_links WHERE skein_task_id = ?",
            (int(event.resource.id),),
        )
        if not link:
            return
        command_context = context.command_context()
        task = context.work_items.get_task(int(event.resource.id), command_context)
        self.client.update_status(link["external_id"], task.status, context.delivery_id)

    def metrics(self) -> dict[str, int]:
        links = self.store.query_one("SELECT COUNT(*) AS count FROM work_links")
        runs = self.store.query_one("SELECT COUNT(*) AS count FROM sync_runs")
        if links is None or runs is None:
            raise RuntimeError("The Atlas extension store is not initialized.")
        return {"linked_items": int(links["count"]), "sync_runs": int(runs["count"])}
