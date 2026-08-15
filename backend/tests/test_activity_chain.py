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
    from app.services import collab, users

    users.ensure_user("tester")
    _log(3)
    # Give seq 1 the HIGHEST id, so id order and seq order disagree. It cannot
    # be done with an UPDATE any more — `id` is GENERATED ALWAYS, which is the
    # schema refusing the resequencing this test used to perform — so the row
    # is rewritten verbatim and takes a fresh id on the way back in. Ordering
    # by id would now put seq 1 first.
    row = db.query_row("SELECT * FROM activity WHERE seq = 1")
    db.execute("DELETE FROM activity WHERE seq = 1")
    db.execute(
        "INSERT INTO activity (actor, action, detail, created_at, seq, hash, prev_hash)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            row["actor"],
            row["action"],
            row["detail"],
            row["created_at"],
            row["seq"],
            row["hash"],
            row["prev_hash"],
        ),
    )
    assert (
        db.query_row("SELECT MAX(id) AS m FROM activity")["m"]
        == db.query_row("SELECT id AS m FROM activity WHERE seq = 1")["m"]
    ), "seq 1 must hold the newest id for this test to mean anything"
    assert [r["seq"] for r in collab.recent_activity("tester")] == [3, 2, 1]
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


# ---- the anchor log (the off-box half) --------------------------------------


def _reforge_everything(edit_seq: int, new_actor: str) -> None:
    """What an attacker who read services/activity.py does: edit a row,
    recompute the whole chain, and rewrite every in-DB mark to match."""
    from app.services import activity

    rows = db.query("SELECT * FROM activity WHERE seq IS NOT NULL ORDER BY seq")
    prev = db.GENESIS_PREV
    for row in rows:
        actor = new_actor if row["seq"] == edit_seq else row["actor"]
        digest = db.activity_hash(
            row["seq"], row["created_at"], actor, row["action"], row["detail"], prev
        )
        db.execute(
            "UPDATE activity SET actor = ?, hash = ?, prev_hash = ? WHERE seq = ?",
            (actor, digest, None if prev == db.GENESIS_PREV else prev, row["seq"]),
        )
        prev = digest
    activity._set_anchor(rows[-1]["seq"], prev)


def test_nightly_verify_appends_one_anchor_line_per_run(fresh_db):
    from app.services import activity

    _log(3)
    result = activity.nightly_verify()
    assert result["ok"]
    assert result["anchor"]["anchored"] == 3
    _log(2)
    activity.nightly_verify()
    path = activity._anchor_log_paths()[0]
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    tip = db.query_row("SELECT hash FROM activity WHERE seq = 3")["hash"]
    assert f"hash={tip}" in lines[0]
    assert "seq=5" in lines[1]
    # every line also carries the unchained BASELINE, so a later reset of it
    # contradicts a dated, mirrored record instead of passing silently
    assert all("unchained=" in ln for ln in lines)


def test_a_break_is_never_anchored(fresh_db):
    """Appending after a failed verification would anchor a digest the
    verification just refused to bless."""
    from app.services import activity

    _log(3)
    activity.nightly_verify()
    _log(2)
    # tamper a row the tail run WILL see — one written since the last anchor.
    # (a row behind the anchor is the documented limit of the tail run and is
    # caught by the findings rule's full walk, not here)
    db.execute("UPDATE activity SET detail = 'tampered' WHERE seq = 4")
    result = activity.nightly_verify()
    assert not result["ok"]
    assert "anchor" not in result
    assert len(activity._anchor_log_paths()[0].read_text().splitlines()) == 1


def test_a_full_reforge_with_every_mark_rewritten_is_caught_by_the_anchor_log(fresh_db):
    """THE case no in-DB check can catch. The chain, the anchor, the high-water mark
    and the baseline all live in platform.db; rewrite them together and the
    full walk passes. The anchor log line was written on an earlier day, and
    the re-forge changed every anchored row's digest — content or lineage."""
    from app.services import activity

    _log(6)
    activity.nightly_verify()
    _reforge_everything(edit_seq=2, new_actor="someone-else")

    assert activity.verify_chain()["ok"]  # the in-DB checks are fully defeated
    result = activity.check_anchor_log()
    assert not result["ok"]
    assert result["seq"] == 6
    assert "no longer matches the record" in result["reason"]


def test_truncation_with_every_mark_rewritten_is_caught_by_the_anchor_log(fresh_db):
    from app.services import activity

    _log(6)
    activity.nightly_verify()
    db.execute("DELETE FROM activity WHERE seq >= 5")
    tail = db.query_row("SELECT seq, hash FROM activity WHERE seq = 4")
    activity._set_anchor(tail["seq"], tail["hash"])
    activity._put({activity.HIGH_SEQ: "4"})

    assert activity.verify_chain()["ok"]
    result = activity.check_anchor_log()
    assert not result["ok"]
    assert result["seq"] == 6
    assert result["reason"] == "an anchored entry is no longer in the ledger"


def test_the_findings_rule_reaches_the_anchor_check(fresh_db):
    from app.services import activity, insights

    _log(4)
    activity.nightly_verify()
    assert insights._r_activity_chain() == []
    _reforge_everything(edit_seq=1, new_actor="ghost")
    fired = insights._r_activity_chain()
    assert len(fired) == 1
    assert fired[0]["subject"] == "anchor:4"
    assert "backup mirror" in fired[0]["message"]


def test_no_anchor_log_is_not_an_error(fresh_db):
    """A deployment that has never completed a nightly run has nothing to
    check — reporting that as tampering would alarm every fresh install."""
    from app.services import activity

    _log(2)
    result = activity.check_anchor_log()
    assert result["ok"]
    assert result["checked"] == 0


def test_torn_and_conflicting_lines(fresh_db):
    from app.services import activity

    _log(3)
    activity.nightly_verify()
    path = activity._anchor_log_paths()[0]
    with path.open("a") as fh:
        fh.write("2026-08-02T03:30:00+00:00 seq=torn")  # crash mid-append
    assert activity.check_anchor_log()["ok"]  # torn lines are skipped, not failed

    with path.open("a") as fh:
        fh.write(f"\n2026-08-03T03:30:00+00:00 seq=3 hash={'0' * 64}\n")
    result = activity.check_anchor_log()
    assert not result["ok"]
    assert result["reason"] == "the anchor logs disagree about this entry"


def test_a_torn_line_does_not_swallow_the_next_night(fresh_db):
    """A torn line has no trailing newline, so a bare append would glue the
    next night's line onto it and lose BOTH from coverage."""
    from app.services import activity

    _log(3)
    activity.nightly_verify()
    path = activity._anchor_log_paths()[0]
    with path.open("a") as fh:
        fh.write("2026-08-02T03:30:00+00:00 seq=3 hash=deadbe")  # torn, no newline
    _log(1)
    activity.nightly_verify()
    result = activity.check_anchor_log()
    assert result["ok"]
    assert result["checked"] == 2  # seq 3 and seq 4 — the healed line parsed


def test_the_job_registry_actually_anchors(fresh_db):
    """The wiring, not the function: reverting the job body to a bare
    verify_tail() would leave every direct nightly_verify test green while
    production never writes a line."""
    from app.services import activity, jobs

    _log(2)
    spec = next(s for s in jobs.JOBS if s.name == "activity-verify")
    result = spec.fn()
    assert result["ok"]
    assert result["anchor"]["anchored"] == 2
    assert activity._anchor_log_paths()[0].exists()


def test_record_anchor_uses_the_verified_anchor_not_the_live_tail(fresh_db):
    """Rows written since verification are unverified — anchoring the live
    tail would launder whatever they happen to say into tomorrow's baseline."""
    from app.services import activity

    _log(3)
    activity.verify_tail()  # blesses seq 3
    _log(2)  # seq 4 and 5 exist but are unverified
    result = activity.record_anchor()
    assert result["anchored"] == 3
    assert "seq=3" in activity._anchor_log_paths()[0].read_text()


def test_deleting_the_local_log_is_caught_by_the_mirror(fresh_db, tmp_path, monkeypatch):
    """The cheapest local-file attack is rm, not a consistent rewrite. The
    mirror holds the same lines, and the check reads both."""
    from app.services import activity, admin

    monkeypatch.setattr(admin, "mirror_dir", lambda: tmp_path / "mirror")
    (tmp_path / "mirror").mkdir()
    _log(6)
    activity.nightly_verify()
    _reforge_everything(edit_seq=2, new_actor="ghost")
    activity._anchor_log_paths()[0].unlink()  # attacker removes the local log

    assert activity.verify_chain()["ok"]
    result = activity.check_anchor_log()
    assert not result["ok"]
    assert result["seq"] == 6


def test_a_rewritten_local_log_conflicts_with_the_mirror(fresh_db, tmp_path, monkeypatch):
    """A local log rewritten to agree with the forged ledger disagrees with
    the mirror's honest line for the same seq."""
    from app.services import activity, admin

    monkeypatch.setattr(admin, "mirror_dir", lambda: tmp_path / "mirror")
    (tmp_path / "mirror").mkdir()
    _log(6)
    activity.nightly_verify()
    _reforge_everything(edit_seq=2, new_actor="ghost")
    forged_tip = db.query_row("SELECT hash FROM activity WHERE seq = 6")["hash"]
    activity._anchor_log_paths()[0].write_text(f"{db.now()} seq=6 hash={forged_tip}\n")

    result = activity.check_anchor_log()
    assert not result["ok"]
    assert result["reason"] == "the anchor logs disagree about this entry"


def test_an_unmounted_mirror_directory_is_never_manufactured(fresh_db, tmp_path, monkeypatch):
    """mkdir on the mirror path would build the mount point on the LOCAL disk;
    the append would land on the wrong disk and be shadowed when the real
    mount returns — a silent hole in the history."""
    from app.services import activity, admin

    mirror = tmp_path / "not-mounted"
    monkeypatch.setattr(admin, "mirror_dir", lambda: mirror)
    _log(2)
    result = activity.nightly_verify()
    assert result["ok"]
    assert result["anchor"]["anchored"] == 2
    assert len(result["anchor"]["files"]) == 1  # local only
    assert not mirror.exists()


def test_a_night_where_nothing_landed_reports_zero(fresh_db, monkeypatch):
    from app.services import activity

    _log(2)
    activity.verify_tail()
    monkeypatch.setattr(activity, "_anchor_log_paths", lambda: [])
    assert activity.record_anchor() == {"anchored": 0, "files": [], "current": []}


def test_the_mirror_gets_its_own_append_never_a_copy(fresh_db, tmp_path, monkeypatch):
    """Appending to each file independently means truncating the local file
    can never shorten the mirror's history."""
    from app.services import activity, admin

    mirror = tmp_path / "mirror"
    mirror.mkdir()  # a mounted mirror exists; record_anchor never mkdirs it
    monkeypatch.setattr(admin, "mirror_dir", lambda: mirror)
    _log(2)
    activity.nightly_verify()
    _log(1)
    activity.nightly_verify()

    local, mirrored = activity._anchor_log_paths()
    assert str(mirrored).startswith(str(mirror))
    assert local.read_text() == (mirror / activity.ANCHOR_LOG).read_text()

    local.write_text("")  # local truncated — the mirror keeps both lines
    assert len((mirror / activity.ANCHOR_LOG).read_text().splitlines()) == 2


def test_a_sandboxed_data_dir_never_touches_the_mirror(fresh_db, tmp_path, monkeypatch):
    """The same guard the backup mirror has: a test or dev run must not write
    the off-box record."""
    from app.services import activity

    mirror = tmp_path / "mirror"
    monkeypatch.setenv("SKEIN_BACKUP_MIRROR", str(mirror))
    _log(2)
    activity.nightly_verify()
    assert not mirror.exists()  # DATA_DIR is a tmp path, so mirror_dir refuses


def test_an_unwritable_mirror_does_not_fail_the_job(fresh_db, tmp_path, monkeypatch):
    """The mirror is a mounted path that is allowed to be absent — an
    unmounted NAS at 03:30 must not cost the night's local anchor."""
    from app.services import activity, admin

    blocker = tmp_path / "blocker"
    blocker.write_text("")  # a FILE where a directory is needed -> OSError
    monkeypatch.setattr(admin, "mirror_dir", lambda: blocker / "sub")
    _log(2)
    result = activity.nightly_verify()
    assert result["ok"]
    assert result["anchor"]["anchored"] == 2
    assert len(result["anchor"]["files"]) == 1  # local only


def test_an_unchanged_tip_is_not_appended_twice(fresh_db):
    """The startup catch-up runs the nightly job on every process start — a
    dev server restarting on file changes appended the same line dozens of
    times in an evening. One line per tip, not per boot."""
    from app.services import activity

    _log(3)
    activity.nightly_verify()
    activity.nightly_verify()
    result = activity.nightly_verify()
    path = activity._anchor_log_paths()[0]
    assert len(path.read_text().splitlines()) == 1
    assert result["anchor"]["anchored"] == 3  # already on record still counts
    assert result["anchor"]["files"] == []
    assert result["anchor"]["current"] == [str(path)]


def test_a_mirror_that_missed_a_night_still_catches_up(fresh_db, tmp_path, monkeypatch):
    """The skip is PER FILE: the local file already holding the line must not
    stop the mirror from getting it once the mount returns."""
    from app.services import activity, admin

    _log(2)
    activity.nightly_verify()  # mirror not configured yet — local only
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    monkeypatch.setattr(admin, "mirror_dir", lambda: mirror)
    result = activity.nightly_verify()
    assert result["anchor"]["files"] == [str(mirror / activity.ANCHOR_LOG)]
    local = activity._anchor_log_paths()[0]
    assert local.read_text() != ""
    assert "seq=2" in (mirror / activity.ANCHOR_LOG).read_text()


def test_a_reset_baseline_contradicts_the_anchor_log(fresh_db):
    """The in-DB baseline is re-derived whenever its app_settings row is
    absent, so deleting that ONE row re-baselines to whatever is present now
    and launders a smuggled row. The in-DB marks cannot see it; the anchor log
    can, because it recorded the baseline on a night that already passed."""
    from app.services import activity

    _log(3)
    assert activity.nightly_verify()["ok"]
    assert activity.check_anchor_log()["ok"]

    # smuggle a row in outside the chain, then reset the baseline
    db.execute(
        "INSERT INTO activity (actor, action, detail, created_at)"
        " VALUES ('mallory', 'approve_change', 'proposal 7 approved', ?)",
        (db.now(),),
    )
    assert activity.verify_chain()["ok"] is False  # caught, before the reset
    db.execute("DELETE FROM app_settings WHERE key = 'activity_chain_legacy'")
    assert activity.verify_chain()["ok"] is True  # the in-DB check is laundered

    out = activity.check_anchor_log()
    assert out["ok"] is False
    assert "baseline" in out["reason"]


def test_a_legitimate_unchained_row_reports_the_same_way(fresh_db):
    """Honest about what it cannot tell apart: a real fallback append raises
    the baseline the same way a laundering does, so the anchor-log check
    reports the fact and its date, never a verdict about intent. The verdict
    comes from adoption: the adopt_unchained receipt checked against the server
    log is where the two cases separate."""
    from app.services import activity

    _log(2)
    activity.nightly_verify()
    db.execute(
        "INSERT INTO activity (actor, action, detail, created_at)"
        " VALUES ('scheduler', 'run findings', 'fallback', ?)",
        (db.now(),),
    )
    db.execute("DELETE FROM app_settings WHERE key = 'activity_chain_legacy'")
    activity.verify_chain()  # re-baselines

    out = activity.check_anchor_log()
    assert out["ok"] is False
    assert "baseline" in out["reason"]


def test_the_baseline_contradiction_survives_until_adoption_records_it(fresh_db):
    """record_anchor wrote the CURRENT baseline, so one night after a
    laundering the elevated value became the new max() and the contradiction
    erased itself — the check held for under 24 hours, then went quiet WITH NO
    RECORD. Adoption is allowed to quiet it, because adoption is not silence:
    the smuggled row is chained where it can never again be edited or deleted
    unseen, a loud adopt_unchained receipt names it, and the receipt itself is
    anchored that same night. The alarm converts into a permanent record."""
    from app.services import activity

    _log(3)
    activity.nightly_verify()
    db.execute(
        "INSERT INTO activity (actor, action, detail, created_at)"
        " VALUES ('mallory', 'approve_change', 'smuggled', ?)",
        (db.now(),),
    )
    db.execute("DELETE FROM app_settings WHERE key = 'activity_chain_legacy'")
    activity.verify_chain()  # re-baselines
    # the contradiction holds until a night runs — no silent wash-out window
    assert activity.check_anchor_log()["ok"] is False

    result = activity.nightly_verify()
    assert result["adopted"] == 1
    # quiet again, but on the record: the receipt is chained AND anchored,
    # and the smuggled row is now tamper-evident like every other row
    assert activity.check_anchor_log()["ok"] is True
    receipt = db.query_row("SELECT seq FROM activity WHERE action = 'adopt_unchained'")
    assert receipt["seq"] is not None
    assert result["anchor"]["anchored"] == receipt["seq"]
    smuggled = db.query_row("SELECT id, seq FROM activity WHERE actor = 'mallory'")
    assert smuggled["seq"] is not None
    db.execute("UPDATE activity SET detail = 'laundered' WHERE id = ?", (smuggled["id"],))
    assert activity.verify_chain()["ok"] is False


def test_the_baseline_check_does_not_mask_a_reforge(fresh_db):
    """The baseline finding used to `return` early, short-circuiting the
    per-seq digest replay that is this function's primary job."""
    from app.services import activity

    _log(4)
    activity.nightly_verify()  # anchors seq 4
    # raise the baseline AND remove the anchored row — two independent faults
    db.execute(
        "INSERT INTO activity (actor, action, detail, created_at) VALUES ('x','y','',?)",
        (db.now(),),
    )
    db.execute("DELETE FROM app_settings WHERE key = 'activity_chain_legacy'")
    activity.verify_chain()  # re-baselines, so the baseline check will fire
    db.execute("DELETE FROM activity WHERE seq = 4")

    out = activity.check_anchor_log()
    assert out["ok"] is False
    # the digest replay still ran rather than returning at the baseline
    # finding — the replay is this function's primary job
    assert out["seq"] == 4
    assert "no longer in the ledger" in out["reason"]


# ---- adoption: the heal that replaced the permanent unchained alarm ----------


def test_a_fallback_row_is_adopted_into_the_chain(fresh_db):
    """db.log_activity records a row UNCHAINED when the write lock cannot be
    taken. Before adoption, one such row flipped every later verification to
    "tampered" forever — an alarm with no recovery path, caused by nothing
    but load, aimed at whoever runs the server."""
    _log(3)
    assert activity.verify_chain()["ok"]  # records the baseline (0)
    db.execute(
        "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
        ("tester", "test_action", "written under contention", db.now()),
    )
    assert not activity.verify_chain()["ok"]  # visible while pending

    result = activity.nightly_verify()
    assert result["adopted"] == 1
    assert result["ok"]
    after = activity.verify_chain()
    assert after["ok"]
    assert after["unchained_rows"] == 0
    receipt = db.query_row("SELECT seq, detail FROM activity WHERE action = 'adopt_unchained'")
    assert receipt["seq"] is not None  # the receipt is itself chained
    assert "1 row" in receipt["detail"]


def test_an_adopted_row_is_tamper_evident_afterwards(fresh_db):
    """The point of adopting rather than counting: an unchained row can be
    edited or deleted silently forever — it is structurally exempt from every
    link check, and a deletion even LOWERS the count below the baseline.
    Adoption ends both."""
    _log(2)
    rid = db.execute(
        "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?) RETURNING id",
        ("tester", "test_action", "orphan", db.now()),
    )
    activity.nightly_verify()
    db.execute("UPDATE activity SET detail = 'rewritten' WHERE id = ?", (rid,))
    assert not activity.verify_chain()["ok"]


def test_adoption_with_nothing_to_adopt_writes_no_receipt(fresh_db):
    """A nightly receipt on a clean chain is noise that teaches the feed's
    readers to skim past the one receipt that matters."""
    _log(2)
    result = activity.nightly_verify()
    assert result["adopted"] == 0
    assert db.query_one("SELECT id FROM activity WHERE action = 'adopt_unchained'") is None


def test_the_same_nights_anchor_covers_adopted_rows(fresh_db):
    """Adoption runs before verify and anchor inside nightly_verify, so the
    receipt and the adopted rows are blessed the same night — ordered the
    other way, the 06:50 findings walk fired a false HIGH tamper finding in
    the gap."""
    _log(2)
    db.execute(
        "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
        ("tester", "test_action", "", db.now()),
    )
    result = activity.nightly_verify()
    assert result["ok"]
    # 2 chained + 1 adopted + 1 receipt: the anchor blesses through the receipt
    assert result["anchor"]["anchored"] == 4


def test_adoption_lowers_the_baseline_so_it_is_not_an_allowance(fresh_db):
    """With legacy rows adopted but the baseline left standing, `unchained >
    legacy` admits that many smuggled rows silently. Lowering is also the one
    direction check_anchor_log permits — its alarm is a baseline above the
    lowest ever anchored."""
    for _ in range(3):
        db.execute(
            "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
            ("old", "legacy", "", db.now()),
        )
    _log(2)
    assert activity.verify_chain()["unchained_baseline"] == 3
    activity.nightly_verify()
    assert db.query_row("SELECT COUNT(*) AS n FROM activity WHERE seq IS NULL")["n"] == 0

    # one smuggled row must alarm again — a standing baseline of 3 absorbs it
    db.execute(
        "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
        ("mallory", "smuggled", "", db.now()),
    )
    result = activity.verify_chain()
    assert not result["ok"]
    assert result["unchained_baseline"] == 0
    assert activity.check_anchor_log()["ok"] is True  # lowering never trips the log


def test_the_adoption_finding_fires_once_per_receipt(fresh_db, monkeypatch):
    """The finding is the push signal that replaced the permanent alarm: the
    feed line alone is skimmable, and after adoption verify_chain goes green
    again, so without this rule a smuggled row never reached the findings
    surface at all."""
    from datetime import date

    from app.services import insights

    _log(2)
    db.execute(
        "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
        ("mallory", "smuggled", "", db.now()),
    )
    activity.nightly_verify()
    found = insights._r_activity_chain()
    assert len(found) == 1
    assert found[0]["rule_id"] == "ledger_rows_adopted"
    assert found[0]["severity"] == "medium"
    # the two counts, and the comparison between them, are the whole signal:
    # told to grep the log instead, an operator who found ONE genuine warning
    # stood down for every row adopted that night
    assert "expected" in found[0]["message"]
    assert "more than the rows expected" in found[0]["message"]
    receipt = db.query_row("SELECT seq FROM activity WHERE action = 'adopt_unchained'")
    assert found[0]["subject"] == f"adopt:{receipt['seq']}"
    # once the two-day window passes, the rule is quiet again — the receipt is
    # not editable (it is chained), so the window is moved, not the row
    monkeypatch.setattr(insights, "_today", lambda: date(2030, 1, 1))
    assert insights._r_activity_chain() == []


def test_an_unchained_row_does_not_suppress_the_digest_walk(fresh_db):
    """verify_chain used to RETURN at the unchained count, before the walk
    that is its primary job. One row with a NULL seq — cheap to arrange, and
    the honest lock-timeout path produces one by itself — then hid a re-forge
    of the whole chain until the nightly adoption cleared it."""
    _log(4)
    assert activity.verify_chain()["ok"]  # sets the baseline at 0
    # a smuggled unchained row AND a tampered chained row
    db.execute(
        "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
        ("mallory", "smuggled", "", db.now()),
    )
    db.execute("UPDATE activity SET detail = 'rewritten' WHERE seq = 2")
    result = activity.verify_chain()
    assert not result["ok"]
    # the DIGEST break is reported, not just the count that used to mask it
    assert result["broken_at"] == 2
    assert "does not match its digest" in result["reason"]


# --- properties: the hash design holds for ALL content, not chosen examples --

from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

_field = st.text(min_size=0, max_size=80)


@given(actor=_field.filter(bool), action=_field, detail=_field, moved=st.text("ab", min_size=1))
def test_content_cannot_imitate_a_field_boundary(actor, action, detail, moved):
    """Length-prefixing is the design claim in db.activity_hash: shifting
    characters across a field boundary must always change the digest, or
    ('ab','c') and ('a','bc') would be the same ledger row."""
    base = db.activity_hash(1, "2026-08-14T00:00:00", actor, action + moved, detail, "genesis")
    shifted = db.activity_hash(1, "2026-08-14T00:00:00", actor + moved, action, detail, "genesis")
    assert base != shifted


@settings(
    deadline=None,
    max_examples=25,
    # deliberate fixture reuse: the ledger is append-only, so every example
    # extends the same chain and a full verify still passes — the property
    # is stronger against one long mixed-content chain than many short ones
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(rows=st.lists(st.tuples(_field.filter(bool), _field, _field), min_size=1, max_size=4))
def test_any_text_round_trips_through_the_chain(fresh_db, rows):
    """Newlines, quotes, bidi controls, emoji — whatever lands in a detail
    string must chain and verify. A character class that broke verification
    would let an attacker write an uncheckable row on purpose."""
    for actor, action, detail in rows:
        db.log_activity(actor, action, detail)
    result = activity.verify_chain()
    assert result["ok"], result


def test_adoption_does_not_deadlock_or_collide_with_ordinary_appends(fresh_db):
    """Adoption assigns seqs itself, so it must hold the ledger lock.

    Without it, adoption reads the tail and writes the seqs that follow while
    ordinary appends do the same, and the two orders of the seq index entry
    and the advisory lock form a deadlock cycle. Measured on the unfixed
    code: DeadlockDetected on innocent business writes, and UniqueViolation
    on the adoption itself. Neither is in db.BUSY_ERRORS, so both surface as
    500s on requests that did nothing wrong."""
    errors: list[tuple[str, str]] = []

    def adopt():
        try:
            activity.adopt_unchained(actor="scheduler")
        except Exception as exc:  # the failure IS the assertion
            errors.append(("adopt", type(exc).__name__))

    def append():
        for i in range(6):
            try:
                with db.transaction():
                    db.log_activity("probe", "ordinary", f"w{i}")
            except Exception as exc:  # the failure IS the assertion
                errors.append(("append", type(exc).__name__))

    for _ in range(3):
        for i in range(4):
            db.execute(
                "INSERT INTO activity (actor, action, detail, created_at) VALUES (?, ?, ?, ?)",
                ("probe", "orphan", f"o{i}", db.now()),
            )
        threads = [threading.Thread(target=adopt)]
        threads += [threading.Thread(target=append) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    assert errors == []
    assert activity.verify_chain()["ok"]
