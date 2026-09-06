"""REST authorization and transaction cleanup must leave the ASGI loop runnable."""

import asyncio
import threading
from contextlib import contextmanager
from contextvars import ContextVar

import anyio
import httpx
import pytest
from starlette.concurrency import run_in_threadpool

from app import db
from app.main import create_app
from app.services import promises, users


def test_contending_policy_requests_do_not_block_the_holder_commit(fresh_db, monkeypatch):
    users.ensure_user("policy-holder")
    users.ensure_user("policy-waiter")
    promise = promises.add_promise("Before", actor="policy-holder")["id"]
    holding = threading.Event()
    attempted = threading.Event()
    original_update = promises.edit_promise
    original_query = db.query

    def update(*args, **kwargs):
        result = original_update(*args, **kwargs)
        if kwargs.get("actor") == "policy-holder":
            holding.set()
            assert attempted.wait(5), "the second request never attempted its policy lock"
        return result

    def query(sql, params=()):
        if "FROM promises WHERE id = ? FOR UPDATE" in sql and holding.is_set():
            # PostgreSQL bounds the broken implementation too: an event-loop
            # timeout cannot fire while its synchronous SELECT blocks that loop.
            db.execute("SET LOCAL statement_timeout = '1500ms'")
            attempted.set()
        return original_query(sql, params)

    monkeypatch.setattr(promises, "edit_promise", update)
    monkeypatch.setattr(db, "query", query)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(), raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            first = asyncio.create_task(
                client.patch(
                    f"/api/promises/{promise}",
                    headers={"X-User": "policy-holder"},
                    json={"promise": "First"},
                )
            )
            assert await asyncio.to_thread(holding.wait, 5)
            second = asyncio.create_task(
                client.patch(
                    f"/api/promises/{promise}",
                    headers={"X-User": "policy-waiter"},
                    json={"promise": "Second"},
                )
            )
            return await asyncio.wait_for(asyncio.gather(first, second), 10)

    responses = asyncio.run(run())
    assert [response.status_code for response in responses] == [200, 200]
    assert fresh_db.query_row("SELECT promise FROM promises WHERE id = ?", (promise,)) == {
        "promise": "Second"
    }


def test_policy_lock_wait_is_bounded_and_retryable(fresh_db, monkeypatch):
    from app.services import policy_context

    promise = promises.add_promise("Before")["id"]
    held, release = threading.Event(), threading.Event()
    monkeypatch.setattr(db, "TRANSACTION_LOCK_TIMEOUT", "50ms")

    def hold():
        with db.transaction():
            policy_context.hold_resource("promise", promise)
            held.set()
            assert release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(5)

        async def run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
            ) as client:
                return await client.patch(
                    f"/api/promises/{promise}",
                    headers={"X-User": "waiter"},
                    json={"promise": "After"},
                )

        response = asyncio.run(run())
        assert response.status_code == 503
        assert response.headers["retry-after"] == "5"
        assert response.json() == {
            "detail": "The database is busy. Wait 5 seconds, then send the request again."
        }
        assert fresh_db.query_row("SELECT promise FROM promises WHERE id = ?", (promise,)) == {
            "promise": "Before"
        }
    finally:
        release.set()
        holder.join(5)
    assert not holder.is_alive()
    assert asyncio.run(run()).status_code == 200


@pytest.mark.parametrize("failure", [ValueError, RuntimeError])
def test_atomic_route_exception_rolls_back_write_and_callbacks(fresh_db, monkeypatch, failure):
    promise = promises.add_promise("Before")["id"]
    original = promises.edit_promise
    callbacks = []
    loop_thread = threading.get_ident()

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        assert db.on_commit(lambda: callbacks.append("committed"))
        assert db.on_rollback(lambda: callbacks.append(("rolled back", threading.get_ident())))
        raise failure("failed after writing")

    monkeypatch.setattr(promises, "edit_promise", fail)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(), raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            return await client.patch(
                f"/api/promises/{promise}",
                headers={"X-User": "editor"},
                json={"promise": "After"},
            )

    response = asyncio.run(run())
    assert response.status_code == (400 if failure is ValueError else 500)
    assert len(callbacks) == 1 and callbacks[0][0] == "rolled back"
    assert callbacks[0][1] != loop_thread
    assert fresh_db.query_row("SELECT promise FROM promises WHERE id = ?", (promise,)) == {
        "promise": "Before"
    }
    assert fresh_db.query_one("SELECT id FROM activity WHERE action = 'edit_promise'") is None


def test_atomic_route_validation_rolls_back_dependency_writes(fresh_db, monkeypatch):
    from app.routes import deps

    promise = promises.add_promise("Before")["id"]
    original = deps._resolve
    auth_threads = []
    loop_thread = threading.get_ident()

    def resolve(*args, **kwargs):
        auth_threads.append(threading.get_ident())
        return original(*args, **kwargs)

    monkeypatch.setattr(deps, "_resolve", resolve)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            return await client.patch(
                f"/api/promises/{promise}",
                headers={"X-User": "invalid-body-user"},
                json={"promise": ["not a string"]},
            )

    assert asyncio.run(run()).status_code == 422
    assert auth_threads and loop_thread not in auth_threads
    assert fresh_db.query_one("SELECT id FROM users WHERE name = 'invalid-body-user'") is None


def test_async_transaction_propagates_context_and_cleans_up_without_worker_capacity(fresh_db):
    marker = ContextVar("policy_test_context", default="missing")
    callback = []
    occupied, release = threading.Event(), threading.Event()

    def write():
        assert marker.get() == "request-context"
        assert db.in_transaction()
        with db.transaction():
            promise = promises.add_promise("Committed")["id"]
        assert db.on_commit(
            lambda: callback.append(
                (marker.get(), db.in_transaction(), threading.get_ident(), promise)
            )
        )

    def occupy_worker():
        occupied.set()
        assert release.wait(5)

    async def run():
        loop_thread = threading.get_ident()
        marker.set("request-context")
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        limiter.total_tokens = 1
        blocker = None
        try:
            async with db.async_transaction():
                await run_in_threadpool(write)
                blocker = asyncio.create_task(run_in_threadpool(occupy_worker))
                assert await asyncio.to_thread(occupied.wait, 3)
            assert not db.in_transaction()
            assert callback and callback[0][:2] == ("request-context", False)
            assert callback[0][2] != loop_thread
            assert not release.is_set()
        finally:
            release.set()
            if blocker is not None:
                await blocker
            limiter.total_tokens = previous

    asyncio.run(run())
    assert fresh_db.query_row("SELECT promise FROM promises WHERE id = ?", (callback[0][3],)) == {
        "promise": "Committed"
    }


@pytest.mark.parametrize("cancellation", ["asyncio", "anyio"])
def test_async_transaction_cancellation_rolls_back_and_resets_context(fresh_db, cancellation):
    callbacks = []

    def write():
        promises.add_promise("Cancelled")
        assert db.on_commit(lambda: callbacks.append("committed"))
        assert db.on_rollback(lambda: callbacks.append(("rolled back", db.in_transaction())))

    async def run():
        ready = asyncio.Event()
        scopes = []

        async def request():
            with anyio.CancelScope() as scope:
                scopes.append(scope)
                async with db.async_transaction():
                    await run_in_threadpool(write)
                    ready.set()
                    await asyncio.Event().wait()
            assert not db.in_transaction()

        request_task = asyncio.create_task(request())
        await asyncio.wait_for(ready.wait(), 3)
        if cancellation == "asyncio":
            request_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request_task
        else:
            scopes[0].cancel()
            await request_task
        assert not db.in_transaction()

    asyncio.run(run())
    assert callbacks == [("rolled back", False)]
    assert fresh_db.query_one("SELECT id FROM promises WHERE promise = 'Cancelled'") is None


@pytest.mark.parametrize("fail_entry", [False, True])
def test_async_transaction_joins_cancelled_entry_before_rollback(fresh_db, monkeypatch, fail_entry):
    original = db.transaction
    entering, release = threading.Event(), threading.Event()

    @contextmanager
    def paused_transaction(**kwargs):
        with original(**kwargs):
            # Use the service under the real nested manager, not this wrapper.
            with monkeypatch.context() as nested:
                nested.setattr(db, "transaction", original)
                promises.add_promise("Cancelled entry")
            entering.set()
            assert release.wait(5)
            if fail_entry:
                raise RuntimeError("entry failed after cancellation")
            yield

    monkeypatch.setattr(db, "transaction", paused_transaction)

    async def request():
        async with db.async_transaction():
            pytest.fail("cancelled entry reached the request body")

    async def run():
        task = asyncio.create_task(request())
        try:
            assert await asyncio.to_thread(entering.wait, 3)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)

    asyncio.run(run())
    assert fresh_db.query_one("SELECT id FROM promises WHERE promise = 'Cancelled entry'") is None
    assert not db.in_transaction()


def test_rest_auth_policy_and_commit_run_off_loop_but_body_reads_stay_on_it(fresh_db, monkeypatch):
    from fastapi import APIRouter

    from app.extensions import (
        IdentityContribution,
        PolicyContribution,
        PolicyResource,
        RouteContribution,
        RouteOperationContribution,
        SkeinModule,
    )
    from app.routes import deps

    loop_thread = threading.get_ident()
    request_marker = ContextVar("policy_request_marker", default="missing")
    seen = set()
    original_resolve = deps._resolve
    original_flush = db._flush_activity

    def observe(phase):
        assert threading.get_ident() != loop_thread, phase
        assert request_marker.get() == "request", phase
        seen.add(phase)

    def resolve(*args, **kwargs):
        observe("auth")
        return original_resolve(*args, **kwargs)

    def flush(*args):
        observe("commit")
        return original_flush(*args)

    def identities(*args):
        observe("identity")
        return {}

    def policy(request):
        observe("contributed-policy" if request.action == "acme.read" else "core-policy")
        return None

    router = APIRouter(prefix="/api/extensions/acme.workplace")

    @router.get("/item")
    def item():
        return {"ok": True}

    module = SkeinModule(
        module_id="acme.workplace",
        version="1.0.0",
        extension_api="1.0",
        minimum_core="0.2.0",
        maximum_core_exclusive="0.6.0",
        identities=(IdentityContribution("acme.workplace.identity", identities),),
        policies=(PolicyContribution("acme.workplace.policy", policy),),
        routes=(
            RouteContribution(
                "acme.workplace.item",
                router,
                (
                    RouteOperationContribution(
                        "GET",
                        "/api/extensions/acme.workplace/item",
                        "acme.read",
                        PolicyResource("acme-item"),
                        "read",
                        "low",
                    ),
                ),
            ),
        ),
    )
    monkeypatch.setattr(deps, "_resolve", resolve)
    monkeypatch.setattr(db, "_flush_activity", flush)

    async def run():
        loop = asyncio.get_running_loop()
        request_marker.set("request")

        async def body():
            assert asyncio.get_running_loop() is loop
            yield b'{"promise":'
            await asyncio.sleep(0)
            assert asyncio.get_running_loop() is loop
            yield b'"Streamed body"}'

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(modules=(module,))),
            base_url="http://test",
            headers={"X-User": "editor"},
        ) as client:
            response = await client.post(
                "/api/promises", headers={"Content-Type": "application/json"}, content=body()
            )
            assert response.status_code == 200
            assert (await client.get("/api/extensions/acme.workplace/item")).status_code == 200

    asyncio.run(run())
    assert seen == {"auth", "identity", "core-policy", "contributed-policy", "commit"}


@pytest.mark.parametrize("atomic", [False, True])
def test_auth_lock_wait_is_bounded_before_resource_policy(fresh_db, monkeypatch, atomic):
    from app.routes.deps import _resolve

    held, release = threading.Event(), threading.Event()
    monkeypatch.setattr(db, "TRANSACTION_LOCK_TIMEOUT", "50ms")

    def hold():
        with db.transaction():
            users.ensure_human_identity("identity-waiter")
            held.set()
            assert release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(3)

        def resolve():
            with db.transaction():
                # An independent statement bound also stops the unfixed
                # advisory-lock wait, before any resource hold is reached.
                db.execute("SET LOCAL statement_timeout = '500ms'")
                _resolve("identity-waiter", "", "POST")

        async def run():
            with pytest.raises(db.BUSY_ERRORS):
                if atomic:
                    async with db.async_transaction():
                        await run_in_threadpool(resolve)
                else:
                    await run_in_threadpool(resolve)

        asyncio.run(run())
    finally:
        release.set()
        holder.join(5)
    assert not holder.is_alive()


def test_non_atomic_auth_waiters_cannot_starve_atomic_request_workers(fresh_db, monkeypatch):
    from app.services import policy_context

    promise = promises.add_promise("Before")["id"]
    holding, attempted, release = threading.Event(), threading.Event(), threading.Event()
    original_context = policy_context.for_route_scoped
    original_lock = db.name_lock
    monkeypatch.setattr(db, "TRANSACTION_LOCK_TIMEOUT", "50ms")

    def context(*args):
        result = original_context(*args)
        if args[0] == "promises":
            holding.set()
            assert release.wait(5)
        return result

    def lock(namespace, name):
        if namespace == db.LOCK_IDENTITY and name == "new-shared-identity" and holding.is_set():
            db.execute("SET LOCAL statement_timeout = '750ms'")
            attempted.set()
        return original_lock(namespace, name)

    monkeypatch.setattr(policy_context, "for_route_scoped", context)
    monkeypatch.setattr(db, "name_lock", lock)

    async def run():
        limiter = anyio.to_thread.current_default_thread_limiter()
        previous = limiter.total_tokens
        limiter.total_tokens = 2
        requests = []
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(), raise_app_exceptions=False),
                base_url="http://test",
                headers={"X-User": "new-shared-identity"},
            ) as client:
                requests.append(
                    asyncio.create_task(
                        client.patch(f"/api/promises/{promise}", json={"promise": "After"})
                    )
                )
                assert await asyncio.to_thread(holding.wait, 3)
                requests.extend(
                    asyncio.create_task(
                        client.post("/api/tasks", json={"title": f"Concurrent {index}"})
                    )
                    for index in range(2)
                )
                assert await asyncio.to_thread(attempted.wait, 3)
                # One waiter owns the second token and another queues before
                # the holder can ask for its next synchronous dependency.
                await asyncio.sleep(0.01)
                release.set()
                responses = await asyncio.wait_for(asyncio.gather(*requests), 5)
                assert responses[0].status_code == 200
                assert all(response.status_code in (200, 503) for response in responses[1:])
        finally:
            release.set()
            if requests:
                await asyncio.gather(*requests, return_exceptions=True)
            limiter.total_tokens = previous

    asyncio.run(run())
    assert fresh_db.query_row("SELECT promise FROM promises WHERE id = ?", (promise,)) == {
        "promise": "After"
    }


def test_cancelled_asgi_request_cannot_write_through_a_recycled_connection(fresh_db):
    from fastapi import FastAPI

    from app.extensions.fastapi import PolicyAPIRoute
    from app.extensions.registry import ExtensionRegistry
    from app.routes.deps import CurrentUser

    promise = promises.add_promise("Before")["id"]
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    late_errors = []
    app = FastAPI()
    app.state.skein_registry = ExtensionRegistry.build(())
    app.router.route_class = PolicyAPIRoute

    @app.patch("/api/promises/{promise_id}")
    def edit(promise_id: int, user: CurrentUser):
        promises.edit_promise(promise_id, "Uncommitted", actor=user)
        entered.set()
        try:
            assert release.wait(5)
            try:
                promises.edit_promise(promise_id, "Late write", actor=user)
            except Exception as exc:
                late_errors.append(type(exc).__name__)
        finally:
            finished.set()
        return {"ok": True}

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            request = asyncio.create_task(
                client.patch(f"/api/promises/{promise}", headers={"X-User": "cancelled-editor"})
            )
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(request, 3)
                assert await asyncio.to_thread(
                    db.query_row, "SELECT promise FROM promises WHERE id = ?", (promise,)
                ) == {"promise": "Before"}
            finally:
                release.set()
                assert await asyncio.to_thread(finished.wait, 3)

    asyncio.run(run())
    assert late_errors, "the cancelled worker kept an open pooled connection"
    assert fresh_db.query_row("SELECT promise FROM promises WHERE id = ?", (promise,)) == {
        "promise": "Before"
    }
