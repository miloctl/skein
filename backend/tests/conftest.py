import os
import tempfile

os.environ["SKEIN_DATA_DIR"] = tempfile.mkdtemp(prefix="skein-test-")
os.environ["SKEIN_SCHEDULER"] = "0"
os.environ["SKEIN_MODEL_PROVIDER"] = "mock"
os.environ["SKEIN_AGENT_REVIEW"] = "0"
os.environ["SKEIN_EMBEDDINGS"] = "0"
# "" and not pop: config's load_dotenv() re-fills an ABSENT var from
# backend/.env, so popping is exactly what would let a dev box's overlay
# leak into the suite. Empty survives load_dotenv and means "no overlay".
os.environ["SKEIN_PLAYBOOKS_DIR"] = ""
os.environ["SKEIN_PERSONAS_DIR"] = ""
# Same reason: a deployment that curates a menu in backend/.env otherwise
# gives every menu-sensitive assertion (agents/status, /health warnings,
# personas.unlisted_model_warnings) a registry the test never wrote.
os.environ["SKEIN_MODELS"] = ""
# The <NAME>_FILE hatch (config._structured) leaks the same way, one step
# worse: a dev box that points SKEIN_MODELS_FILE at a mounted menu turns the
# line above from an empty menu into a FAULT, because setting both forms of
# one setting is refused on purpose. Every structured setting gets BOTH names
# blanked so a future local price, param or MCP document cannot steer the suite.
os.environ["SKEIN_MODELS_FILE"] = ""
os.environ["SKEIN_MODEL_PRICES"] = ""
os.environ["SKEIN_MODEL_PRICES_FILE"] = ""
os.environ["SKEIN_MODEL_PARAMS"] = ""
os.environ["SKEIN_MODEL_PARAMS_FILE"] = ""
os.environ["SKEIN_MCP_SERVERS"] = ""
os.environ["SKEIN_MCP_SERVERS_FILE"] = ""
# Same reason again, and this one bites only in the evening: with the
# deployment's zone in force, db.today() and db.now()[:10] are the same string
# west of UTC only until 20:00 local. A suite that reads the developer's zone
# passes all day and fails after dinner, and CI (UTC, no .env) never sees it.
os.environ["SKEIN_TZ"] = ""

from datetime import UTC

import pytest


@pytest.fixture(autouse=True)
def _reset_ratelimit():
    from app import ratelimit

    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture(autouse=True)
def _core_policy_engine():
    """Unit tests dispatch stock tools without an application entry point.

    Production entry points install the composed engine, and
    current_policy_engine() fails closed without one. Install the
    core-rules-only engine here so a direct tool call keeps the exact
    pre-composition behavior. A test that composes workplace rules
    installs its own engine on top with its own token."""
    from app.extensions.policy import (
        PolicyEngine,
        reset_policy_engine,
        set_policy_engine,
    )

    token = set_policy_engine(PolicyEngine())
    yield
    reset_policy_engine(token)


@pytest.fixture(autouse=True)
def _reset_telemetry_buffers(monkeypatch):
    """Process-local perf state must not cross test databases: a detect()
    timestamp from one test would let hint() skip detection against the next
    test's fresh db, and a buffered tool_usage count would land in the wrong
    database (or never land — tests assert counts right after record_use, so
    the 30s buffer is zeroed to flush per call). Receipts too: a box left
    set by one test's chat turn collects a later test's gate writes in the
    same worker, and whichever test drains next fails on the leftovers. The
    consult budget is the same shape: a SPENT box left by one turn refuses
    every later consult in that worker, before the sub-agent is even built."""
    from app.agents import identity, receipts
    from app.services import adoption, fieldguide

    adoption.reset()
    fieldguide.reset()
    receipts.reset()
    identity.reset_consults()
    monkeypatch.setattr(adoption, "FLUSH_SECONDS", 0.0)
    yield
    adoption.reset()
    fieldguide.reset()
    receipts.reset()
    identity.reset_consults()


# Filled by _worker_db; read by fresh_db.
_BASELINE_TABLES: set[str] = set()


@pytest.fixture(scope="session")
def _worker_db(worker_id):
    """One migrated database per xdist worker, reused by every test in it.

    A database PER TEST would be correct and far too slow: CREATE DATABASE ...
    TEMPLATE costs ~100 ms against the ~0.3 ms file copy the SQLite fixture
    used, and 2000 of them is minutes. Per worker, migrations run once and
    fresh_db truncates between tests instead — which keeps real COMMIT
    semantics, unlike wrapping each test in a transaction that never commits
    (db.on_commit callbacks would then never fire).
    """
    import psycopg

    from app import config, db

    base = config.DATABASE_URL
    if not base:
        pytest.exit(
            "SKEIN_DATABASE_URL is not set. Start a server with:\n"
            "  docker run -d --name skein-db -p 5432:5432 -e POSTGRES_USER=skein"
            " -e POSTGRES_PASSWORD=skein -e POSTGRES_DB=skein postgres:17-alpine",
            returncode=1,
        )
    # The PID is part of the name: two pytest sessions against one server
    # would otherwise share it, and the DROP below would take the other
    # run's database out from under it mid-suite.
    name = f"skein_test_{worker_id}_{os.getpid()}"
    # autocommit: CREATE/DROP DATABASE cannot run inside a transaction block.
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}"')
    url = base.rsplit("/", 1)[0] + f"/{name}"
    config.DATABASE_URL, config.DATABASE_ERROR = url, ""
    # The ENV too, not just the module attribute: several tests reload
    # app.config to exercise a boot-time fault, and a reload re-reads
    # SKEIN_DATABASE_URL — pointing the worker back at the developer's own
    # database, where init_db then re-applies the baseline over a live schema.
    os.environ["SKEIN_DATABASE_URL"] = url
    db.close_pool()
    db.init_db()
    # The exact shape init_db produces. fresh_db drops anything a test adds on
    # top, so a test that creates a table (scope's `probe`) cannot collide with
    # the next test in the same worker — under SQLite each test had its own
    # file and could not.
    _BASELINE_TABLES.clear()
    _BASELINE_TABLES.update(
        r["t"]
        for r in db.query(
            "SELECT table_name AS t FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
    )
    yield url
    db.close_pool()
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch, _worker_db):
    from app import config, db

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    # SESSIONS_DIR is derived from DATA_DIR at import — left unpatched, session
    # files persist across tests within a worker while the DB resets, and a
    # test that restores a reused thread id reads a previous test's session
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    # TRUNCATE every table in one statement, so foreign keys never order it and
    # one round trip covers the reset. RESTART IDENTITY because tests assert on
    # ids ("task #1"), which a continuing sequence would break on the second
    # test in a worker.
    tables = db.query(
        "SELECT quote_ident(table_schema) || '.' || quote_ident(table_name) AS t"
        " FROM information_schema.tables"
        " WHERE table_schema NOT IN ('pg_catalog', 'information_schema')"
        "   AND table_type = 'BASE TABLE' AND table_name != 'schema_version'"
    )
    if tables:
        names = ", ".join(r["t"] for r in tables)
        db.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    extra = [
        r["t"]
        for r in db.query(
            "SELECT quote_ident(table_name) AS t, table_name AS raw"
            " FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
        )
        if r["t"].strip('"') not in _BASELINE_TABLES
    ]
    for name in extra:
        db.execute(f"DROP TABLE IF EXISTS {name} CASCADE")
    # Extension and private schemas are DROPPED, not truncated: their tables
    # are created by the test itself (store.migrate, private_notes._ready), so
    # truncating rows leaves the table behind and the next test's CREATE hits
    # "already exists". Dropping returns the database to the shape init_db
    # left it in.
    owned = db.query(
        "SELECT quote_ident(schema_name) AS s FROM information_schema.schemata"
        " WHERE schema_name LIKE 'ext\\_%' OR schema_name = ?",
        (config.PRIVATE_SCHEMA,),
    )
    for row in owned:
        db.execute(f"DROP SCHEMA IF EXISTS {row['s']} CASCADE")
    from app.services import private_notes

    private_notes._schema_ready = False
    yield db


@pytest.fixture()
def scratch_db(worker_id, _worker_db, monkeypatch):
    """A throwaway database of this test's own, for tests that MUTATE schema.

    fresh_db shares one database per worker and only truncates rows, so a test
    that deletes a schema_version row or applies a staged migration corrupts
    every test that follows it in that worker. Under SQLite each test had its
    own file and this class of test was free.
    """
    import psycopg

    from app import config, db

    base = _worker_db.rsplit("/", 1)[0]
    name = f"skein_scratch_{worker_id}_{os.getpid()}_{abs(hash(monkeypatch)) % 10**6}"
    with psycopg.connect(_worker_db, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{name}"')
    previous = config.DATABASE_URL
    config.DATABASE_URL = f"{base}/{name}"
    os.environ["SKEIN_DATABASE_URL"] = config.DATABASE_URL  # reloads, as in _worker_db
    db.close_pool()
    try:
        # INSIDE the try: a failure here would otherwise leave config and the
        # environment pointed at a scratch database that is never dropped, and
        # every later test in this worker would run against it.
        db.init_db()
        yield db
    finally:
        db.close_pool()
        config.DATABASE_URL = previous
        os.environ["SKEIN_DATABASE_URL"] = previous
        with psycopg.connect(_worker_db, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture()
def client(fresh_db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, headers={"X-User": "tester"}) as c:
        yield c


def _strong(client=None, name="tester"):
    from app.services.api_keys import create_key

    return {"Authorization": f"Bearer {create_key(name, 'r')['key']}"}


def _unread_for(fresh_db, user, like):
    return fresh_db.query_one(
        'SELECT * FROM notifications WHERE "user" = ? AND message LIKE ? AND read_at IS NULL',
        (user, like),
    )


def _delegated_task(fresh_db, title="probe"):
    from app.services import delegation, users, work

    users.ensure_user("mira")
    users.ensure_user("scout", kind="agent")
    t = work.create_task(title=title, actor="mira")
    delegation.delegate_task(t["id"], "scout", "mira", actor="mira")
    return t["id"]


def _ago(days: float) -> str:
    from datetime import datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
