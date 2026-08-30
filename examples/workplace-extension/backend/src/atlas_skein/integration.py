"""Atlas work-system adapter built only on Skein public contracts."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import partial
from http.client import HTTPMessage
from threading import BoundedSemaphore, Event, Thread
from time import monotonic
from typing import IO, Any, Literal, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.extensions import EventExecutionContext, ExtensionStore
from app.public import (
    CommandContext,
    CreateTaskCommand,
    DomainEvent,
    TaskView,
    UpdateTaskCommand,
    WorkItems,
)

MAX_RESPONSE_BYTES = 256 * 1024
MAX_ITEMS = 500
MAX_STATUS_DELIVERIES = 50
STATUS_DRAIN_SECONDS = 10
STATUS_LEASE_SECONDS = 30
STATUS_RETRY_SECONDS = 60
_TRANSIENT_HTTP_STATUSES = {408, 425, 429}
_SYNC_ORIGINS = {
    "extension:atlas.workplace.routes",
    "extension:atlas.workplace.sync",
    "extension:atlas.workplace.sync-tool",
}


class AtlasUnavailableError(RuntimeError):
    """The Atlas adapter could not complete a temporary remote operation."""


class AtlasBadResponseError(RuntimeError):
    """The Atlas API refused the request or returned unusable data."""


class _RefuseRedirect(HTTPRedirectHandler):
    def http_error_302(
        self,
        _req: Request,
        response: IO[bytes],
        _code: int,
        _message: str,
        _headers: HTTPMessage,
    ) -> Any | None:
        response.close()
        raise AtlasBadResponseError("The Atlas API redirected an authenticated request.")

    # urllib gives every redirect code the same handler signature, but typeshed
    # declares the inherited aliases as separate methods.
    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302  # type: ignore[assignment]


_NO_REDIRECT_OPENER = build_opener(_RefuseRedirect)
# Timed-out blocking calls cannot be killed. Fixed daemon slots keep slow
# provider code from consuming the application's worker pool without limit.
_HTTP_SLOTS = BoundedSemaphore(4)
_STATUS_SLOTS = BoundedSemaphore(4)


@dataclass
class _ThreadResult[ResultT]:
    value: ResultT | None = None
    error: BaseException | None = None


def _run_bounded[ResultT](
    work: Callable[[], ResultT],
    deadline: float,
    slots: BoundedSemaphore,
    *,
    thread_name: str,
    timeout_message: str,
) -> ResultT:
    remaining = max(0.0, deadline - monotonic())
    if not slots.acquire(timeout=remaining):
        raise AtlasUnavailableError(timeout_message)
    done = Event()
    result = _ThreadResult[ResultT]()

    def run() -> None:
        try:
            result.value = work()
        except BaseException as exc:
            result.error = exc
        finally:
            done.set()
            slots.release()

    try:
        Thread(target=run, name=thread_name, daemon=True).start()
    except BaseException:
        slots.release()
        raise
    if not done.wait(max(0.0, deadline - monotonic())):
        raise AtlasUnavailableError(timeout_message)
    if result.error is not None:
        raise result.error
    return cast(ResultT, result.value)


def _run_http(work: Callable[[], bytes | None], deadline: float) -> bytes | None:
    return _run_bounded(
        work,
        deadline,
        _HTTP_SLOTS,
        thread_name="atlas-http",
        timeout_message="The Atlas API response timed out.",
    )


def _run_status(work: Callable[[], None], deadline: float) -> None:
    return _run_bounded(
        work,
        deadline,
        _STATUS_SLOTS,
        thread_name="atlas-status",
        timeout_message="The Atlas status delivery timed out.",
    )


def _read_bounded_response(response: Any, deadline: float) -> bytes:
    raw = bytearray()
    read = getattr(response, "read1", response.read)
    while len(raw) <= MAX_RESPONSE_BYTES:
        if monotonic() >= deadline:
            raise AtlasUnavailableError("The Atlas API response timed out.")
        chunk = read(MAX_RESPONSE_BYTES + 1 - len(raw))
        if monotonic() >= deadline:
            raise AtlasUnavailableError("The Atlas API response timed out.")
        if not chunk:
            break
        raw.extend(chunk)
    return bytes(raw)


@dataclass(frozen=True)
class AtlasItem:
    external_id: str
    title: str
    status: str = "todo"
    classification: str = "internal"


class _AtlasItemPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    external_id: str = Field(min_length=1, max_length=180)
    title: str = Field(min_length=1, max_length=200)
    status: Literal["todo", "in_progress", "blocked", "done", "void"] = "todo"
    classification: str = Field("internal", min_length=1, max_length=80)

    @field_validator("external_id", "title", "classification")
    @classmethod
    def usable_text(cls, value: str) -> str:
        if not value.strip() or not value.isprintable():
            raise ValueError("Atlas item text must be usable by Skein.")
        return value


class _AtlasItemsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    items: list[_AtlasItemPayload] = Field(max_length=MAX_ITEMS)


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
        if timeout_seconds <= 0:
            raise ValueError("The Atlas API timeout must be greater than zero.")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        read_json: bool = False,
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
        deadline = monotonic() + self.timeout_seconds

        def perform() -> bytes | None:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise AtlasUnavailableError("The Atlas API response timed out.")
            try:
                # A redirect can move the Authorization header to another origin
                # or downgrade it to plaintext; the opener refuses instead.
                with _NO_REDIRECT_OPENER.open(request, timeout=remaining) as response:
                    if monotonic() >= deadline:
                        raise AtlasUnavailableError("The Atlas API response timed out.")
                    if not read_json:
                        return None
                    return _read_bounded_response(response, deadline)
            except AtlasBadResponseError:
                raise
            except HTTPError as exc:
                exc.close()
                error = (
                    AtlasUnavailableError
                    if exc.code in _TRANSIENT_HTTP_STATUSES or 500 <= exc.code <= 599
                    else AtlasBadResponseError
                )
                raise error("The Atlas API request failed.") from exc
            except (URLError, TimeoutError, OSError) as exc:
                raise AtlasUnavailableError("The Atlas API request failed.") from exc

        raw = _run_http(perform, deadline)
        if raw is None:
            return None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise AtlasBadResponseError("The Atlas API response is too large.")
        try:
            parsed: object = json.loads(raw)
            return parsed
        except (RecursionError, TypeError, ValueError) as exc:
            raise AtlasBadResponseError("The Atlas API returned invalid JSON.") from exc

    def list_items(self) -> tuple[AtlasItem, ...]:
        result = self._request("GET", "/items", read_json=True)
        try:
            payload = _AtlasItemsPayload.model_validate(
                result if isinstance(result, dict) else {"items": result}
            )
        except ValidationError as exc:
            raise AtlasBadResponseError("The Atlas API returned an invalid item list.") from exc
        external_ids = [item.external_id for item in payload.items]
        if len(set(external_ids)) != len(external_ids):
            raise AtlasBadResponseError("The Atlas API returned duplicate item IDs.")
        return tuple(
            AtlasItem(
                external_id=item.external_id,
                title=item.title,
                status=item.status,
                classification=item.classification,
            )
            for item in payload.items
        )

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

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[None]:
        """One unit of extension-owned work.

        Delegates to the store. A store is a SCHEMA in the Skein database now,
        not a file of its own, so ExtensionStore.transaction() is the whole
        contract — and it nests, which is what the re-entrancy guard that used
        to live here provided.
        """
        with self.store.transaction():
            yield

    def _execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        return self.store.execute(sql, params)

    def _query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return self.store.query(sql, params)

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
        # Network I/O runs AFTER the mapping is committed, never inside the
        # transaction that wrote it. A retry from a different contribution then
        # reuses the mapping and the same remote idempotency key instead of
        # creating another task — and a slow remote never holds a write open.
        # (Core and extension data now live in one database, so this ordering
        # is a deliberate choice rather than something the storage forces.)
        self._deliver_pending_statuses(work, context)
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
            # The SAME shape db.now() writes — ISO-8601, seconds, UTC. These are
            # TEXT columns compared lexicographically across the tree, and
            # now()::text renders a space separator that sorts before "T".
            self._execute(
                "INSERT INTO sync_runs (created_count, updated_count, finished_at)"
                " VALUES (?, ?, to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'))",
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
                "INSERT INTO sync_claims"
                " (external_id, owner_namespace, skein_task_id) VALUES (?, ?, NULL)"
                " ON CONFLICT DO NOTHING",
                (external_id, owner_namespace),
            )
            # FOR UPDATE, then LOOK AGAIN. Two syncs of the same item both read
            # "no link" and both insert — one no-ops on the conflict. The lock
            # is what makes the loser wait for the winner to finish linking,
            # and the second read of work_links is what it sees when it wakes
            # up. Without the pair, both callers create a task for one item.
            claim = self._query_one(
                "SELECT owner_namespace, skein_task_id FROM sync_claims"
                " WHERE external_id = ? FOR UPDATE",
                (external_id,),
            )
            relinked = self._query_one(
                "SELECT skein_task_id FROM work_links WHERE external_id = ?",
                (external_id,),
            )
            if relinked is not None:
                return {"linked_task_id": int(relinked["skein_task_id"])}
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
            # The INSERT reports whether it created the link — a read taken
            # BEFORE it cannot. Two syncs of the same item both see "no prior
            # link" and both count a creation, so one item is reported created
            # twice while only one row exists. RETURNING yields a row only for
            # the caller that actually inserted.
            inserted = self._query(
                "INSERT INTO work_links"
                " (external_id, skein_task_id, classification) VALUES (?, ?, ?)"
                " ON CONFLICT DO NOTHING RETURNING external_id",
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
            return bool(inserted)

    def _queue_status(self, event_id: str, external_id: str, status: str) -> None:
        self._execute(
            "INSERT INTO status_outbox"
            " (event_id, external_id, status, delivered) VALUES (?, ?, ?, 0)"
            " ON CONFLICT DO NOTHING",
            (event_id, external_id, status),
        )

    def _record_status(self, external_id: str, task: TaskView) -> None:
        self._queue_status(
            f"atlas-status:{external_id}:{task.updated_at}:{task.status}",
            external_id,
            task.status,
        )

    def _claim_pending_status(self) -> dict[str, Any] | None:
        with self._transaction():
            # An earlier row for the SAME item blocks every later status, even
            # while another worker holds its lease. Other items stay claimable.
            pending = self._query_one(
                "SELECT pending.event_id, pending.external_id, pending.status"
                " FROM status_outbox AS pending WHERE pending.delivered = 0"
                " AND pending.dead = 0"
                " AND (pending.lease_until IS NULL OR pending.lease_until <= now())"
                " AND NOT EXISTS (SELECT 1 FROM status_outbox AS earlier"
                " WHERE earlier.external_id = pending.external_id"
                " AND earlier.delivered = 0 AND earlier.dead = 0"
                " AND earlier.sequence_id < pending.sequence_id)"
                " ORDER BY pending.sequence_id"
                " FOR UPDATE OF pending SKIP LOCKED LIMIT 1"
            )
            if pending is None:
                return None
            token = uuid4().hex
            leased = self._query(
                "UPDATE status_outbox SET lease_token = ?,"
                " lease_until = now() + (? * INTERVAL '1 second')"
                " WHERE event_id = ? AND delivered = 0"
                " RETURNING event_id, external_id, status, lease_token",
                (token, STATUS_LEASE_SECONDS, str(pending["event_id"])),
            )
            if not leased:
                raise RuntimeError("The Atlas status delivery was not leased.")
            return leased[0]

    def _mark_status_delivered(self, delivery: dict[str, Any]) -> bool:
        with self._transaction():
            updated = self._query(
                "UPDATE status_outbox SET delivered = 1, lease_token = '',"
                " lease_until = NULL, error_code = ''"
                " WHERE event_id = ? AND delivered = 0"
                " AND lease_token = ? RETURNING event_id",
                (str(delivery["event_id"]), str(delivery["lease_token"])),
            )
            return bool(updated)

    def _defer_status(self, delivery: dict[str, Any], error_code: str) -> None:
        with self._transaction():
            self._execute(
                "UPDATE status_outbox SET lease_token = '',"
                " lease_until = now() + (? * INTERVAL '1 second'), error_code = ?"
                " WHERE event_id = ? AND delivered = 0 AND dead = 0"
                " AND lease_token = ?",
                (
                    STATUS_RETRY_SECONDS,
                    error_code,
                    str(delivery["event_id"]),
                    str(delivery["lease_token"]),
                ),
            )

    def _dead_status(self, delivery: dict[str, Any], error_code: str) -> None:
        with self._transaction():
            self._execute(
                "UPDATE status_outbox SET dead = 1, lease_token = '',"
                " lease_until = NULL, error_code = ?"
                " WHERE event_id = ? AND delivered = 0 AND lease_token = ?",
                (
                    error_code,
                    str(delivery["event_id"]),
                    str(delivery["lease_token"]),
                ),
            )

    def _has_deferred_status(self) -> bool:
        return (
            self._query_one(
                "SELECT 1 AS present FROM status_outbox WHERE delivered = 0"
                " AND dead = 0 AND lease_token = '' AND lease_until > now() LIMIT 1"
            )
            is not None
        )

    def _status_settled(self, event_id: str) -> bool:
        row = self._query_one(
            "SELECT delivered, dead FROM status_outbox WHERE event_id = ?",
            (event_id,),
        )
        return row is not None and bool(row["delivered"] or row["dead"])

    def _status_is_current(
        self,
        delivery: dict[str, Any],
        work: WorkItems,
        context: CommandContext,
    ) -> bool:
        link = self._query_one(
            "SELECT skein_task_id FROM work_links WHERE external_id = ?",
            (str(delivery["external_id"]),),
        )
        if link is None:
            return False
        task = work.get_task(int(link["skein_task_id"]), context)
        return task.status == str(delivery["status"])

    def _deliver_pending_statuses(
        self,
        work: WorkItems | None = None,
        context: CommandContext | None = None,
    ) -> None:
        deadline = monotonic() + STATUS_DRAIN_SECONDS
        first_failure: Exception | None = None
        for _ in range(MAX_STATUS_DELIVERIES):
            if monotonic() >= deadline:
                break
            delivery = self._claim_pending_status()
            if delivery is None:
                break
            if (
                work is not None
                and context is not None
                and not self._status_is_current(delivery, work, context)
            ):
                self._dead_status(delivery, "ATLAS_STATUS_SUPERSEDED")
                continue
            external_id = str(delivery["external_id"])
            status = str(delivery["status"])
            event_id = str(delivery["event_id"])
            try:
                # The lease is committed before this call. A slow remote never
                # holds an extension transaction or blocks claims for other items.
                _run_status(
                    partial(self.client.update_status, external_id, status, event_id),
                    deadline,
                )
            except AtlasBadResponseError as exc:
                try:
                    self._dead_status(delivery, "ATLAS_BAD_RESPONSE")
                except Exception as settlement_error:
                    raise exc from settlement_error
                first_failure = first_failure or exc
                continue
            except Exception as exc:
                error_code = (
                    "ATLAS_UNAVAILABLE"
                    if isinstance(exc, AtlasUnavailableError)
                    else "ATLAS_DELIVERY_FAILED"
                )
                try:
                    self._defer_status(delivery, error_code)
                except Exception as settlement_error:
                    raise exc from settlement_error
                first_failure = first_failure or exc
                continue
            self._mark_status_delivered(delivery)
        if first_failure is not None:
            raise first_failure
        if self._has_deferred_status():
            raise AtlasUnavailableError("An Atlas status delivery is waiting for retry.")

    def deliver_task_event(
        self,
        event: DomainEvent,
        context: EventExecutionContext,
    ) -> None:
        if event.origin in _SYNC_ORIGINS:
            return
        link = self._query_one(
            "SELECT external_id FROM work_links WHERE skein_task_id = ?",
            (int(event.resource.id),),
        )
        if not link:
            return
        command_context = context.command_context()
        task = context.work_items.get_task(int(event.resource.id), command_context)
        # Task events share the same queue as synchronization. A direct PATCH can
        # overtake an older leased status and then be overwritten when it finishes.
        self._queue_status(context.delivery_id, str(link["external_id"]), task.status)
        self._deliver_pending_statuses(context.work_items, command_context)
        if not self._status_settled(context.delivery_id):
            raise AtlasUnavailableError("The Atlas status delivery is still pending.")

    def metrics(self) -> dict[str, int]:
        links = self._query_one("SELECT COUNT(*) AS count FROM work_links")
        runs = self._query_one("SELECT COUNT(*) AS count FROM sync_runs")
        if links is None or runs is None:
            raise RuntimeError("The Atlas extension store is not initialized.")
        return {"linked_items": int(links["count"]), "sync_runs": int(runs["count"])}
