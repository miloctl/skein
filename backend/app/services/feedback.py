"""Eval corpus: thumbs and corrections on platform output become labeled
examples. `eval_capture` replays the capture classifier against its own
correction history — run it before changing the rules (or a model prompt)."""

from .. import db

# pulse: the weekly one-question check — "did Skein reduce coordination
# effort this week?" (up/down + optional note in correction). Team-aggregated
# only; never displayed per person.
KINDS = ("chat", "capture", "proposal", "finding", "pulse")
VERDICTS = ("up", "down", "corrected")


def record_feedback(
    kind: str,
    input_text: str,
    output: str = "",
    verdict: str = "up",
    correction: str = "",
    *,
    actor: str = "system",
) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    if not input_text.strip():
        raise ValueError("input_text is required")
    if verdict == "corrected" and not correction.strip():
        raise ValueError("a corrected verdict needs the correction")
    # pulse votes are DESIGNED anonymous: no created_by, and the ledger gets
    # neither actor nor verdict (on a 6-person team, actor-without-verdict is
    # still deanonymizable against the tally). A documented narrowing of the
    # provenance norm — the vote is honest only if it can't be attributed.
    stored_by = "" if kind == "pulse" else actor
    fid = db.execute(
        "INSERT INTO feedback (kind, input, output, verdict, correction,"
        " created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kind, input_text, output, verdict, correction, stored_by, db.now()),
    )
    if kind == "pulse":
        db.log_activity("team", "record_feedback", f"#{fid} pulse")
    else:
        db.log_activity(actor, "record_feedback", f"#{fid} {kind}/{verdict}")
    return {"id": fid, "kind": kind, "verdict": verdict}


_COLS = "id, kind, input, output, verdict, correction, created_at"  # never created_by


def list_feedback(kind: str = "") -> list[dict]:
    if kind:
        return db.query(
            f"SELECT {_COLS} FROM feedback WHERE kind = ? ORDER BY id DESC LIMIT 100",  # noqa: S608
            (kind,),
        )
    return db.query(f"SELECT {_COLS} FROM feedback ORDER BY id DESC LIMIT 100")  # noqa: S608


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
            mismatches.append(
                {"id": r["id"], "input": r["input"], "expected": expected, "predicted": predicted}
            )
    n = len(results)
    return {
        "cases": n,
        "passed": sum(results),
        "accuracy": round(sum(results) / n, 3) if n else None,
        "mismatches": mismatches,
    }


def pulse_tally(weeks: int = 8) -> list[dict]:
    """Team-aggregated weekly pulse: up = Skein reduced coordination effort,
    down = it added effort. Counts only — never who said what (the one burden
    signal telemetry can't measure honestly if people feel watched)."""
    rows = db.query(
        "SELECT substr(created_at, 1, 10) AS day, verdict FROM feedback"
        " WHERE kind = 'pulse' ORDER BY id DESC LIMIT 500"
    )
    from datetime import date

    buckets: dict[str, dict] = {}
    for r in rows:
        iso = date.fromisoformat(r["day"]).isocalendar()
        wk = f"{iso.year}-W{iso.week:02d}"
        b = buckets.setdefault(wk, {"week": wk, "up": 0, "down": 0})
        if r["verdict"] in ("up", "down"):
            b[r["verdict"]] += 1
    return sorted(buckets.values(), key=lambda b: b["week"], reverse=True)[:weeks]
