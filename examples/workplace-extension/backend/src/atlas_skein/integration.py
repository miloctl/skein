"""Atlas work-system adapter built only on Skein public contracts."""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class _RefuseRedirect(HTTPRedirectHandler):
    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        raise AtlasUnavailableError("The Atlas API redirected an authenticated request.")


_NO_REDIRECT_OPENER = build_opener(_RefuseRedirect)

from app.extensions import EventExecutionContext, ExtensionStore
from app.public import (
    CommandContext,
    CreateTaskCommand,
    DomainEvent,
    TaskView,
    UpdateTaskCommand,
    WorkItems,
)


@dataclass(frozen=True)
class AtlasItem:
    external_id: str
    title: str
    status: str = "todo"
    classification: str = "internal"


class AtlasUnavailableError(RuntimeError):
    """The Atlas adapter could not complete a remote operation."""


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
        parsed = urlsplit(self.endpoint)
        # This client sends a bearer token on every request. Plaintext HTTP
        # hands that token to any network-positioned reader, so only the
        # loopback development case is exempt from HTTPS.
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
        ):
            raise ValueError("The Atlas API URL must use HTTPS.")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> object:
        body = None if payload is None else json.dumps(payload).encode()
        request = Request(  # noqa: S310 -- constructor receives the validated HTTPS endpoint
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
            # A redirect can move the Authorization header to another origin
            # or downgrade it to plaintext; the opener refuses instead.
            with _NO_REDIRECT_OPENER.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise AtlasUnavailableError("The Atlas API request failed.") from exc
        if not raw:
            return None
        try:
            parsed: object = json.loads(raw)
            return parsed
        except (TypeError, ValueError) as exc:
            raise AtlasUnavailableError("The Atlas API returned invalid JSON.") from exc

    def list_items(self) -> tuple[AtlasItem, ...]:
        result = self._request("GET", "/items")
        rows = result.get("items", []) if isinstance(result, dict) else result
        if not isinstance(rows, list):
            raise AtlasUnavailableError("The Atlas API returned an invalid item list.")
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
            raise AtlasUnavailableError("The Atlas API returned an invalid item.") from exc

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
        self._active: ContextVar[sqlite3.Connection | None] = ContextVar(
            f"atlas_store_{id(self)}",
            default=None,
        )

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[None]:
        """Use the ExtensionStore connection contract available in core 0.2.0."""
        active = self._active.get()
        if active is not None:
            yield
            return
        with contextlib.closing(self.store.connect()) as connection:
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            token = self._active.set(connection)
            try:
                yield
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                self._active.reset(token)

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        active = self._active.get()
        if active is None:
            return self.store.execute(sql, params)
        cursor = active.execute(sql, tuple(params))
        return int(cursor.lastrowid or 0)

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        active = self._active.get()
        if active is None:
            return self.store.query(sql, params)
        return [dict(row) for row in active.execute(sql, tuple(params)).fetchall()]

    def _query_one(
        self,
        sql: str,
        params: Sequence[Any] = (),
    ) -> dict[str, Any] | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    def sync(
        self,
        work: WorkItems,
        context: CommandContext,
    ) -> dict[str, int]:
        result = self._sync_items(work, context)
        # The core and extension stores cannot share one transaction. Commit
        # the durable core mapping and outbound intent before network I/O.
        # A retry from a different contribution then reuses the mapping and
        # the same remote idempotency key instead of creating another task.
        self._deliver_pending_statuses()
        return result

    def _sync_items(
        self,
        work: WorkItems,
        context: CommandContext,
    ) -> dict[str, int]:
        created = updated = 0
        for item in self.client.list_items():
            link = self._query_one(
                "SELECT skein_task_id FROM work_links WHERE external_id = ?",
                (item.external_id,),
            )
            if link:
                task = work.get_task(int(link["skein_task_id"]), context)
                if task.title != item.title or task.status != item.status:
                    task = work.update_task(
                        UpdateTaskCommand.model_validate(
                            {
                                "task_id": task.id,
                                "title": item.title,
                                "status": item.status,
                            }
                        ),
                        context,
                    )
                    updated += 1
                self._record_status(item.external_id, task)
            else:
                claim = self._claim_item(
                    item.external_id,
                    context.namespace,
                )
                linked_task_id = int(claim.get("linked_task_id") or 0)
                if linked_task_id:
                    task = work.get_task(linked_task_id, context)
                    if task.title != item.title or task.status != item.status:
                        task = work.update_task(
                            UpdateTaskCommand.model_validate(
                                {
                                    "task_id": task.id,
                                    "title": item.title,
                                    "status": item.status,
                                }
                            ),
                            context,
                        )
                        updated += 1
                    self._record_status(item.external_id, task)
                    continue
                claimed_task_id = int(claim.get("skein_task_id") or 0)
                if not claimed_task_id and claim["owner_namespace"] != context.namespace:
                    continue
                if claimed_task_id:
                    task = work.get_task(claimed_task_id, context)
                else:
                    task = work.create_task(
                        CreateTaskCommand.model_validate(
                            {
                                "title": item.title,
                                "status": item.status,
                                "idempotency_key": f"atlas-item:{item.external_id}",
                            }
                        ),
                        context,
                    )
                    self._stage_claim_task(
                        item.external_id,
                        context.namespace,
                        task.id,
                    )
                if self._complete_mapping(item, task):
                    created += 1
        with self._transaction():
            self._execute(
                "INSERT INTO sync_runs (created_count, updated_count, finished_at)"
                " VALUES (?, ?, datetime('now'))",
                (created, updated),
            )
        return {"created": created, "updated": updated}

    def _claim_item(self, external_id: str, owner_namespace: str) -> dict[str, Any]:
        if not owner_namespace:
            raise RuntimeError("The Atlas synchronization needs a contribution namespace.")
        with self._transaction():
            link = self._query_one(
                "SELECT skein_task_id FROM work_links WHERE external_id = ?",
                (external_id,),
            )
            if link is not None:
                return {"linked_task_id": int(link["skein_task_id"])}
            self._execute(
                "INSERT OR IGNORE INTO sync_claims"
                " (external_id, owner_namespace, skein_task_id) VALUES (?, ?, NULL)",
                (external_id, owner_namespace),
            )
            claim = self._query_one(
                "SELECT owner_namespace, skein_task_id FROM sync_claims"
                " WHERE external_id = ?",
                (external_id,),
            )
            if claim is None:
                raise RuntimeError("The Atlas synchronization claim was not stored.")
            return claim

    def _stage_claim_task(
        self,
        external_id: str,
        owner_namespace: str,
        task_id: int,
    ) -> None:
        with self._transaction():
            self._execute(
                "UPDATE sync_claims SET skein_task_id = ?"
                " WHERE external_id = ? AND owner_namespace = ?"
                " AND (skein_task_id IS NULL OR skein_task_id = ?)",
                (task_id, external_id, owner_namespace, task_id),
            )
            claim = self._query_one(
                "SELECT skein_task_id FROM sync_claims WHERE external_id = ?",
                (external_id,),
            )
            if claim is None:
                link = self._query_one(
                    "SELECT skein_task_id FROM work_links WHERE external_id = ?",
                    (external_id,),
                )
                if link is not None and int(link["skein_task_id"]) == task_id:
                    return
                raise RuntimeError("The Atlas synchronization claim was removed.")
            if int(claim.get("skein_task_id") or 0) != task_id:
                raise RuntimeError("The Atlas synchronization claim conflicts with the task.")

    def _complete_mapping(self, item: AtlasItem, task: TaskView) -> bool:
        with self._transaction():
            prior = self._query_one(
                "SELECT skein_task_id FROM work_links WHERE external_id = ?",
                (item.external_id,),
            )
            self._execute(
                "INSERT OR IGNORE INTO work_links"
                " (external_id, skein_task_id, classification) VALUES (?, ?, ?)",
                (item.external_id, task.id, item.classification),
            )
            stored = self._query_one(
                "SELECT skein_task_id FROM work_links WHERE external_id = ?",
                (item.external_id,),
            )
            if stored is None or int(stored["skein_task_id"]) != task.id:
                raise RuntimeError("The Atlas work mapping conflicts with the Skein task.")
            self._record_status(item.external_id, task)
            self._execute("DELETE FROM sync_claims WHERE external_id = ?", (item.external_id,))
            return prior is None

    def _record_status(self, external_id: str, task: TaskView) -> None:
        event_id = f"atlas-status:{external_id}:{task.updated_at}:{task.status}"
        self._execute(
            "INSERT OR IGNORE INTO status_outbox"
            " (event_id, external_id, status, delivered) VALUES (?, ?, ?, 0)",
            (event_id, external_id, task.status),
        )

    def _deliver_pending_statuses(self) -> None:
        with self._transaction():
            pending = self._query(
                "SELECT event_id, external_id, status FROM status_outbox"
                " WHERE delivered = 0 ORDER BY event_id"
            )
            for delivery in pending:
                self.client.update_status(
                    str(delivery["external_id"]),
                    str(delivery["status"]),
                    str(delivery["event_id"]),
                )
                self._execute(
                    "UPDATE status_outbox SET delivered = 1 WHERE event_id = ?",
                    (str(delivery["event_id"]),),
                )

    def deliver_task_event(
        self,
        event: DomainEvent,
        context: EventExecutionContext,
    ) -> None:
        link = self._query_one(
            "SELECT external_id FROM work_links WHERE skein_task_id = ?",
            (int(event.resource.id),),
        )
        if not link:
            return
        command_context = context.command_context()
        task = context.work_items.get_task(int(event.resource.id), command_context)
        self.client.update_status(link["external_id"], task.status, context.delivery_id)

    def metrics(self) -> dict[str, int]:
        links = self._query_one("SELECT COUNT(*) AS count FROM work_links")
        runs = self._query_one("SELECT COUNT(*) AS count FROM sync_runs")
        if links is None or runs is None:
            raise RuntimeError("The Atlas extension store is not initialized.")
        return {"linked_items": int(links["count"]), "sync_runs": int(runs["count"])}
