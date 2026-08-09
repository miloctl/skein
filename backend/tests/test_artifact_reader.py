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
    assert "detail" in r.json()
