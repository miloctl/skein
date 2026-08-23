"""Extension-owned schemas and isolated migration streams."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from hashlib import sha256
from typing import Any

from .. import db
from .contracts import ExtensionMigration

# Extension names come from a manifest and match registry._IDENTIFIER
# (lowercase, dot- and dash-separated). Neither separator is legal unquoted in
# a schema name, so both fold to an underscore.
_UNSAFE = re.compile(r"[^a-z0-9_]")


def schema_for(name: str) -> str:
    """The schema an extension named `name` owns.

    Prefixed rather than used bare: without it an extension called `public`
    would name the core schema, and one called `private` would name the 1:1
    notes.
    """
    slug = _UNSAFE.sub("_", name.strip().lower())
    if not slug or not slug[0].isascii() or not slug[0].isalpha():
        raise ValueError("An extension store needs a name that starts with a letter.")
    schema = f"ext_{slug}"
    if len(schema.encode("utf-8")) > 63:
        raise ValueError("An extension store schema name must be 63 bytes or fewer.")
    return schema


class ExtensionStore:
    """Tables in a schema of the extension's own, never the core schema.

    Unqualified names in extension SQL resolve inside that schema and nowhere
    else (db.schema_scope sets search_path per transaction). That is a
    STATEMENT-level boundary, not a privilege one: an in-process module runs
    as the Skein process on one connection role, so SQL that NAMES
    `public.tasks` still reaches it. Extensions are trusted code loaded from
    the deployment's own image — the registry, not this class, is what decides
    which ones load.
    """

    def __init__(self, name: str, *, include_in_backup: bool = True) -> None:
        self.name = name
        # The prefix is what makes a core-schema collision impossible: an
        # extension named `public` or `private` lands in ext_public /
        # ext_private and reaches neither.
        self.schema = schema_for(name)
        # Defaults to true because losing an extension's data by omission is
        # the worse failure. A store whose contents are rebuildable from the
        # remote system it mirrors can opt out.
        self.include_in_backup = include_in_backup

    @contextmanager
    def _scope(self) -> Iterator[None]:
        db.ensure_owned_schema(self.schema)
        with db.schema_scope(self.schema):
            yield

    def migrate(self, migrations: Sequence[ExtensionMigration]) -> None:
        """Apply this extension's own numbered migrations, once each.

        The digest pins the STATEMENTS of an already-applied version: an
        extension that edits a shipped migration in place would otherwise have
        two different schemas in the field under one version number, and
        nothing would say which one a database has.
        """
        with db.transaction(), self._scope():
            # The schema name lock from ensure_owned_schema stays held until
            # every migration statement and its version receipt commit.
            db.execute(
                "CREATE TABLE IF NOT EXISTS extension_schema_version"
                " (version bigint PRIMARY KEY, name text NOT NULL,"
                " applied_at text NOT NULL, digest text NOT NULL)"
            )
            for migration in sorted(migrations, key=lambda item: item.version):
                digest = sha256("\0".join(migration.statements).encode()).hexdigest()
                row = db.query_one(
                    "SELECT name, digest FROM extension_schema_version WHERE version = ?",
                    (migration.version,),
                )
                if row:
                    if row["name"] != migration.name:
                        raise ValueError(
                            f"extension migration {migration.version} changed its name"
                        )
                    if row["digest"] != digest:
                        raise ValueError(
                            f"extension migration {migration.version} changed its statements"
                        )
                    continue
                for statement in migration.statements:
                    db.execute(statement)
                db.execute(
                    "INSERT INTO extension_schema_version"
                    " (version, name, applied_at, digest) VALUES (?, ?, ?, ?)",
                    (migration.version, migration.name, db.now(), digest),
                )

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run a write. Returns the first column of the RETURNING row, else 0.

        There is no last-inserted-id to hand back: an INSERT whose id you need
        must ask for it with `RETURNING id`. Without one this returns 0, so
        `new_id = store.execute("INSERT ...")` reads as a successful write and
        yields a falsy id.
        """
        with self._scope():
            return db.execute(sql, tuple(params))

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._scope():
            return db.query(sql, tuple(params))

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self) -> Iterator[ExtensionStore]:
        """Serialize one extension-owned operation across threads and workers."""
        with self._scope():
            yield self
