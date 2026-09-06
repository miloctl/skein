"""Concurrent writes keep name claims, settlement, and requests single-use."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import pytest


@contextmanager
def _pause_first(monkeypatch, target, attribute, matches):
    original = getattr(target, attribute)
    paused, release = Event(), Event()

    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        if matches(*args, **kwargs) and not paused.is_set():
            paused.set()
            assert release.wait(5), "the first writer was not released"
        return result

    monkeypatch.setattr(target, attribute, wrapped)
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            yield pool, paused, release
        finally:
            release.set()


def _race(pool, paused, release, first, second):
    def outcome(call):
        try:
            return call()
        except Exception as exc:
            return exc

    first_result = pool.submit(outcome, first)
    assert paused.wait(5), "the first writer did not reach the read/write gap"
    attempted, finished = Event(), Event()

    def run_second():
        attempted.set()
        try:
            return outcome(second)
        finally:
            finished.set()

    second_result = pool.submit(run_second)
    assert attempted.wait(5)
    finished.wait(0.2)
    release.set()
    return first_result.result(5), second_result.result(5)


@pytest.mark.parametrize(
    "operations", [("create", "create"), ("rename", "rename"), ("create", "rename")]
)
@pytest.mark.parametrize("names", [("ALPHA", "alpha"), ("İris", "iris")])
def test_engagement_name_collision_is_a_validation_error(fresh_db, monkeypatch, operations, names):
    from app.services import engagements

    ids = [engagements.create_engagement(f"Original {i}", actor="tester")["id"] for i in range(2)]
    assert fresh_db.query_row("SELECT lower(?) = lower(?) AS same", names)["same"]

    def write(index):
        if operations[index] == "create":
            return engagements.create_engagement(names[index], actor="tester")
        return engagements.update_engagement(ids[index], name=names[index], actor="tester")

    with _pause_first(
        monkeypatch,
        fresh_db,
        "query_one",
        lambda sql, *args, **kwargs: sql.startswith("SELECT id FROM engagements WHERE lower(name)"),
    ) as (pool, paused, release):
        results = _race(pool, paused, release, lambda: write(0), lambda: write(1))

    assert sum(isinstance(result, dict) for result in results) == 1, results
    failures = [result for result in results if isinstance(result, Exception)]
    assert len(failures) == 1 and isinstance(failures[0], ValueError), results
    assert "already exists" in str(failures[0])
    assert (
        fresh_db.query_row(
            "SELECT COUNT(*) AS n FROM engagements WHERE lower(name) = lower(?)", (names[0],)
        )["n"]
        == 1
    )


def test_engagement_rename_matches_the_policy_entry_lock_order(fresh_db, monkeypatch):
    from app.services import engagements, policy_context, users

    users.ensure_user("scout", kind="agent")
    eid = engagements.create_engagement("Original", actor="tester")["id"]

    def policy_update():
        with fresh_db.transaction():
            policy_context.hold_resource("engagement", eid)
            return engagements.update_engagement(
                eid, name="Renamed", summary="human change", actor="tester"
            )

    with _pause_first(monkeypatch, engagements, "_hold_name", lambda *_args, **_kwargs: True) as (
        pool,
        paused,
        release,
    ):
        results = _race(
            pool,
            paused,
            release,
            lambda: engagements.update_engagement(
                eid, name="Renamed", summary="agent change", actor="scout", origin="agent"
            ),
            policy_update,
        )

    assert all(isinstance(result, dict) for result in results), results
    assert fresh_db.query_row("SELECT name, summary FROM engagements WHERE id = ?", (eid,)) == {
        "name": "Renamed",
        "summary": "human change",
    }


@pytest.mark.parametrize("second_action", ["settle", "edit"])
def test_promise_settlement_prevents_concurrent_history_writes(
    fresh_db, monkeypatch, second_action
):
    from app.services import promises, users

    users.ensure_user("scout", kind="agent")
    promise = promises.add_promise("ship the agreed scope", actor="tester")
    pid = promise["id"]
    with _pause_first(
        monkeypatch,
        promises,
        "_emit_promise_event",
        lambda event_type, *args, **kwargs: event_type == "skein.promise.updated",
    ) as (pool, paused, release):
        results = _race(
            pool,
            paused,
            release,
            lambda: promises.update_promise(pid, "kept", actor="scout", origin="agent"),
            lambda: (
                promises.update_promise(pid, "missed", actor="scout", origin="agent")
                if second_action == "settle"
                else promises.edit_promise(pid, promise="rewrite history", actor="tester")
            ),
        )

    assert results[0] == {"id": pid, "status": "kept"}
    assert isinstance(results[1], ValueError), results
    assert fresh_db.query_row("SELECT promise, status FROM promises WHERE id = ?", (pid,)) == {
        "promise": "ship the agreed scope",
        "status": "kept",
    }
    assert (
        fresh_db.query_row(
            "SELECT COUNT(*) AS n FROM activity WHERE action IN ('update_promise', 'edit_promise')"
        )["n"]
        == 1
    )
    assert (
        fresh_db.query_row(
            "SELECT COUNT(*) AS n FROM extension_outbox WHERE event_type = 'skein.promise.updated'"
        )["n"]
        == 1
    )


def test_concurrent_key_requests_share_one_unread_notification(fresh_db, monkeypatch):
    from app.services import api_keys, users

    user = users.ensure_user("tester")["name"]
    with _pause_first(
        monkeypatch,
        fresh_db,
        "query_one",
        lambda sql, *args, **kwargs: sql.startswith("SELECT id FROM notifications"),
    ) as (pool, paused, release):
        results = _race(
            pool,
            paused,
            release,
            lambda: api_keys.request_key(user),
            lambda: api_keys.request_key(user),
        )

    assert all(isinstance(result, dict) and result["requested"] for result in results), results
    assert sorted(result["already_pending"] for result in results) == [False, True]
    assert fresh_db.query_row("SELECT COUNT(*) AS n FROM notifications")["n"] == 1
    assert (
        fresh_db.query_row("SELECT COUNT(*) AS n FROM activity WHERE action = 'request_key'")["n"]
        == 1
    )
