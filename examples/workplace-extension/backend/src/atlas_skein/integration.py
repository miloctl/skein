"""Atlas work-system adapter built only on Skein public contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

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


class AtlasHttpClient:
    """Small fictional HTTP adapter used by the deployment example."""

    def __init__(self, endpoint: str, token: str, timeout_seconds: float = 10) -> None:
        self.endpoint = endpoint.rstrip("/")
        if urlsplit(self.endpoint).scheme not in ("http", "https"):
            raise ValueError("The Atlas API URL must use HTTP or HTTPS.")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def _request(self, method: str, path: str, payload: dict | None = None):
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
            return json.loads(raw)
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


class AtlasIntegration:
    def __init__(self, client: AtlasClient, store: ExtensionStore) -> None:
        self.client = client
        self.store = store

    def sync(
        self,
        work: WorkItems,
        subject: PolicySubject,
        *,
        actor: str = "",
        correlation_id: str = "",
    ) -> dict[str, int]:
        context = CommandContext(
            subject,
            "atlas-integration",
            correlation_id=correlation_id,
            project_type="standard",
            actor=actor,
            actor_kind="agent" if actor else subject.kind,
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
            self.client.update_status(
                item.external_id,
                task.status,
                correlation_id or f"atlas-sync:{item.external_id}:{task.status}",
            )
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
