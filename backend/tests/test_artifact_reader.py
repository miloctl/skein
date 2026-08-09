def test_an_unreadable_artifact_answers_json_not_plain_text(client):
    """The 500 class is right — this is our own state. The shape was not:
    with no handler Starlette answers a bare `Internal Server Error` in
    text/plain, and the operator instruction inside the message reached
    nobody. An error response is always JSON."""
    from pathlib import Path

    from app import db
    from app.services import rituals

    rituals.week_open(actor="ava", force=True)
    aid = db.query_one("SELECT id FROM artifacts ORDER BY id DESC")["id"]
    # INSIDE the artifacts root: a path outside it is caught by the
    # containment check and is a 404, which is a different rule
    from app import config

    gone = str(Path(config.DATA_DIR) / "artifacts" / "gone.md")
    db.execute("UPDATE artifacts SET path = ? WHERE id = ?", (gone, aid))
    r = client.get(f"/api/artifacts/{aid}")
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    # the operator instruction is the whole point — a body with a `detail`
    # key and none of the sentence would pass a shape check and help nobody
    assert "data/artifacts is mounted" in r.json()["detail"]


def test_an_unclassified_failure_is_json_and_says_nothing_about_itself():
    """The JSON rule held for the handled classes and nothing else — a
    KeyError or a bad-SQL OperationalError answered `Internal Server Error`
    in text/plain, and lib/api.ts fell back to the status line.

    The handler is exercised directly: TestClient re-raises a server
    exception rather than returning the handler's response, and a second
    client with that turned off starts its own lifespan on another thread and
    collides with this one's SQLite connection.
    """
    import asyncio
    import json as jsonlib

    from app.main import app, unhandled_error_handler

    assert Exception in app.exception_handlers, "the catch-all is not registered"

    resp = asyncio.run(unhandled_error_handler(None, KeyError("SKEIN_MODEL_API_KEY")))
    assert resp.status_code == 500
    assert resp.media_type == "application/json"
    # nothing from the exception: unclassified text is as likely to be a
    # filesystem path or a library's internals as anything a reader can use
    assert b"SKEIN_MODEL_API_KEY" not in resp.body
    assert "server log" in jsonlib.loads(resp.body)["detail"]
