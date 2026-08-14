"""Extension-owned SQLite stores and isolated migration streams."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterator, Sequence
from contextvars import ContextVar
from hashlib import sha256
from pathlib import Path
from typing import Any

from .. import config, db
from .contracts import ExtensionMigration


def _deny_attach(action: int, *_rest: object) -> int:
    """Refuse ATTACH on an extension connection.

    The path check in _make_sure_is_separate only sees the file this store
    opened, so one ATTACH statement would reach a core database from a
    connection that passed it. This closes that statement, not the trust
    model: an in-process module runs as the Skein process and can still open
    any file that process can.
    """
    return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_ATTACH else sqlite3.SQLITE_OK


class ExtensionStore:
    """A store that never opens the core or private Skein database."""

    def __init__(self, path: Path | str, *, include_in_backup: bool = True) -> None:
        self.path = Path(path)
        # Defaults to true because losing an extension's data by omission is
        # the worse failure. A store whose contents are rebuildable from the
        # remote system it mirrors can opt out.
        self.include_in_backup = include_in_backup
        self._active: ContextVar[sqlite3.Connection | None] = ContextVar(
            f"skein_extension_store_{id(self)}",
            default=None,
        )

    def _make_sure_is_separate(self) -> None:
        target = self.path.resolve()
        protected = {Path(db.DB_PATH).resolve(), Path(config.PRIVATE_DB_PATH).resolve()}
        if target in protected:
            raise ValueError("An extension store cannot use a Skein core database path.")

    def connect(self) -> sqlite3.Connection:
        self._make_sure_is_separate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        # After the PRAGMAs: an authorizer set before them denies nothing here,
        # but every statement the extension runs passes through it.
        connection.set_authorizer(_deny_attach)
        return connection

    def migrate(self, migrations: Sequence[ExtensionMigration]) -> None:
        self._make_sure_is_separate()
        with contextlib.closing(self.connect()) as connection:
            connection.isolation_level = None
            connection.execute(
                "CREATE TABLE IF NOT EXISTS extension_schema_version"
                " (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL,"
                " digest TEXT NOT NULL)"
            )
            for migration in sorted(migrations, key=lambda item: item.version):
                digest = sha256("\0".join(migration.statements).encode()).hexdigest()
                connection.execute("BEGIN IMMEDIATE")
                try:
                    row = connection.execute(
                        "SELECT name, digest FROM extension_schema_version WHERE version = ?",
                        (migration.version,),
                    ).fetchone()
                    if row:
                        if row["name"] != migration.name:
                            raise ValueError(
                                f"extension migration {migration.version} changed its name"
                            )
                        if row["digest"] != digest:
                            raise ValueError(
                                f"extension migration {migration.version} changed its statements"
                            )
                        connection.execute("COMMIT")
                        continue
                    for statement in migration.statements:
                        connection.execute(statement)
                    broken = connection.execute("PRAGMA foreign_key_check").fetchmany(5)
                    if broken:
                        raise sqlite3.IntegrityError(
                            f"extension migration {migration.version} leaves broken foreign keys"
                        )
                    connection.execute(
                        "INSERT INTO extension_schema_version"
                        " (version, name, applied_at, digest) VALUES (?, ?, ?, ?)",
                        (migration.version, migration.name, db.now(), digest),
                    )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        active = self._active.get()
        if active is not None:
            cursor = active.execute(sql, tuple(params))
            return int(cursor.lastrowid or 0)
        with contextlib.closing(self.connect()) as connection:
            cursor = connection.execute(sql, tuple(params))
            connection.commit()
            return int(cursor.lastrowid or 0)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        active = self._active.get()
        if active is not None:
            return [dict(row) for row in active.execute(sql, tuple(params)).fetchall()]
        with contextlib.closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute(sql, tuple(params)).fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextlib.contextmanager
    def transaction(self) -> Iterator[ExtensionStore]:
        """Serialize one extension-owned operation across threads and workers."""
        active = self._active.get()
        if active is not None:
            yield self
            return
        with contextlib.closing(self.connect()) as connection:
            connection.isolation_level = None
            connection.execute("BEGIN IMMEDIATE")
            token = self._active.set(connection)
            try:
                yield self
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            finally:
                self._active.reset(token)
