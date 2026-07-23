import os
import tempfile

os.environ["STRANDS_DATA_DIR"] = tempfile.mkdtemp(prefix="strands-test-")
os.environ["STRANDS_SCHEDULER"] = "0"
os.environ["STRANDS_MODEL_PROVIDER"] = "mock"
os.environ["STRANDS_AGENT_REVIEW"] = "0"
os.environ["STRANDS_EMBEDDINGS"] = "0"

import pytest


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    from app import config, db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    db.init_db()
    return db


@pytest.fixture()
def client(fresh_db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app, headers={"X-User": "tester"}) as c:
        yield c
