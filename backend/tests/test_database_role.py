"""The deployed application role, exercised against a disposable database."""

import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql as pgsql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

pytestmark = pytest.mark.skipif(
    os.getenv("SKEIN_ROLE_CONTRACT") != "1",
    reason="run separately with SKEIN_ROLE_CONTRACT=1",
)


def _require_disposable_superuser(control) -> None:
    current = control.execute(
        "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if not current or not current[0]:
        pytest.fail(
            "The database role contract requires the disposable PostgreSQL superuser.",
            pytrace=False,
        )


def _bootstrap_conninfo(info: dict, database: str, user: str) -> tuple[str, str]:
    params = {key: value for key, value in info.items() if value is not None}
    password = str(params.pop("password", ""))
    params.update(dbname=database, user=user)
    return make_conninfo(**{key: str(value) for key, value in params.items()}), password


def test_bootstrap_role_runs_skein_without_database_create(monkeypatch):
    from conftest import _create_test_database, _drop_test_database

    from app import config, db
    from app.extensions.contracts import ExtensionMigration
    from app.extensions.data import ExtensionStore
    from app.services import admin, private_notes

    original_url = config.DATABASE_URL
    original_error = config.DATABASE_ERROR
    info = conninfo_to_dict(original_url)
    suffix = uuid4().hex[:8]
    created = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    database = f"skein_role_{created}_{suffix}"
    role = f"skein_app_{created}_{suffix}"
    password = "p@ss:w/x?#'quoted"
    script = Path(__file__).parents[2] / "deploy" / "postgres-init" / "10-app-role.sh"

    with psycopg.connect(original_url, autocommit=True) as control:
        _require_disposable_superuser(control)
        bootstrap_user = control.info.user
    _create_test_database(original_url, database)

    # psql does not read SKEIN_DATABASE_URL. Pass its complete connection
    # contract as conninfo (service, TLS, failover, options) and keep a literal
    # password out of process arguments.
    bootstrap_conninfo, bootstrap_password = _bootstrap_conninfo(info, database, bootstrap_user)
    # Keep ambient PG settings because the configured conninfo can deliberately
    # rely on PGSERVICEFILE, PGPASSFILE, or client-certificate paths.
    env = dict(os.environ)
    env.update(
        {
            "POSTGRES_USER": bootstrap_user,
            "POSTGRES_DB": database,
            "POSTGRES_CONNINFO": bootstrap_conninfo,
            "SKEIN_APP_USER": role,
            "SKEIN_APP_PASSWORD": password,
            **({"PGPASSWORD": bootstrap_password} if bootstrap_password else {}),
        }
    )
    try:
        shell = shutil.which("bash")
        assert shell, "bash must be installed for the role bootstrap drill"
        for _ in range(2):
            subprocess.run(  # noqa: S603 — fixed shell and repository script
                [shell, str(script)],
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
        admin_info = {**info, "dbname": database}
        admin_url = make_conninfo(
            **{key: str(value) for key, value in admin_info.items() if value is not None}
        )
        with psycopg.connect(admin_url, autocommit=True) as control:
            control.execute(
                pgsql.SQL("CREATE SCHEMA ext_role_contract AUTHORIZATION {}").format(
                    pgsql.Identifier(role)
                )
            )
            flags = control.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls"
                " FROM pg_roles WHERE rolname = %s",
                (role,),
            ).fetchone()
            assert flags == (False, False, False, False)
            assert control.execute(
                "SELECT has_database_privilege(%s, %s, 'CREATE')", (role, database)
            ).fetchone() == (False,)

        role_info = {**info, "dbname": database, "user": role, "password": password}
        role_url = make_conninfo(
            **{key: str(value) for key, value in role_info.items() if value is not None}
        )
        monkeypatch.setattr(config, "DATABASE_URL", role_url)
        monkeypatch.setattr(config, "DATABASE_ERROR", "")
        db.close_pool()
        private_notes._schema_ready = False
        db.init_db()
        assert db.privilege_warnings() == []
        note = private_notes.add_note("mira", "dana", "restricted role works")
        assert private_notes.list_notes("mira", "dana")[0]["id"] == note["id"]

        store = ExtensionStore("role_contract")
        store.migrate(
            (
                ExtensionMigration(
                    1,
                    "records",
                    ("CREATE TABLE records (value text NOT NULL)",),
                ),
            )
        )
        store.execute("INSERT INTO records (value) VALUES (?)", ("works",))
        assert store.query("SELECT value FROM records") == [{"value": "works"}]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("CREATE SCHEMA application_must_not_create")

        admin.set_extension_stores({"role": store.schema}, {"role"})
        result = admin.backup()
        restore = shutil.which("pg_restore")
        assert restore, "pg_restore must be installed for the role backup drill"
        listing = subprocess.run(  # noqa: S603 — absolute pg_restore path
            [restore, "--list", result["database_path"]],
            env=admin._pg_env(),
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "SCHEMA - private" in listing
        assert "SCHEMA - ext_role_contract" in listing
    finally:
        admin.set_extension_stores({}, set())
        private_notes._schema_ready = False
        monkeypatch.setattr(config, "DATABASE_URL", original_url)
        monkeypatch.setattr(config, "DATABASE_ERROR", original_error)
        db.close_pool()
        # Plain DROP exposes a leaked app or restore connection. Killing it
        # here would let the security contract pass while its cleanup is broken.
        _drop_test_database(original_url, database)
        with psycopg.connect(original_url, autocommit=True) as control:
            control.execute(pgsql.SQL("DROP ROLE IF EXISTS {}").format(pgsql.Identifier(role)))
