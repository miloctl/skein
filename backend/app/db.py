"""Thin SQLite layer. One connection per operation keeps this thread-safe
under uvicorn without a pool. Schema lives in ../migrations/*.sql."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .config import DB_PATH

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _statements(sql: str) -> list[str]:
    """Split a migration into statements. Convention: migrations contain no
    semicolons inside string literals or trigger bodies."""
    return [s.strip() for s in sql.split(";") if s.strip()]


def init_db() -> None:
    """Apply pending migrations in filename order; track in schema_version.

    Each migration runs inside ONE transaction (BEGIN IMMEDIATE) together with
    its schema_version insert, so a crash mid-migration rolls back cleanly and
    concurrent workers serialize on the write lock instead of double-applying.
    """
    conn = connect()
    conn.isolation_level = None  # explicit transaction control
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version"
            " (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.execute("BEGIN IMMEDIATE")
            try:
                already = conn.execute(
                    "SELECT 1 FROM schema_version WHERE version = ?", (path.name,)
                ).fetchone()
                if already:
                    conn.execute("COMMIT")
                    continue
                for stmt in _statements(path.read_text()):
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                    (path.name, now()),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.close()


def query(sql: str, params: tuple = ()) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def query_one(sql: str, params: tuple = ()) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> int:
    """Run a write statement, return lastrowid."""
    with connect() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid


def execute_rowcount(sql: str, params: tuple = ()) -> int:
    """Run a write statement, return the number of affected rows (for
    compare-and-swap guards like `... WHERE status = 'pending'`)."""
    with connect() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount


def log_activity(actor: str, action: str, detail: str = "") -> None:
    execute(
        "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
        (actor, action, detail, now()),
    )
