"""Bounded application-pool availability and fresh database authentication."""

import asyncio
from contextlib import suppress

import psycopg

from .. import config, db


async def database_ready() -> bool:
    if config.DATABASE_ERROR:
        return False
    try:
        # db.pool() has no networked checkout/reset callbacks. Adding one would
        # block this event loop. A full or reconnecting pool must fail promptly.
        with db.pool().connection(timeout=0.01):
            # A pooled socket can hide a changed password. The fresh connection's
            # wall-clock deadline includes DNS, authentication and the query.
            async with asyncio.timeout(2):
                connection = await psycopg.AsyncConnection.connect(
                    config.DATABASE_URL, autocommit=True, connect_timeout=2
                )
                query = asyncio.create_task(connection.execute("SELECT 1"))
                try:
                    cursor = await asyncio.shield(query)
                    return await cursor.fetchone() == (1,)
                finally:
                    # Psycopg cancellation opens a second socket, then waits for
                    # the original query. Close first so neither can outlive the
                    # deadline (tests/test_readiness.py's stalled protocol peer).
                    await connection.close()
                    query.cancel()
                    with suppress(asyncio.CancelledError, psycopg.Error):
                        await query
    except (psycopg.Error, TimeoutError, OSError, ValueError):
        return False
