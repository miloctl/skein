"""The activity ledger is tamper-evident: every chained row commits to its own
content and to the row before it. These tests are the whole point of the
feature — if they pass while the chain is decorative, the feature is a lie."""

import threading

import pytest

from app import db
from app.services import activity


def _log(n: int, actor: str = "tester") -> None:
    for i in range(n):
        db.log_activity(actor, "test_action", f"#{i}")


def test_chain_verifies_after_appends(fresh_db):
    _log(5)
    result = activity.verify_chain()
    assert result["ok"]
    assert result["entries"] == 5
    assert result["chained_from"] == 1
    assert result["chained_through"] == 5
    assert result["broken_at"] is None


def test_first_row_chains_to_genesis(fresh_db):
    _log(1)
    row = db.query_row("SELECT seq, prev_hash FROM activity WHERE seq = 1")
    assert row["seq"] == 1
    assert row["prev_hash"] is None
    assert activity.verify_chain()["ok"]


@pytest.mark.parametrize(
    "edit",
    [
        "UPDATE activity SET detail = 'tampered' WHERE seq = 2",
        "UPDATE activity SET actor = 'tampered' WHERE seq = 2",
        "UPDATE activity SET action = 'tampered' WHERE seq = 2",
        "UPDATE activity SET created_at = 'tampered' WHERE seq = 2",
    ],
)
def test_edited_row_breaks_the_chain(fresh_db, edit):
    _log(4)
    db.execute(edit)
    result = activity.verify_chain()
    assert not result["ok"]
    assert result["broken_at"] == 2
    assert result["reason"] == "row content does not match its digest"


def test_deleted_row_breaks_the_chain(fresh_db):
    _log(4)
    db.execute("DELETE FROM activity WHERE seq = 2")
    result = activity.verify_chain()
    assert not result["ok"]
    assert result["broken_at"] == 2


def test_rewritten_row_with_recomputed_digest_still_breaks(fresh_db):
    """The attacker who knows the hash function still cannot fix ONE row: its
    digest is the next row's prev_hash, so the break just moves downstream."""
    _log(4)
    row = db.query_row("SELECT * FROM activity WHERE seq = 2")
    forged = db.activity_hash(2, row["created_at"], row["actor"], "forged", "x", row["prev_hash"])
    db.execute(
        "UPDATE activity SET action = 'forged', detail = 'x', hash = ? WHERE seq = 2", (forged,)
    )
    result = activity.verify_chain()
    assert not result["ok"]
    assert result["broken_at"] == 3
    assert result["reason"] == "prev_hash does not match the row before"


def test_pre_migration_rows_count_as_unchained_never_verified(fresh_db):
    db.execute(
        "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
        ("old", "legacy_action", "", db.now()),
    )
    _log(2)
    result = activity.verify_chain()
    assert result["ok"]
    assert result["unchained_rows"] == 1
    assert result["entries"] == 2


def test_appends_inside_a_transaction_chain(fresh_db):
    with db.transaction():
        _log(3)
    assert activity.verify_chain()["ok"]
    assert db.query_row("SELECT COUNT(*) AS n FROM activity WHERE seq IS NOT NULL")["n"] == 3


def test_concurrent_appends_never_fork_or_drop(fresh_db):
    """The expected count is hard-coded on purpose: asserting contiguity
    against len(rows) would pass just as happily if a third of the appends had
    fallen through to the unchained path."""
    threads = [threading.Thread(target=_log, args=(6,)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = db.query("SELECT seq FROM activity WHERE seq IS NOT NULL ORDER BY seq")
    assert [r["seq"] for r in rows] == list(range(1, 25))
    assert db.query_row("SELECT COUNT(*) AS n FROM activity")["n"] == 24
    assert activity.verify_chain()["ok"]


def test_tail_truncation_is_caught(fresh_db):
    """The most likely attack: delete the rows recording what you just did.
    A shorter chain is internally perfect, so only the high-water mark sees it."""
    _log(10)
    assert activity.verify_chain()["ok"]  # records the high-water mark
    db.execute("DELETE FROM activity WHERE seq >= 8")
    result = activity.verify_chain()
    assert not result["ok"]
    assert result["broken_at"] == 8
    assert "tail was removed" in result["reason"]


def test_tail_truncation_then_legitimate_appends_is_caught(fresh_db):
    """Appending after a truncation re-uses the freed seqs and re-links
    cleanly. Length alone is what gives it away, so the mark is a high-water
    mark and not a last-seen one."""
    _log(10)
    assert activity.verify_chain()["ok"]
    db.execute("DELETE FROM activity WHERE seq >= 8")
    _log(2)
    result = activity.verify_chain()
    assert not result["ok"]
    assert "tail was removed" in result["reason"]


def test_head_truncation_with_a_reroot_is_caught(fresh_db):
    """The digest is unkeyed, so an attacker can delete the head and re-root
    the survivor by NULLing its prev_hash and recomputing forward. Genesis is
    only legal at seq 1."""
    _log(6)
    db.execute("DELETE FROM activity WHERE seq <= 3")
    prev = db.GENESIS_PREV
    for row in db.query("SELECT * FROM activity ORDER BY seq"):
        digest = db.activity_hash(
            row["seq"], row["created_at"], row["actor"], row["action"], row["detail"], prev
        )
        db.execute(
            "UPDATE activity SET hash = ?, prev_hash = ? WHERE seq = ?",
            (digest, None if prev == db.GENESIS_PREV else prev, row["seq"]),
        )
        prev = digest
    result = activity.verify_chain()
    assert not result["ok"]
    assert result["broken_at"] == 4
    assert "head was removed" in result["reason"]


def test_a_full_reforge_is_caught_by_the_stored_anchor(fresh_db):
    """Recomputing the whole chain after an edit produces a valid chain. The
    anchor is the out-of-chain state that contradicts it — which is why the
    full walk cross-checks it instead of trusting the links alone."""
    _log(6)
    activity.verify_tail()  # blesses seq 6
    rows = db.query("SELECT * FROM activity ORDER BY seq")
    db.execute("UPDATE activity SET actor = 'someone-else' WHERE seq = 2")
    prev = db.GENESIS_PREV
    for row in rows:
        actor = "someone-else" if row["seq"] == 2 else row["actor"]
        digest = db.activity_hash(
            row["seq"], row["created_at"], actor, row["action"], row["detail"], prev
        )
        db.execute(
            "UPDATE activity SET hash = ?, prev_hash = ? WHERE seq = ?",
            (digest, None if prev == db.GENESIS_PREV else prev, row["seq"]),
        )
        prev = digest
    result = activity.verify_chain()
    assert not result["ok"]
    assert result["reason"] == "the stored anchor does not match this row"


def test_rows_written_outside_the_chain_are_caught(fresh_db):
    """An unchained row is structurally exempt from every link check. Only a
    count against the recorded baseline notices it."""
    _log(3)
    assert activity.verify_chain()["ok"]  # records the legacy baseline (0)
    db.execute(
        "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
        ("ghost", "smuggled", "", db.now()),
    )
    result = activity.verify_chain()
    assert not result["ok"]
    assert "outside the chain" in result["reason"]


def test_the_legacy_baseline_is_not_treated_as_tampering(fresh_db):
    """Pre-036 rows already present at the first run are the baseline, not a
    break — otherwise every existing deployment alarms on upgrade."""
    for _ in range(3):
        db.execute(
            "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
            ("old", "legacy", "", db.now()),
        )
    _log(2)
    result = activity.verify_chain()
    assert result["ok"]
    assert result["unchained_rows"] == 3
    assert result["unchained_baseline"] == 3


def test_the_feed_orders_by_seq_not_rowid(fresh_db):
    """`id` is outside the digest, so ordering the feed by it would let the
    visible timeline be resequenced while verification still reports intact."""
    from app.services import collab

    _log(3)
    db.execute("UPDATE activity SET id = 500 WHERE seq = 1")
    assert [r["seq"] for r in collab.recent_activity()] == [3, 2, 1]
    assert activity.verify_chain()["ok"]


def test_verify_tail_advances_the_anchor_and_holds_it_on_a_break(fresh_db):
    _log(3)
    assert activity.verify_tail()["ok"]
    assert activity._anchor()[0] == 3

    _log(2)
    result = activity.verify_tail()
    assert result["ok"]
    assert result["entries"] == 2  # only the new rows were walked
    assert activity._anchor()[0] == 5

    db.execute("UPDATE activity SET detail = 'tampered' WHERE seq = 5")
    assert not activity.verify_tail()["ok"]  # the anchor row itself is re-derived
    assert activity._anchor()[0] == 5  # a break never advances the anchor
    assert not activity.verify_tail()["ok"]  # and it keeps re-reporting


def test_verify_tail_cannot_see_behind_its_anchor_but_a_full_walk_can(fresh_db):
    """The honest limit of incremental verification, pinned so nobody
    "optimizes" the findings rule onto the cheap path."""
    _log(5)
    assert activity.verify_tail()["ok"]
    db.execute("UPDATE activity SET detail = 'tampered' WHERE seq = 2")
    assert activity.verify_tail()["ok"]
    assert not activity.verify_chain()["ok"]


def test_incremental_verify_detects_tampering_inside_the_anchor_row(fresh_db):
    """A stored anchor hash is what makes this detectable: recomputing from the
    anchor row alone would trust the row being attacked."""
    _log(3)
    activity.verify_tail()
    _log(1)
    row = db.query_row("SELECT * FROM activity WHERE seq = 3")
    forged = db.activity_hash(3, row["created_at"], row["actor"], "forged", "x", row["prev_hash"])
    db.execute(
        "UPDATE activity SET action = 'forged', detail = 'x', hash = ? WHERE seq = 3", (forged,)
    )
    assert not activity.verify_tail()["ok"]


def test_chain_health_reports_the_uncovered_tail(fresh_db):
    _log(2)
    activity.verify_tail()
    _log(3)
    health = activity.chain_health()
    assert health["verified_through"] == 2
    assert health["latest"] == 5
    assert health["unverified"] == 3


def test_chain_health_goes_negative_on_truncation(fresh_db):
    """A clamp to zero here would render truncation as 'fully verified'."""
    _log(5)
    activity.verify_tail()
    db.execute("DELETE FROM activity WHERE seq >= 4")
    health = activity.chain_health()
    assert health["unverified"] == -2
    assert health["high_water"] == 5


def test_the_anchor_is_written_atomically(fresh_db):
    """Split across two autocommits, a crash between them would point a live
    seq at a stale hash and wedge verify_tail on a false break forever."""
    _log(3)
    activity.verify_tail()
    seq, digest = activity._anchor()
    assert seq == 3
    assert digest == db.query_row("SELECT hash FROM activity WHERE seq = 3")["hash"]


def test_verify_endpoint(client):
    client.post("/api/notes", json={"topic": "t", "content": "c"})
    body = client.get("/api/activity/verify").json()
    assert body["ok"]
    assert body["entries"] >= 1
    assert client.get("/api/activity/verify?tail=1").json()["ok"]


def test_health_carries_the_chain_block(client):
    assert "activity_chain" in client.get("/health").json()


def test_findings_rule_fires_on_a_break(fresh_db):
    from app.services import insights

    _log(3)
    assert insights._r_activity_chain() == []
    db.execute("UPDATE activity SET detail = 'tampered' WHERE seq = 2")
    fired = insights._r_activity_chain()
    assert len(fired) == 1
    assert fired[0]["rule_id"] == "activity_chain_broken"
    assert fired[0]["severity"] == "high"
    assert fired[0]["receipt"]["broken_at"] == 2
