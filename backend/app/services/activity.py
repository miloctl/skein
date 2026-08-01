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

WHAT THIS CATCHES, and what it does not. The digest is unkeyed and every input
to it is stored in the row it protects, so anyone who can write platform.db can
recompute the whole chain. Three marks in app_settings raise that cost — the
last verified anchor, a monotonic high-water seq, and the pre-036 unchained
baseline — but they live in the same file. This detects corruption, partial
writes, and hand edits. It does not defeat an attacker who read this module and
is willing to rewrite app_settings too. Making that claim true needs an anchor
stored outside the database, which is not built.
"""

from .. import db

ANCHOR_SEQ = "activity_chain_seq"
ANCHOR_HASH = "activity_chain_hash"
HIGH_SEQ = "activity_chain_high_seq"
LEGACY_UNCHAINED = "activity_chain_legacy"


def _settings(*keys: str) -> dict[str, str]:
    marks = ", ".join("?" for _ in keys)
    rows = db.query(f"SELECT key, value FROM app_settings WHERE key IN ({marks})", keys)  # noqa: S608
    return {r["key"]: r["value"] for r in rows}


def _int_setting(got: dict[str, str], key: str) -> int:
    try:
        return int(got.get(key, "0"))
    except ValueError:
        return 0


def _anchor() -> tuple[int, str]:
    got = _settings(ANCHOR_SEQ, ANCHOR_HASH)
    return _int_setting(got, ANCHOR_SEQ), got.get(ANCHOR_HASH, "")


def _put(pairs: dict[str, str]) -> None:
    with db.transaction():
        for key, value in pairs.items():
            db.execute(
                "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
                " updated_at = excluded.updated_at",
                (key, value, db.now()),
            )


def _set_anchor(seq: int, digest: str) -> None:
    """Both keys in ONE transaction. Written separately, a crash between them
    leaves a seq pointing at the wrong hash, and since a break never advances
    the anchor, verify_tail would report that false break forever."""
    _put({ANCHOR_SEQ: str(seq), ANCHOR_HASH: digest})


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

    A link walk alone proves too little. Three deletions leave a shorter chain
    that is internally perfect, so each is checked against state held outside
    the rows themselves:

    - Cutting the TAIL off. Caught by a monotonic high-water mark: the chain
      may never be shorter than the longest it has ever been verified at.
    - Cutting the HEAD off and re-rooting the survivor. Caught by requiring a
      full walk to start at seq 1, since a NULL prev_hash means genesis and
      genesis is only legal there.
    - Rewriting history and re-anchoring. A full walk cross-checks the stored
      anchor, so the newest blessed digest must still be reproducible.

    expected_prev is the digest the row AT since_seq must still produce. That
    row is re-derived from its own content on every run, because an
    incremental walk only looks forward and would never revisit the newest row
    it already blessed.
    """
    rows = db.query(
        "SELECT seq, hash, prev_hash, actor, action, detail, created_at FROM activity"
        " WHERE seq IS NOT NULL AND seq > ? ORDER BY seq ASC",
        (since_seq,),
    )
    unchained = db.query_row("SELECT COUNT(*) AS n FROM activity WHERE seq IS NULL")["n"]
    marks = _settings(HIGH_SEQ, LEGACY_UNCHAINED)
    high = _int_setting(marks, HIGH_SEQ)
    if LEGACY_UNCHAINED not in marks:  # first ever run — today's count IS the baseline
        _put({LEGACY_UNCHAINED: str(unchained)})
        marks[LEGACY_UNCHAINED] = str(unchained)
    legacy = _int_setting(marks, LEGACY_UNCHAINED)
    latest = db.query_row("SELECT COALESCE(MAX(seq), 0) AS seq FROM activity")["seq"]
    anchor_seq, anchor_hash = _anchor()

    out: dict = {
        "ok": True,
        "entries": len(rows),
        "chained_from": rows[0]["seq"] if rows else None,
        "chained_through": rows[-1]["seq"] if rows else None,
        "broken_at": None,
        "reason": "",
        "unchained_rows": unchained,
        "unchained_baseline": legacy,
    }

    if latest < high:
        out.update(
            ok=False,
            broken_at=latest + 1,
            reason=f"chain ends at {latest} but reached {high} before — the tail was removed",
        )
        return out
    if unchained > legacy:
        out.update(
            ok=False,
            reason=f"{unchained - legacy} row(s) were written to the ledger outside the chain",
        )
        return out

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
    elif rows and rows[0]["seq"] != 1:
        out.update(
            ok=False,
            broken_at=rows[0]["seq"],
            reason=f"chain starts at {rows[0]['seq']}, not 1 — the head was removed",
        )
        return out

    want = 1 if since_seq == 0 else since_seq + 1
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
        if since_seq == 0 and seq == anchor_seq and anchor_hash and digest != anchor_hash:
            out.update(ok=False, broken_at=seq, reason="the stored anchor does not match this row")
            return out
        prev = digest
        want += 1

    if since_seq == 0 and anchor_seq > latest:
        out.update(
            ok=False,
            broken_at=latest + 1,
            reason=f"the stored anchor points at seq {anchor_seq}, past the end of the chain",
        )
        return out
    if latest > high:
        _put({HIGH_SEQ: str(latest)})
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
    """Compact /health block — the stored anchor plus what is not yet covered.

    `unverified` is signed on purpose: a NEGATIVE value means the chain is
    shorter than what was already verified, which is truncation, not progress.
    Clamping it to zero here would hide the one state worth seeing.
    """
    seq, _ = _anchor()
    latest = db.query_row("SELECT COALESCE(MAX(seq), 0) AS seq FROM activity")["seq"]
    marks = _settings(HIGH_SEQ, LEGACY_UNCHAINED)
    unchained = db.query_row("SELECT COUNT(*) AS n FROM activity WHERE seq IS NULL")["n"]
    return {
        "verified_through": seq,
        "latest": latest,
        "unverified": latest - seq,
        "high_water": _int_setting(marks, HIGH_SEQ),
        "unchained_rows": unchained,
        "unchained_baseline": _int_setting(marks, LEGACY_UNCHAINED),
    }
