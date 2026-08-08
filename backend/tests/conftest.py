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


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    from app import config, db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PRIVATE_DB_PATH", tmp_path / "private.db")
    # SESSIONS_DIR is derived from DATA_DIR at import — left unpatched, session
    # files persist across tests within a worker while the DB resets, and a
    # test that restores a reused thread id reads a previous test's session
    monkeypatch.setattr(config, "SESSIONS_DIR", tmp_path / "sessions")
    db.init_db()
    return db


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
        "SELECT * FROM notifications WHERE user = ? AND message LIKE ? AND read_at IS NULL",
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
