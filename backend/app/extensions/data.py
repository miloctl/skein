"""Extension-owned SQLite stores and isolated migration streams."""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

from .. import config, db
from .contracts import ExtensionMigration


class ExtensionStore:
    """A store that never opens the core or private Skein database."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

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
        return connection

    def migrate(self, migrations: Sequence[ExtensionMigration]) -> None:
        self._make_sure_is_separate()
        with contextlib.closing(self.connect()) as connection:
            connection.isolation_level = None
            connection.execute(
                "CREATE TABLE IF NOT EXISTS extension_schema_version"
                " (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL,"
                " digest TEXT NOT NULL DEFAULT '')"
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(extension_schema_version)"
                ).fetchall()
            }
            if "digest" not in columns:
                connection.execute(
                    "ALTER TABLE extension_schema_version"
                    " ADD COLUMN digest TEXT NOT NULL DEFAULT ''"
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
                        if row["digest"] and row["digest"] != digest:
                            raise ValueError(
                                f"extension migration {migration.version} changed its statements"
                            )
                        if not row["digest"]:
                            connection.execute(
                                "UPDATE extension_schema_version SET digest = ? WHERE version = ?",
                                (digest, migration.version),
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
        with contextlib.closing(self.connect()) as connection:
            cursor = connection.execute(sql, tuple(params))
            connection.commit()
            return int(cursor.lastrowid or 0)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with contextlib.closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute(sql, tuple(params)).fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None
