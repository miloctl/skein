"""The self-growing eval corpus: what counts as a label and what stays unscored free text."""


def test_eval_capture_freetext_correction_is_unscored(client):
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
    out = client.get("/api/eval/capture").json()
    assert out["cases"] == 1 and out["passed"] == 1
    assert len(out["unscored"]) == 1


def test_feedback_and_eval_capture(client):
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
            "correction": "commitment",
        },
    )
    out = client.get("/api/eval/capture").json()
    assert out["cases"] == 2 and out["passed"] == 1
    assert out["mismatches"][0]["expected"] == "commitment"

    r = client.post(
        "/api/feedback", json={"kind": "capture", "input_text": "x", "verdict": "corrected"}
    )
    assert r.status_code == 400  # corrected needs the correction
