"""The self-growing eval corpus: what counts as a label and what stays unscored free text."""

from conftest import _strong


def test_eval_capture_freetext_correction_is_unscored(client, monkeypatch):
    from app import config

    # the replay is for administrators named in SKEIN_ADMINS (routes/api.py)
    monkeypatch.setattr(config, "ADMINS", frozenset({"tester"}))
    client.post(
        "/api/feedback",
        json={
            "kind": "capture",
            "input_text": "decision: x — review by 2026-10-01",
            "output": "decision",
            "verdict": "corrected",
            "correction": "review_by should have been parsed",
        },
    )
    client.post(
        "/api/feedback",
        json={
            "kind": "capture",
            "input_text": "todo: ship it",
            "output": "task",
            "verdict": "up",
        },
    )
    out = client.get("/api/eval/capture", headers=_strong(client)).json()
    assert out["cases"] == 1 and out["passed"] == 1
    assert len(out["unscored"]) == 1


def test_feedback_and_eval_capture(client, monkeypatch):
    from app import config

    # the replay is for administrators named in SKEIN_ADMINS (routes/api.py)
    monkeypatch.setattr(config, "ADMINS", frozenset({"tester"}))
    # correct classification, thumbs up
    client.post(
        "/api/feedback",
        json={"kind": "capture", "input_text": "todo: ship it", "output": "task", "verdict": "up"},
    )
    # a case the rules get wrong today
    client.post(
        "/api/feedback",
        json={
            "kind": "capture",
            "input_text": "remember we owe legal a summary",
            "output": "note",
            "verdict": "corrected",
            "correction": "promise",
        },
    )
    out = client.get("/api/eval/capture", headers=_strong(client)).json()
    assert out["cases"] == 2 and out["passed"] == 1
    assert out["mismatches"][0]["expected"] == "promise"

    r = client.post(
        "/api/feedback", json={"kind": "capture", "input_text": "x", "verdict": "corrected"}
    )
    assert r.status_code == 400  # corrected needs the correction


def test_feedback_reaches_its_author_and_administrators_only(client, fresh_db, monkeypatch):
    """A feedback row stores the chat input and the output it judged, and
    GET /api/feedback served every teammate's rows to everyone."""
    from app import config

    monkeypatch.setattr(config, "ADMINS", frozenset({"ops"}))
    posted = client.post(
        "/api/feedback",
        json={"kind": "chat", "input_text": "ZZMYCHATZZ", "output": "o", "verdict": "down"},
        headers={"X-User": "alice"},
    ).json()
    assert "ZZMYCHATZZ" not in client.get("/api/feedback", headers={"X-User": "bob"}).text
    assert "ZZMYCHATZZ" in client.get("/api/feedback", headers={"X-User": "alice"}).text
    assert "ZZMYCHATZZ" in client.get("/api/feedback", headers=_strong(client, "ops")).text
    assert client.get("/api/eval/capture", headers=_strong(client, "bob")).status_code == 403
    assert (
        client.delete(f"/api/feedback/{posted['id']}", headers={"X-User": "bob"}).status_code == 404
    )
    assert (
        client.delete(f"/api/feedback/{posted['id']}", headers={"X-User": "alice"}).status_code
        == 200
    )
    assert fresh_db.query("SELECT * FROM feedback") == []


def test_the_fallback_administrator_reads_no_teammates_feedback(client, fresh_db, monkeypatch):
    """With SKEIN_ADMINS unset, trusted-header makes every key holder an
    administrator, and each of them read every teammate's chat excerpts."""
    from app import config

    monkeypatch.setattr(config, "ADMINS", frozenset())
    client.post(
        "/api/feedback",
        json={"kind": "chat", "input_text": "ZZEXCERPTZZ", "output": "o", "verdict": "down"},
        headers={"X-User": "alice"},
    )
    keyed = _strong(client, "ops")
    assert "ZZEXCERPTZZ" not in client.get("/api/feedback", headers=keyed).text
    assert client.get("/api/eval/capture", headers=keyed).status_code == 403
