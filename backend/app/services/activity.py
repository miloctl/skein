"""Provenance-ledger reads: verification of the tamper-evident chain over
`activity`.

Two verifications, on purpose:

- verify_tail() walks only what arrived since the last good anchor (stored in
  app_settings). Cheap, runs nightly, keeps /health honest about freshness.
- verify_chain() walks everything. An anchor is a claim about the past, so an
  incremental run can never notice an edit to a row it already passed. The
  daily findings rule therefore pays for the full walk — that is the run that
  answers "is the ledger intact", and it is the one an operator acts on.

Rows written before migration 036 carry no seq. They are counted as UNCHAINED
and never as verified: the chain covers what it covers, and saying otherwise
would be the dishonest version of this feature.
"""

from .. import db

ANCHOR_SEQ = "activity_chain_seq"
ANCHOR_HASH = "activity_chain_hash"


def _anchor() -> tuple[int, str]:
    rows = db.query(
        "SELECT key, value FROM app_settings WHERE key IN (?, ?)", (ANCHOR_SEQ, ANCHOR_HASH)
    )
    got = {r["key"]: r["value"] for r in rows}
    try:
        seq = int(got.get(ANCHOR_SEQ, "0"))
    except ValueError:
        seq = 0
    return seq, got.get(ANCHOR_HASH, "")


def _set_anchor(seq: int, digest: str) -> None:
    for key, value in ((ANCHOR_SEQ, str(seq)), (ANCHOR_HASH, digest)):
        db.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (key, value, db.now()),
        )


def _digest(row: dict) -> str:
    return db.activity_hash(
        row["seq"],
        row["created_at"],
        row["actor"],
        row["action"],
        row["detail"] or "",
        row["prev_hash"] or db.GENESIS_PREV,
    )


def verify_chain(since_seq: int = 0, expected_prev: str = "") -> dict:
    """Recompute every digest after since_seq and check it links backwards.

    expected_prev is the digest the row AT since_seq must still produce — pass
    a value the caller verified earlier. That row is re-derived from its own
    stored content on every run, because an incremental walk that only looks
    forward would never revisit the newest verified row and would miss an edit
    to it. Verifying from 0 needs no anchor and recomputes everything.
    """
    rows = db.query(
        "SELECT seq, hash, prev_hash, actor, action, detail, created_at FROM activity"
        " WHERE seq IS NOT NULL AND seq > ? ORDER BY seq ASC",
        (since_seq,),
    )
    unchained = db.query_row("SELECT COUNT(*) AS n FROM activity WHERE seq IS NULL")["n"]
    out: dict = {
        "ok": True,
        "entries": len(rows),
        "chained_from": rows[0]["seq"] if rows else None,
        "chained_through": rows[-1]["seq"] if rows else None,
        "broken_at": None,
        "reason": "",
        "unchained_rows": unchained,
    }

    prev = db.GENESIS_PREV
    if since_seq > 0:
        anchor = db.query_one(
            "SELECT seq, hash, prev_hash, actor, action, detail, created_at FROM activity"
            " WHERE seq = ?",
            (since_seq,),
        )
        if anchor is None:
            out.update(ok=False, broken_at=since_seq, reason="anchor row is missing")
            return out
        prev = _digest(anchor)
        if prev != (expected_prev or anchor["hash"]):
            out.update(
                ok=False, broken_at=since_seq, reason="row content does not match its digest"
            )
            return out
    if not rows:
        return out

    want = rows[0]["seq"] if since_seq == 0 else since_seq + 1
    for row in rows:
        seq = row["seq"]
        if seq != want:
            out.update(ok=False, broken_at=want, reason=f"missing seq {want}")
            return out
        if (row["prev_hash"] or db.GENESIS_PREV) != prev:
            out.update(ok=False, broken_at=seq, reason="prev_hash does not match the row before")
            return out
        digest = _digest(row)
        if digest != row["hash"]:
            out.update(ok=False, broken_at=seq, reason="row content does not match its digest")
            return out
        prev = digest
        want += 1
    return out


def verify_tail() -> dict:
    """Verify what arrived since the last good anchor, then move the anchor.

    A break leaves the anchor where it was, so every later run re-reports it
    until a human deals with it. Silence after a break would be the worst
    possible outcome for an audit trail.
    """
    seq, digest = _anchor()
    result = verify_chain(seq, expected_prev=digest)
    if result["ok"] and result["chained_through"]:
        last = db.query_row("SELECT hash FROM activity WHERE seq = ?", (result["chained_through"],))
        _set_anchor(result["chained_through"], last["hash"])
    return result


def chain_health() -> dict:
    """Compact /health block — the stored anchor plus what is not yet covered."""
    seq, _ = _anchor()
    tail = db.query_one("SELECT MAX(seq) AS seq FROM activity WHERE seq IS NOT NULL")
    latest = (tail or {}).get("seq") or 0
    return {"verified_through": seq, "latest": latest, "unverified": max(0, latest - seq)}
