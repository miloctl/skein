"""Run extension handlers while core work stays on the calling thread."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, TypeVar

from ..extensions.policy import PolicyEngine
from .errors import PublicError
from .work import CommandContext, CreateTaskCommand, TaskView, UpdateTaskCommand, WorkItems

_ResultT = TypeVar("_ResultT")


class _DeadlineExpired(Exception):
    """Internal marker for a handler that passed its declared deadline."""


def _closed_error() -> PublicError:
    return PublicError(
        "EXECUTION_CONTEXT_CLOSED",
        "The execution deadline passed before this work request could run.",
    )


@dataclass
class _OwnerCall:
    operation: Callable[[], Any]
    finished: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


class _OwnerDispatcher:
    """Move public work calls to the thread that owns the outer transaction."""

    def __init__(self) -> None:
        self.owner_thread = threading.get_ident()
        self._condition = threading.Condition()
        self._pending: deque[_OwnerCall] = deque()
        self._closed = False

    def call(self, operation: Callable[[], _ResultT]) -> _ResultT:
        with self._condition:
            if self._closed:
                raise _closed_error()
            if threading.get_ident() == self.owner_thread:
                inline = True
            else:
                inline = False
                request = _OwnerCall(operation)
                self._pending.append(request)
                self._condition.notify()
        if inline:
            return operation()
        request.finished.wait()
        if request.error is not None:
            raise request.error
        return request.result

    def run_until(self, future: Future[_ResultT], timeout: float) -> _ResultT:
        deadline = time.monotonic() + timeout
        completed_at: list[float] = []

        def wake(_future: Future[_ResultT]) -> None:
            with self._condition:
                completed_at.append(time.monotonic())
                self._condition.notify()

        future.add_done_callback(wake)
        try:
            while True:
                with self._condition:
                    now = time.monotonic()
                    if future.done():
                        finished_at = completed_at[0] if completed_at else now
                        if finished_at <= deadline:
                            break
                        raise _DeadlineExpired
                    if now >= deadline:
                        raise _DeadlineExpired
                    if self._pending:
                        request = self._pending.popleft()
                    else:
                        self._condition.wait(deadline - now)
                        continue
                try:
                    from .. import db

                    # A top-level WorkItems method normally owns a transaction.
                    # Here it joins the review transaction, so give the queued
                    # command the equivalent rollback boundary before its
                    # exception is transferred back to the handler thread.
                    with db.savepoint():
                        request.result = request.operation()
                except BaseException as exc:
                    request.error = exc
                finally:
                    request.operation = _discarded_operation
                    request.finished.set()
                if time.monotonic() >= deadline:
                    raise _DeadlineExpired
            return future.result()
        finally:
            self.close()

    def close(self) -> None:
        """Close atomically with enqueue and release every waiting handler."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
            pending = tuple(self._pending)
            self._pending.clear()
        for request in pending:
            request.error = _closed_error()
            request.operation = _discarded_operation
            request.finished.set()


def _discarded_operation() -> None:
    return None


class _OwnerWorkItems(WorkItems):
    """A public WorkItems facade whose database calls run on one owner thread."""

    def __init__(self, policy: PolicyEngine, dispatcher: _OwnerDispatcher) -> None:
        super().__init__(policy)
        self._dispatcher = dispatcher

    def _issue_context(
        self,
        execution_context: object,
        *,
        project_type: str = "",
        attributes: dict[str, Any] | None = None,
    ) -> CommandContext:
        return self._dispatcher.call(
            lambda: WorkItems._issue_context(
                self,
                execution_context,
                project_type=project_type,
                attributes=attributes,
            )
        )

    def get_task(self, task_id: int, context: CommandContext) -> TaskView:
        return self._dispatcher.call(lambda: WorkItems.get_task(self, task_id, context))

    def create_task(self, command: CreateTaskCommand, context: CommandContext) -> TaskView:
        return self._dispatcher.call(lambda: WorkItems.create_task(self, command, context))

    def update_task(self, command: UpdateTaskCommand, context: CommandContext) -> TaskView:
        return self._dispatcher.call(lambda: WorkItems.update_task(self, command, context))


@dataclass(frozen=True)
class BoundedHandlerResult:
    value: Any = None
    timed_out: bool = False


def run_bounded_work_handler[ContextT](
    policy: PolicyEngine,
    bind: Callable[[WorkItems], ContextT],
    handler: Callable[[ContextT, Any], Any],
    request: Any,
    timeout: float,
    *,
    thread_name: str,
) -> BoundedHandlerResult:
    """Run a handler in a worker and its public work on this calling thread.

    The calling thread can own a review transaction. The worker never receives
    that connection — db.py keys the ambient transaction to the context, so a
    worker thread starts with none. At the deadline, the facade closes before
    this function returns, so a late handler cannot use a committed
    connection.
    """
    dispatcher = _OwnerDispatcher()
    work_items = _OwnerWorkItems(policy, dispatcher)
    services = bind(work_items)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=thread_name)
    future = executor.submit(handler, services, request)
    try:
        try:
            return BoundedHandlerResult(dispatcher.run_until(future, timeout))
        except _DeadlineExpired:
            future.cancel()
            return BoundedHandlerResult(timed_out=True)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
