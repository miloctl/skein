"""Contracts for pytest's PostgreSQL controller and disposable databases."""

import os
import secrets
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import conftest as fixtures
import psycopg
import pytest
from psycopg import sql as pgsql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._row


def _offline_env(**overrides):
    env = dict(os.environ)
    for name in (
        "SKEIN_DATABASE_URL",
        "SKEIN_DB_HOST",
        "SKEIN_DB_PORT",
        "SKEIN_DB_USER",
        "SKEIN_DB_PASSWORD",
        "SKEIN_DB_NAME",
    ):
        env[name] = ""
    env.update(overrides)
    return env


class _ProbeConnection:
    def __init__(self, flags=(False, True)):
        self.flags = flags
        self.queries: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, _params=None):
        text = str(query)
        self.queries.append(text)
        if "rolsuper" in text:
            return _Result(self.flags)
        return _Result((1,))


def test_database_names_are_unique_parseable_and_bounded(monkeypatch):
    created = datetime(2026, 8, 25, 1, 2, 3, tzinfo=UTC)
    first = fixtures._test_database_name(
        "scratch", created, "run id with punctuation", "gw0", suffix="0123abcd"
    )
    second = fixtures._test_database_name(
        "scratch", created, "run id with punctuation", "gw0", suffix="89abcdef"
    )

    suffixes = iter(("11111111", "22222222"))
    monkeypatch.setattr(fixtures.secrets, "token_hex", lambda _bytes: next(suffixes))
    random_first = fixtures._test_database_name(
        "scratch", created, "run id with punctuation", "gw0"
    )
    random_second = fixtures._test_database_name(
        "scratch", created, "run id with punctuation", "gw0"
    )

    assert first != second
    assert random_first != random_second
    assert first == "skein_scratch_20260825010203_43a79528105b_gw0_0123abcd"
    assert len(first.encode()) <= 63
    assert fixtures._test_database_created_at(first) == created
    assert fixtures._test_database_created_at("skein_app_20260825010203_0123abcd") == created
    assert fixtures._test_database_created_at("skein_scratch_gw0_12345") is None
    assert fixtures._test_database_created_at("skein_test_20260825010203_bad") is None


def test_database_conninfo_replaces_only_the_database_name():
    source = make_conninfo(
        user="skein",
        password="p@ss/w?#'quoted",
        host="localhost",
        port="5432",
        dbname="source",
        options="-c search_path=public",
    )

    changed = conninfo_to_dict(fixtures._database_conninfo(source, "target"))

    assert changed == {**conninfo_to_dict(source), "dbname": "target"}


def test_create_database_cleans_a_lost_ack_but_not_a_name_collision(monkeypatch):
    dropped = []

    class Admin:
        def __init__(self, error):
            self.error = error

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _query):
            raise self.error

    monkeypatch.setattr(fixtures, "_drop_test_database", lambda *args: dropped.append(args))
    monkeypatch.setattr(
        psycopg,
        "connect",
        lambda *_args, **_kwargs: Admin(psycopg.OperationalError("lost acknowledgement")),
    )
    with pytest.raises(psycopg.OperationalError):
        fixtures._create_test_database("base", "unique-name")
    assert dropped == [("base", "unique-name")]

    dropped.clear()
    monkeypatch.setattr(
        psycopg,
        "connect",
        lambda *_args, **_kwargs: Admin(psycopg.errors.DuplicateDatabase("collision")),
    )
    with pytest.raises(psycopg.errors.DuplicateDatabase):
        fixtures._create_test_database("base", "not-owned")
    assert dropped == []


@pytest.mark.parametrize("flags", [(True, False), (False, True)])
def test_postgres_preflight_accepts_superuser_or_createdb(monkeypatch, flags):
    connection = _ProbeConnection(flags)
    call = {}

    def connect(url, **kwargs):
        call.update(url=url, **kwargs)
        return connection

    monkeypatch.setattr(psycopg, "connect", connect)
    monkeypatch.setattr(fixtures, "_old_test_databases", lambda _connection: [])
    monkeypatch.setattr(fixtures, "_old_test_roles", lambda _connection: [])

    assert fixtures._postgres_preflight("postgresql://skein:secret@db/skein") is None
    assert call == {
        "url": "postgresql://skein:secret@db/skein",
        "autocommit": True,
        "connect_timeout": 3,
    }
    assert connection.queries[:2] == [
        "SET statement_timeout = '3s'",
        "SET lock_timeout = '3s'",
    ]
    assert "SELECT 1" in connection.queries


def test_postgres_preflight_returns_fixed_nonsecret_errors(monkeypatch):
    secret_url = "postgresql://hidden-user:hidden-password@hidden-host/skein"

    def refused(*_args, **_kwargs):
        raise psycopg.OperationalError("hidden-password connection detail")

    monkeypatch.setattr(psycopg, "connect", refused)
    connection_error = fixtures._postgres_preflight(secret_url)
    assert connection_error == fixtures._POSTGRES_UNAVAILABLE
    assert "hidden" not in connection_error

    monkeypatch.setattr(
        psycopg, "connect", lambda *_args, **_kwargs: _ProbeConnection((False, False))
    )
    capability_error = fixtures._postgres_preflight(secret_url)
    assert capability_error == fixtures._POSTGRES_CAPABILITY
    assert "hidden" not in capability_error


def test_postgres_preflight_names_old_orphans_without_deleting(monkeypatch):
    connection = _ProbeConnection()
    orphan = "skein_test_20260823010203_0123456789ab_gw0_0123abcd"
    monkeypatch.setattr(psycopg, "connect", lambda *_args, **_kwargs: connection)
    monkeypatch.setattr(fixtures, "_old_test_databases", lambda _connection: [orphan])
    monkeypatch.setattr(fixtures, "_old_test_roles", lambda _connection: [])

    error = fixtures._postgres_preflight("postgresql://skein:secret@db/skein")

    assert error == (
        "Old inactive pytest resources were found. Delete these exact names,"
        f" then run the tests again: {orphan}"
    )
    assert all(
        "drop database" not in query.lower() and "drop role" not in query.lower()
        for query in connection.queries
    )


def test_orphan_database_catalog_is_scoped_to_the_current_owner():
    class CatalogConnection:
        query = ""

        def execute(self, query):
            self.query = str(query)
            return _Result([])

    connection = CatalogConnection()

    assert fixtures._old_test_databases(connection) == []
    assert "owner.rolname = current_user" in connection.query


def test_pure_selection_runs_without_postgres():
    path = Path(__file__)
    result = subprocess.run(  # noqa: S603 — fixed Python, pytest module and test path
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-n0",
            f"{path}::test_database_names_are_unique_parseable_and_bounded",
        ],
        cwd=path.parents[1],
        env=_offline_env(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_role_contract_normal_skip_happens_before_database_fixtures():
    path = Path(__file__).with_name("test_database_role.py")
    result = subprocess.run(  # noqa: S603 — fixed Python, pytest module and test path
        [sys.executable, "-m", "pytest", "-q", "-n0", "--setup-show", str(path)],
        cwd=path.parents[1],
        env=_offline_env(SKEIN_ROLE_CONTRACT="0"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    output = result.stdout + result.stderr

    assert result.returncode == 0, output
    assert "1 skipped" in output
    assert "_worker_db" not in output
    assert "scratch_db" not in output


def test_role_contract_preserves_libpq_connection_parameters():
    from test_database_role import _bootstrap_conninfo

    source = {
        "service": "skein-test",
        "passfile": "/tmp/pass file",
        "password": "literal-secret",
        "sslmode": "verify-full",
        "sslrootcert": "/tmp/root.pem",
        "target_session_attrs": "read-write",
        "options": "-c search_path=private",
    }
    conninfo, password = _bootstrap_conninfo(source, "role-db", "role-admin")

    assert password == "literal-secret"
    assert "literal-secret" not in conninfo
    assert conninfo_to_dict(conninfo) == {
        **{key: value for key, value in source.items() if key != "password"},
        "dbname": "role-db",
        "user": "role-admin",
    }


def test_explicit_role_contract_fails_closed_for_nonsuperuser():
    from test_database_role import _require_disposable_superuser

    class Control:
        def execute(self, _query):
            return _Result((False,))

    with pytest.raises(
        pytest.fail.Exception,
        match=r"The database role contract requires the disposable PostgreSQL superuser\.",
    ):
        _require_disposable_superuser(Control())


def test_postgres_commands_allow_a_clean_twenty_minute_shutdown():
    root = Path(__file__).parents[2]
    compose = (root / "docker-compose.yml").read_text()
    assert "stop_grace_period: 20m" in compose
    postgres = (root / "deploy" / "k8s" / "base" / "postgres.yaml").read_text()
    assert "terminationGracePeriodSeconds: 1200" in postgres
    role_contract = (root / "backend" / "tests" / "test_database_role.py").read_text()
    assert "pg_terminate_backend" not in role_contract
    bootstrap = (root / "deploy" / "postgres-init" / "10-app-role.sh").read_text()
    assert "POSTGRES_CONNINFO" in bootstrap
    assert "POSTGRES_CONNINFO" in postgres

    for path in (
        root / "CLAUDE.md",
        root / "backend" / ".env.example",
        root / "backend" / "tests" / "conftest.py",
        root / "scripts" / "skein.sh",
    ):
        assert "--stop-timeout 1200" in path.read_text(), path
    for path in (
        root / "CLAUDE.md",
        root / "backend" / ".env.example",
        root / "scripts" / "skein.sh",
    ):
        assert "127.0.0.1:" in path.read_text(), path


def test_worker_database_setup_failure_drops_the_created_database(
    monkeypatch, testrun_uid, worker_id
):
    from app import config, db

    name = fixtures._test_database_name("test", datetime.now(UTC), testrun_uid, worker_id)
    previous_url = config.DATABASE_URL
    previous_error = config.DATABASE_ERROR
    previous_env = os.environ.get("SKEIN_DATABASE_URL")
    monkeypatch.setattr(fixtures, "_test_database_name", lambda *_args, **_kwargs: name)
    monkeypatch.setattr(db, "init_db", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    generator = fixtures._worker_db.__wrapped__(worker_id, testrun_uid)

    try:
        with pytest.raises(RuntimeError, match="boom"):
            next(generator)
        with psycopg.connect(previous_url, autocommit=True) as control:
            assert (
                control.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
                is None
            )
        assert previous_url == config.DATABASE_URL
        assert previous_error == config.DATABASE_ERROR
        assert os.environ.get("SKEIN_DATABASE_URL") == previous_env
    finally:
        db.close_pool()
        config.DATABASE_URL = previous_url
        config.DATABASE_ERROR = previous_error
        if previous_env is None:
            os.environ.pop("SKEIN_DATABASE_URL", None)
        else:
            os.environ["SKEIN_DATABASE_URL"] = previous_env
        with psycopg.connect(previous_url, autocommit=True) as control:
            control.execute(pgsql.SQL("DROP DATABASE IF EXISTS {}").format(pgsql.Identifier(name)))


def test_old_orphan_detector_names_only_owned_inactive_new_format(
    _worker_db, testrun_uid, worker_id
):
    created = datetime.now(UTC).replace(microsecond=0)
    check_at = created + timedelta(hours=25)
    old = fixtures._test_database_name("test", created, testrun_uid, worker_id)
    # The candidate timestamps stay fresh to concurrent real-time preflights.
    # Advancing only this direct detector call makes the test deterministic
    # without blocking another pytest session that shares the server.
    fresh = fixtures._test_database_name(
        "test", created + timedelta(hours=2), testrun_uid, worker_id
    )
    active = fixtures._test_database_name("scratch", created, testrun_uid, worker_id)
    legacy = f"skein_test_{worker_id}_{secrets.token_hex(4)}"
    names = (old, fresh, active, legacy)
    created_names: list[str] = []
    active_connection = None
    with psycopg.connect(_worker_db, autocommit=True) as control:
        try:
            for name in names:
                control.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(name)))
                created_names.append(name)
            active_connection = psycopg.connect(
                fixtures._database_conninfo(_worker_db, active), autocommit=True
            )

            detected = fixtures._old_test_databases(control, now=check_at)
            assert old in detected
            assert fresh not in detected
            assert active not in detected
            assert legacy not in detected
            remaining = {
                row[0]
                for row in control.execute(
                    "SELECT datname FROM pg_database WHERE datname = ANY(%s)",
                    (list(names),),
                ).fetchall()
            }
            assert remaining == set(names)
        finally:
            if active_connection is not None:
                active_connection.close()
            for name in created_names:
                control.execute(
                    pgsql.SQL("DROP DATABASE IF EXISTS {}").format(pgsql.Identifier(name))
                )
