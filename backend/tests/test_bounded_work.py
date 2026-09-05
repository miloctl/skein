"""Bounded extension work shares the caller's transaction and lifetime."""

import threading
from contextlib import nullcontext

import pytest

from app.extensions import JobExecutionContext
from app.extensions.policy import PolicyDecision, PolicyEffect, PolicyEngine, PolicySubject
from app.public import (
    CreateBlockerCommand,
    CreatePromiseCommand,
    CreateTaskCommand,
    UpdateBlockerCommand,
    UpdatePromiseCommand,
    UpdateTaskCommand,
)
from app.public._owner_work import run_bounded_work_handler
from app.public.errors import PublicError
from app.public.work import WorkItems, _bind_execution_context


def _bind(work):
    subject = PolicySubject("bounded-worker", kind="service")
    return _bind_execution_context(
        work,
        JobExecutionContext(PolicyEngine(), work, subject, "run", "test.bounded"),
        subject=subject,
        namespace="test.bounded",
        receipt_namespace="test:bounded",
        correlation_id="run",
    )


KINDS = (
    ("task", CreateTaskCommand(title="bounded"), UpdateTaskCommand(task_id=1, title="late")),
    (
        "blocker",
        CreateBlockerCommand(title="bounded"),
        UpdateBlockerCommand(blocker_id=1, title="late"),
    ),
    (
        "promise",
        CreatePromiseCommand(promise="bounded"),
        UpdatePromiseCommand(promise_id=1, status="kept"),
    ),
)


@pytest.mark.parametrize("kind,create,update", KINDS)
def test_preissued_commands_expire_at_the_handler_deadline(fresh_db, kind, create, update):
    release, finished = threading.Event(), threading.Event()
    outcomes = []

    def handler(services, _):
        context = services.command_context()
        try:
            release.wait(5)
            for operation, argument in (("get", 1), ("create", create), ("update", update)):
                try:
                    getattr(services.work_items, f"{operation}_{kind}")(argument, context)
                    outcomes.append("allowed")
                except PublicError as exc:
                    outcomes.append(exc.code)
        finally:
            finished.set()

    try:
        result = run_bounded_work_handler(
            PolicyEngine(), _bind, handler, None, 0.1, thread_name="bounded-test"
        )
        assert result.timed_out
    finally:
        release.set()
        assert finished.wait(5)
    assert outcomes == ["EXECUTION_CONTEXT_CLOSED"] * 3
    assert fresh_db.query_one(f"SELECT COUNT(*) AS n FROM {kind}s")["n"] == 0  # noqa: S608 — closed KINDS table names


@pytest.mark.parametrize("kind,create,update", KINDS)
def test_commands_join_the_owner_rollback(fresh_db, kind, create, update):
    def handler(services, _):
        return getattr(services.work_items, f"create_{kind}")(create, services.command_context())

    with pytest.raises(RuntimeError, match="rollback"), fresh_db.transaction():
        result = run_bounded_work_handler(
            PolicyEngine(), _bind, handler, None, 5, thread_name="bounded-test"
        )
        assert not result.timed_out
        raise RuntimeError("rollback")
    assert fresh_db.query_one(f"SELECT COUNT(*) AS n FROM {kind}s")["n"] == 0  # noqa: S608 — closed KINDS table names


def test_completed_handler_revokes_preissued_context(fresh_db):
    result = run_bounded_work_handler(
        PolicyEngine(),
        _bind,
        lambda services, _: (services.work_items, services.command_context()),
        None,
        5,
        thread_name="bounded-test",
    )
    work, context = result.value
    with pytest.raises(PublicError) as denied:
        work._require_issued_context(context)
    assert denied.value.code == "EXECUTION_CONTEXT_CLOSED"


@pytest.mark.parametrize("kind,create,update", KINDS)
@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize("outer", [False, True])
def test_held_command_keeps_the_review_it_returns(fresh_db, kind, create, update, operation, outer):
    if operation == "update":
        services = _bind(WorkItems(PolicyEngine()))
        getattr(services.work_items, f"create_{kind}")(create, services.command_context())
    policy = PolicyEngine(
        (
            lambda request: (
                PolicyDecision(PolicyEffect.REVIEW, ("Needs review",))
                if request.action == f"work.{kind}.{operation}"
                else None
            ),
        )
    )

    def handler(services, _):
        try:
            getattr(services.work_items, f"{operation}_{kind}")(
                create if operation == "create" else update, services.command_context()
            )
        except PublicError as exc:
            assert exc.code == "REVIEW_REQUIRED"
            return exc.review_id
        pytest.fail("The command was not held")

    with fresh_db.transaction() if outer else nullcontext():
        result = run_bounded_work_handler(
            policy, _bind, handler, None, 5, thread_name="bounded-test"
        )
        assert not result.timed_out
    row = fresh_db.query_one("SELECT status FROM pending_changes WHERE id = ?", (result.value,))
    assert row is not None and row["status"] == "pending"
