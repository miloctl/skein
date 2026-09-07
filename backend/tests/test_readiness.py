"""Public probes remain bounded and disclose no database configuration."""

import asyncio
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import psycopg
from psycopg.conninfo import make_conninfo


def test_database_failure_and_recovery_do_not_stop_liveness(client, monkeypatch):
    from app import config

    url = config.DATABASE_URL
    monkeypatch.setattr(config, "DATABASE_URL", make_conninfo(url, password="probe-private-secret"))
    started = time.monotonic()
    response = client.get("/ready")
    assert response.status_code == 503
    assert time.monotonic() - started < 3
    assert response.json() == {"ok": False, "auth_mode": "trusted-header", "auth_error": ""}
    assert client.get("/health").status_code == 200
    monkeypatch.setattr(config, "DATABASE_URL", url)
    assert client.get("/ready").status_code == 200


def test_probe_has_wall_clock_bound_on_unresponsive_database(client, monkeypatch):
    async def blackhole(*args, **kwargs):
        await asyncio.sleep(30)
        raise AssertionError("probe did not cancel the socket operation")

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", blackhole)
    started = time.monotonic()
    assert client.get("/ready").status_code == 503
    assert time.monotonic() - started < 3
    assert client.get("/health").status_code == 200


def test_probe_closes_a_stalled_query_without_waiting_for_cancel(client, monkeypatch):
    from app import config

    connections = []
    handlers = set()
    query_seen = asyncio.Event()
    query_closed = asyncio.Event()

    def packet(kind, body):
        return kind + struct.pack("!I", len(body) + 4) + body

    async def postgres(reader, writer):
        handler = asyncio.current_task()
        handlers.add(handler)
        connections.append(writer)
        is_query = False
        try:
            size = struct.unpack("!I", await reader.readexactly(4))[0]
            if not 4 <= size <= 8192:
                return
            initial = await reader.readexactly(size - 4)
            if initial[:4] == struct.pack("!I", 196608):
                writer.write(
                    packet(b"R", struct.pack("!I", 0))
                    + packet(b"S", b"client_encoding\x00UTF8\x00")
                    + packet(b"S", b"server_version\x0017.0\x00")
                    + packet(b"K", struct.pack("!II", 123, 456))
                    + packet(b"Z", b"I")
                )
                await writer.drain()
                query = await reader.read(4096)
                assert b"SELECT 1" in query
                is_query = True
                query_seen.set()
            await reader.read()
        except (asyncio.IncompleteReadError, OSError):
            pass
        finally:
            writer.close()
            handlers.discard(handler)
            if is_query:
                query_closed.set()

    async def start():
        return await asyncio.start_server(postgres, "127.0.0.1", 0)

    server = client.portal.call(start)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(
        config,
        "DATABASE_URL",
        f"host=127.0.0.1 port={port} dbname=test user=test sslmode=disable gssencmode=disable",
    )

    async def wait_seen(event):
        await asyncio.wait_for(event.wait(), 1)

    async def stop():
        server.close()
        pending = tuple(handlers)
        for handler in pending:
            handler.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await server.wait_closed()

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as requests:
        pending = requests.submit(client.get, "/ready")
        try:
            client.portal.call(wait_seen, query_seen)
            assert client.get("/health").status_code == 200
            response = pending.result(timeout=3)
            assert response.status_code == 503
            assert time.monotonic() - started < 3
            client.portal.call(wait_seen, query_closed)
            assert len(connections) == 1
        finally:
            client.portal.call(stop)


def test_probe_bounds_a_real_slow_database_query(client, monkeypatch):
    from app import db

    execute = psycopg.AsyncConnection.execute
    pids = []

    async def slow_query(connection, query, *args, **kwargs):
        pids.append(connection.info.backend_pid)
        return await execute(connection, "SELECT pg_sleep(30)")

    monkeypatch.setattr(psycopg.AsyncConnection, "execute", slow_query)
    started = time.monotonic()
    try:
        assert client.get("/ready").status_code == 503
        assert time.monotonic() - started < 3
        assert client.get("/health").status_code == 200
    finally:
        # Closing the probe cannot promise that a remote server stops work.
        # Cancel only this test's artificial sleep before its database is dropped.
        for pid in pids:
            db.query(
                "SELECT pg_cancel_backend(pid) FROM pg_stat_activity WHERE pid = ? AND datname = current_database() AND query = 'SELECT pg_sleep(30)'",
                (pid,),
            )


def test_probe_does_not_wait_for_the_request_connection_pool(client):
    from app import db

    pool = db.pool()
    with ExitStack() as stack:
        for _ in range(pool.max_size):
            stack.enter_context(pool.connection(timeout=3))
        started = time.monotonic()
        assert client.get("/ready").status_code == 503
        assert time.monotonic() - started < 3
        assert client.get("/health").status_code == 200


def test_closed_application_pool_is_not_ready(client):
    from app import db

    db.pool().close()
    try:
        assert client.get("/ready").status_code == 503
        assert client.get("/health").status_code == 200
    finally:
        db.close_pool()
    db.pool().wait(timeout=3)
    assert client.get("/ready").status_code == 200


def test_liveness_is_not_queued_behind_saturated_request_threads(client):
    from anyio.to_thread import current_default_thread_limiter

    borrower = object()

    async def saturate():
        limiter = current_default_thread_limiter()
        previous = limiter.total_tokens
        limiter.total_tokens = 1
        await limiter.acquire_on_behalf_of(borrower)
        return limiter, previous

    limiter, previous = client.portal.call(saturate)

    def release():
        limiter.release_on_behalf_of(borrower)
        limiter.total_tokens = previous

    with ThreadPoolExecutor(max_workers=1) as requests:
        pending = requests.submit(client.get, "/health")
        try:
            assert pending.result(timeout=1).status_code == 200
        finally:
            client.portal.call(release)


def test_database_configuration_fault_is_private(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "DATABASE_ERROR", "private-database.internal: secret-password")
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"ok": False, "auth_mode": "trusted-header", "auth_error": ""}
    assert client.get("/health").status_code == 200
