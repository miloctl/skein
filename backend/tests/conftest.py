import os
import tempfile

os.environ["SKEIN_DATA_DIR"] = tempfile.mkdtemp(prefix="skein-test-")
os.environ["SKEIN_SCHEDULER"] = "0"
os.environ["SKEIN_MODEL_PROVIDER"] = "mock"
os.environ["SKEIN_AGENT_REVIEW"] = "0"
os.environ["SKEIN_EMBEDDINGS"] = "0"

import pytest


@pytest.fixture(autouse=True)
def _reset_ratelimit():
    from app import ratelimit

    ratelimit.reset()
    yield
    ratelimit.reset()


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    from app import config, db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "PRIVATE_DB_PATH", tmp_path / "private.db")
    db.init_db()
    return db


@pytest.fixture()
def client(fresh_db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, headers={"X-User": "tester"}) as c:
        yield c
