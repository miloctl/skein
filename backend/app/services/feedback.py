"""Eval corpus: thumbs and corrections on platform output become labeled
examples. `eval_capture` replays the capture classifier against its own
correction history — run it before changing the rules (or a model prompt)."""

from .. import db

KINDS = ("chat", "capture", "proposal", "finding")
VERDICTS = ("up", "down", "corrected")


def record_feedback(kind: str, input_text: str, output: str = "",
                    verdict: str = "up", correction: str = "",
                    *, actor: str = "system") -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    if not input_text.strip():
        raise ValueError("input_text is required")
    if verdict == "corrected" and not correction.strip():
        raise ValueError("a corrected verdict needs the correction")
    fid = db.execute(
        "INSERT INTO feedback (kind, input, output, verdict, correction,"
        " created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kind, input_text, output, verdict, correction, actor, db.now()),
    )
    db.log_activity(actor, "record_feedback", f"#{fid} {kind}/{verdict}")
    return {"id": fid, "kind": kind, "verdict": verdict}


def list_feedback(kind: str = "") -> list[dict]:
    if kind:
        return db.query(
            "SELECT * FROM feedback WHERE kind = ? ORDER BY id DESC LIMIT 100", (kind,))
    return db.query("SELECT * FROM feedback ORDER BY id DESC LIMIT 100")


def eval_capture() -> dict:
    """Replay the rule-based classifier over the labeled capture corpus.
    'up' rows assert the recorded output was right; 'corrected' rows carry
    the right answer. Deterministic, keyless, run-anywhere."""
    from .capture import classify

    rows = db.query(
        "SELECT * FROM feedback WHERE kind = 'capture'"
        " AND (verdict = 'up' OR verdict = 'corrected') ORDER BY id"
    )
    results, mismatches = [], []
    for r in rows:
        expected = r["correction"] if r["verdict"] == "corrected" else r["output"]
        predicted = classify(r["input"])
        ok = predicted == expected
        results.append(ok)
        if not ok:
            mismatches.append({"id": r["id"], "input": r["input"],
                               "expected": expected, "predicted": predicted})
    n = len(results)
    return {"cases": n,
            "passed": sum(results),
            "accuracy": round(sum(results) / n, 3) if n else None,
            "mismatches": mismatches}
