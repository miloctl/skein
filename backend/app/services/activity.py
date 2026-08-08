"""Provenance-ledger reads: verification of the tamper-evident chain over
`activity`.

Two verifications, on purpose:

- verify_tail() walks only what arrived since the last good anchor (stored in
  app_settings). Cheap, runs nightly, keeps /health honest about freshness.
- verify_chain() walks everything. An anchor is a claim about the past, so an
  incremental run can never notice an edit to a row it already passed. The
  daily findings rule therefore pays for the full walk — that is the run that
  answers "is the ledger intact", and it is the one an operator acts on.

Rows can sit OUTSIDE the chain: pre-036 rows never carried a seq, and
db.log_activity records a row unchained when the write lock cannot be taken (a
business write must not 500 over bookkeeping). The nightly job ADOPTS them —
assigns each the tail seq and hash, appends one chained receipt naming the
rows, and lowers the unchained baseline to what remains. Adoption attests
content from that moment on, never provenance: an adopted row cannot be edited
or deleted silently afterwards, which an unchained row always could.

The receipt is the audit trail, and it names TWO counts: rows adopted, and rows
EXPECTED — the pre-chain baseline plus the fallback appends this process
recorded (db.UNCHAINED_FALLBACKS). Adopted above expected is a row nothing here
wrote. A count is what a machine can check: pointed at the server log instead,
an operator who found one genuine warning cleared every row adopted that night,
because one receipt covers them all. The counter under-counts rather than over-
counts (its bump is a second write on a path the lock already refused), so the
comparison errs toward reporting, which is the safe direction for this signal.

WHAT THIS CATCHES, and what it does not. The digest is unkeyed and every input
to it is stored in the row it protects, so anyone who can write platform.db can
recompute the whole chain. Three marks in app_settings raise that cost — the
last verified anchor, a monotonic high-water seq, and the pre-036 unchained
baseline — but they live in the same file, so they do not defeat an attacker
who read this module and rewrites app_settings too.

The ANCHOR LOG covers that case. Each successful nightly verification appends
the verified tip (seq + digest) to a file beside the backups AND,
independently, to the same file on SKEIN_BACKUP_MIRROR. The daily findings
rule replays every line ever anchored — from BOTH files — against the ledger
as it exists now, so a re-forge or a truncation has to contradict a record
made on an earlier day, and deleting or rewriting the local log is caught by
the mirror's copy of the same lines. Honest limits, in order of sharpness:
rows newer than the last nightly line are covered only by the in-DB marks
until tonight; without a configured mirror, the local log is the only record
and deleting it silences the check; an attacker who can write the mirror too
is not caught at all; an unmounted mirror is skipped for that run, not
failed. Detection, never prevention.
"""

import logging
import re

from .. import db

log = logging.getLogger("skein")

ANCHOR_SEQ = "activity_chain_seq"
ANCHOR_HASH = "activity_chain_hash"
HIGH_SEQ = "activity_chain_high_seq"
LEGACY_UNCHAINED = "activity_chain_legacy"


def _settings(*keys: str) -> dict[str, str]:
    marks = ", ".join("?" for _ in keys)
    rows = db.query(f"SELECT key, value FROM app_settings WHERE key IN ({marks})", keys)  # noqa: S608 — keys hardcoded, id is a bound mark
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
        n = unchained - legacy
        noun = "1 row sits" if n == 1 else f"{n} rows sit"
        out.update(
            ok=False,
            # the SAME remedy the ledger_rows_adopted finding gives
            # (services/insights.py): one condition, one wording
            reason=(
                f"{noun} outside the chain. The nightly adoption chains every"
                " unchained row and records an adopt_unchained receipt naming two"
                " counts. If the rows adopted are more than the rows expected,"
                " a row reached the database outside the service layer."
                " Treat that as tampering and compare platform.db against the most"
                " recent backup in data/backups."
            ),
        )
        # NO return: the digest walk below is this function's primary job, and
        # returning here let ONE unchained row suppress it. That was cheap to
        # arrange — insert a row with a NULL seq, then re-forge the chain, and
        # the walk never ran. check_anchor_log already refuses to return early
        # for the same reason. A break found below overwrites this reason,
        # because a broken digest is the more specific finding of the two.

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


ANCHOR_LOG = "activity-anchors.log"
# `unchained=` is optional so lines written before it existed still parse.
# NOT symmetric: the old pattern was $-anchored, so an OLD binary reading a
# NEW log matches nothing and, because non-matching lines are skipped,
# reports ok with checked=0. Rolling back past this change means the anchor
# replay silently stops checking.
_ANCHOR_LINE = re.compile(r"^\S+ seq=(\d+) hash=([0-9a-f]{64})(?: unchained=(\d+))?$")


def _anchor_log_paths() -> list:
    """The local log beside the backups, plus the same file on the mirror.

    APPENDED to independently, never copied. A copy would let a truncated
    local file overwrite the mirror's longer history — and the mirror's
    history is the record the local file is compared against after a break.
    """
    from .admin import _backups_dir, mirror_dir

    paths = [_backups_dir() / ANCHOR_LOG]
    mirror = mirror_dir()
    if mirror is not None:
        paths.append(mirror / ANCHOR_LOG)
    return paths


def _tail_anchor_line(path) -> tuple[int, str] | None:
    """The last parseable (seq, digest) in an anchor file, or None."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw in reversed(lines):
        m = _ANCHOR_LINE.match(raw.strip())
        if m:
            return int(m.group(1)), m.group(2)
    return None


def _lowest_anchored_baseline() -> int | None:
    """The smallest unchained baseline any anchor line ever recorded.

    record_anchor writes this rather than the live value so a raised baseline
    can never become the new floor: the contradiction has to survive every
    later night, not just the first one.
    """
    lowest: int | None = None
    for path in _anchor_log_paths():
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            m = _ANCHOR_LINE.match(raw.strip())
            if m and m.group(3) is not None:
                v = int(m.group(3))
                lowest = v if lowest is None else min(lowest, v)
    return lowest


def record_anchor() -> dict:
    """Append the last VERIFIED tip to the anchor log(s) — one line per tip.

    Reads the app_settings anchor rather than the live tail: the tail may
    contain rows written since verification ran, and anchoring an unverified
    digest would launder whatever it happens to say into tomorrow's baseline.

    A file whose last line already records this tip is skipped. The startup
    catch-up runs this on every process start, and a dev server restarting on
    file changes appended the same line dozens of times in an evening. The
    check is PER FILE, so a mirror that was unmounted last night still gets
    the line the local file already has.

    A failed append is logged and skipped — the mirror is a mounted path that
    is allowed to be absent — but every file failing means the tip goes
    unanchored, which the job outcome records via the return value.
    """
    seq, digest = _anchor()
    if not seq or not digest:
        return {"anchored": 0, "files": []}
    # The unchained BASELINE rides along. The in-DB baseline self-heals: it is
    # re-derived whenever the app_settings key is absent, so deleting one row
    # re-baselines to whatever is present now and launders any row smuggled in
    # outside the chain. Anchoring it makes that a dated, mirrored
    # contradiction instead of a silent reset — the same property the anchor
    # log already gives the chain itself.
    # Anchor the LOWEST baseline ever recorded, not today's. Writing the
    # current value meant one night after a laundering the elevated baseline
    # became the new max() and the contradiction erased itself — the check
    # held for under 24 hours and then went quiet again.
    baseline = _int_setting(_settings(LEGACY_UNCHAINED), LEGACY_UNCHAINED)
    # `or baseline` was wrong the moment adoption started anchoring 0: zero is
    # falsy, so the lowest-ever clamp this line exists for silently stopped
    # running on every night after the first adoption
    lowest = _lowest_anchored_baseline()
    baseline = baseline if lowest is None else min(baseline, lowest)
    line = f"{db.now()} seq={seq} hash={digest} unchained={baseline}\n"
    written = []
    current = []
    for path in _anchor_log_paths():
        try:
            if _tail_anchor_line(path) == (seq, digest):
                current.append(str(path))
                continue
            # no mkdir here, deliberately. _backups_dir() already creates the
            # local dir, and manufacturing the MIRROR directory would build the
            # mount point on the local disk when the NAS is unmounted — the
            # append would then succeed onto the wrong disk and be shadowed
            # when the real mount returns, a silent hole in the history whose
            # continuity is the whole point. Let a missing mount raise.
            prefix = ""
            if path.exists() and path.stat().st_size:
                with path.open("rb") as fh:
                    fh.seek(-1, 2)
                    if fh.read(1) != b"\n":
                        # a crash mid-append left a torn line with no newline;
                        # gluing tonight's line onto it would lose BOTH
                        prefix = "\n"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(prefix + line)
            written.append(str(path))
        except OSError:
            log.warning("could not append the chain anchor to %s", path, exc_info=True)
    # anchored reports what actually landed or was already on record — a night
    # where every append failed must not read as a success in the job outcome
    return {"anchored": seq if written or current else 0, "files": written, "current": current}


def adopt_unchained(actor: str = "scheduler") -> dict:
    """Chain every row that sits outside the chain, and record a receipt.

    One transaction: the BEGIN IMMEDIATE holds the write lock across
    read-tail, the updates, and the receipt, so no chained append can
    interleave and take a seq this function is about to assign. Only seq-NULL
    rows are ever touched — a row that carries a seq is immutable history.

    A smuggled row is adopted exactly like a lock-timeout fallback, on
    purpose: the two are indistinguishable from the database alone, and
    refusing to adopt meant one fallback row alarmed forever.

    The receipt names BOTH numbers — rows chained, and fallbacks this server
    recorded (db.UNCHAINED_FALLBACKS) — because the count is the only part a
    machine can check. Told to grep the log instead, an operator who found
    one genuine "activity chain append failed" warning stood down for the
    whole night, and one receipt covers every row adopted that night: a
    smuggled row rode out on a real fallback's warning. `adopted` above
    `recorded` is a row nothing in this process wrote. It raises the bar
    rather than closing the hole — an attacker who can write `activity` can
    write `app_settings` too — but it removes the reasoning that cleared N
    rows on the evidence for one.

    The baseline is LOWERED to the new unchained count (0), never raised.
    check_anchor_log alarms on a baseline above the lowest ever anchored, so
    lowering is the one safe direction — and without it the old baseline
    becomes an allowance: `unchained > legacy` with legacy 3 admits three
    smuggled rows silently.
    """
    with db.transaction():
        orphans = db.query(
            "SELECT id, actor, action, detail, created_at FROM activity"
            # created_at first: the seqs land at the tail either way, but the
            # adopted block reads in the order the rows actually happened
            " WHERE seq IS NULL ORDER BY created_at, id"
        )
        if not orphans:
            return {"adopted": 0, "ids": []}
        tail = db.query_one(
            "SELECT seq, hash FROM activity WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1"
        )
        seq = tail["seq"] if tail else 0
        prev = tail["hash"] if tail else db.GENESIS_PREV
        for row in orphans:
            seq += 1
            digest = db.activity_hash(
                seq, row["created_at"], row["actor"], row["action"], row["detail"] or "", prev
            )
            db.execute(
                "UPDATE activity SET seq = ?, hash = ?, prev_hash = ? WHERE id = ?",
                (seq, digest, None if prev == db.GENESIS_PREV else prev, row["id"]),
            )
            prev = digest
        ids = [r["id"] for r in orphans]
        shown = ", ".join(str(i) for i in ids[:20])
        if len(ids) > 20:
            shown += f" and {len(ids) - 20} more"
        noun = "1 row" if len(ids) == 1 else f"{len(ids)} rows"
        ref = "id" if len(ids) == 1 else "ids"
        # What this adoption can ACCOUNT for. Both halves matter: rows that
        # predate the chain were never counted by the fallback counter (it did
        # not exist), so comparing against that counter alone reported the
        # first upgrade of every existing deployment as tampering.
        marks = _settings(LEGACY_UNCHAINED, db.UNCHAINED_FALLBACKS)
        legacy = _int_setting(marks, LEGACY_UNCHAINED)
        recorded = _int_setting(marks, db.UNCHAINED_FALLBACKS)
        accounted = legacy + recorded
        # The counter is best-effort — its bump is a second write, taken on a
        # path the write lock already refused once (db.py), so it can be lost.
        # It therefore UNDER-counts, and the comparison errs toward reporting
        # rather than toward silence. That is the safe direction for a tamper
        # signal, and the finding says so rather than claiming a verdict.
        db.log_activity(
            actor,
            "adopt_unchained",
            f"adopted {noun} into the activity chain ({ref} {shown})."
            f" This server expected {accounted}: {legacy} from before the chain"
            f" and {recorded} from a failed append",
        )
        # reset INSIDE the same transaction as the receipt that reports it,
        # so a crash cannot clear the counts without recording them
        _put({LEGACY_UNCHAINED: "0", db.UNCHAINED_FALLBACKS: "0"})
    return {"adopted": len(orphans), "ids": ids, "accounted": accounted}


def nightly_verify() -> dict:
    """The 03:30 job body: adopt rows recorded outside the chain, verify the
    tail, then anchor the verified tip.

    Adoption runs FIRST so tonight's anchor covers the adopted rows and the
    06:50 findings walk sees a healed chain — ordered the other way, every
    fallback row raised one false HIGH tamper finding before the heal.

    Anchoring only on ok is the point — after a break, appending would anchor
    a digest the verification just refused to bless.
    """
    adopted = adopt_unchained()
    result = verify_tail()
    if result["ok"]:
        result["anchor"] = record_anchor()
    result["adopted"] = adopted["adopted"]
    return result


def check_anchor_log() -> dict:
    """Replay every line ever anchored against the ledger as it exists now.

    This is the check the in-DB marks cannot make: a whole-chain re-forge that
    also rewrites app_settings passes verify_chain, but every anchored row's
    digest changed with the rewrite — content or lineage — so it no longer
    matches what was recorded on the night it was verified.

    Reads the local file AND the mirror copy when one is configured and
    mounted. Reading both is what makes deleting or rewriting the local log
    detectable: the deleted case falls back to the mirror's lines, and the
    rewritten-consistent-with-the-forgery case conflicts with the mirror's
    honest lines for the same seq. Without a mirror the local file is the only
    record, and losing it loses the check — the module docstring says so.
    Lines that do not parse are skipped, not failed: a crash mid-append tears
    a line, and a false tamper alarm teaches the operator to ignore the true
    one.
    """
    out: dict = {"ok": True, "checked": 0, "seq": None, "reason": ""}
    recorded: dict[int, str] = {}
    anchored_baseline: int | None = None
    for path in _anchor_log_paths():
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            m = _ANCHOR_LINE.match(raw.strip())
            if not m:
                continue
            seq, digest = int(m.group(1)), m.group(2)
            if recorded.get(seq, digest) != digest:
                out.update(
                    ok=False,
                    seq=seq,
                    reason="the anchor logs disagree about this entry",
                )
                return out
            recorded[seq] = digest
            if m.group(3) is not None:
                v = int(m.group(3))
                anchored_baseline = v if anchored_baseline is None else min(anchored_baseline, v)
    out["checked"] = len(recorded)
    # The in-DB baseline is re-derived whenever its app_settings row is
    # absent, so deleting that one row re-baselines to whatever is present
    # now — laundering any row smuggled in outside the chain, and leaving no
    # trace the in-DB marks can see. A baseline ABOVE the lowest ever
    # anchored is that reset. adopt_unchained only ever LOWERS the baseline,
    # so no legitimate path raises it — but this check cannot say who did,
    # so it reports the fact rather than pretending to a verdict.
    current_baseline = _int_setting(_settings(LEGACY_UNCHAINED), LEGACY_UNCHAINED)
    if anchored_baseline is not None and current_baseline > anchored_baseline:
        # recorded, then the digest replay still runs: a baseline discrepancy
        # can be an honest fallback append, and returning here would let it
        # mask a whole-chain re-forge, which is this function's primary job
        out.update(
            ok=False,
            reason=(
                f"the unchained baseline is {current_baseline} but an anchored night"
                f" recorded {anchored_baseline}. Either a row was written outside the"
                " chain, or the baseline was reset. Compare"
                " backups/activity-anchors.log against the off-box mirror."
            ),
        )
    for seq in sorted(recorded):
        row = db.query_one(
            "SELECT seq, hash, prev_hash, actor, action, detail, created_at FROM activity"
            " WHERE seq = ?",
            (seq,),
        )
        if row is None:
            out.update(ok=False, seq=seq, reason="an anchored entry is no longer in the ledger")
            return out
        if _digest(row) != recorded[seq]:
            out.update(
                ok=False,
                seq=seq,
                reason="an anchored entry no longer matches the record made when it was verified",
            )
            return out
    return out


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


# ---- the feed (docs: verb-object-outcome, one sentence per row) --------------

# action -> (past-tense verb phrase, salience). Salience tracks consequence:
# destructive and security-relevant actions are loud, ordinary writes are
# normal, system bookkeeping is quiet. An action missing here renders as an
# honest generic row — never a fabricated sentence — so a new log_activity
# call degrades instead of breaking, and the registry lives next to the
# ledger it names.
VERBS: dict[str, tuple[str, str]] = {
    # loud: an adoption nobody expected is the tamper signal (adopt_unchained)
    "adopt_unchained": ("ran an activity-chain adoption", "loud"),
    "capture": ("captured", "normal"),
    "save_note": ("saved a note", "normal"),
    "update_note": ("edited a note", "normal"),
    "delete_note": ("deleted a note", "loud"),
    "delete_chat": ("deleted a chat", "loud"),
    "ask_question": ("asked a question", "normal"),
    "answer_question": ("answered a question", "normal"),
    "assign_question": ("assigned a question", "normal"),
    "record_decision": ("recorded a decision", "normal"),
    "reconfirm_decision": ("reconfirmed a decision", "normal"),
    "stale_decision": ("marked a decision stale", "quiet"),
    "supersede_decision": ("superseded a decision", "normal"),
    "post_standup": ("posted a standup", "normal"),
    "create_task": ("created a task", "normal"),
    "update_task": ("updated a task", "normal"),
    "complete_task": ("completed a task", "normal"),
    "create_milestone": ("created a milestone", "normal"),
    "update_milestone": ("updated a milestone", "normal"),
    "create_engagement": ("created an engagement", "normal"),
    "create_crew": ("created a crew", "normal"),
    "update_crew": ("changed a crew", "normal"),
    # loud: membership is about to decide what a person reads
    # (docs/VISIBILITY.md), so a change to it is not ordinary bookkeeping
    "crew_member_add": ("added someone to a crew", "loud"),
    "crew_member_remove": ("removed someone from a crew", "loud"),
    "update_engagement": ("updated an engagement", "normal"),
    "raise_blocker": ("raised a blocker", "normal"),
    "resolve_blocker": ("resolved a blocker", "normal"),
    "escalate_blocker": ("escalated a blocker", "loud"),
    "edit_blocker": ("edited a blocker", "normal"),
    "submit_intake": ("submitted an intake request", "normal"),
    "score_intake": ("scored an intake request", "normal"),
    "disposition_intake": ("dispositioned an intake request", "normal"),
    "edit_intake": ("edited an intake request", "normal"),
    "accept_without_engagement": ("accepted a request without an engagement", "normal"),
    "delegate_task": ("delegated a task", "normal"),
    "claim_task": ("claimed a delegated task", "normal"),
    "report_progress": ("logged progress on a task", "normal"),
    "add_absence": ("recorded time away", "normal"),
    "delete_absence": ("deleted a time-away entry", "loud"),
    "add_promise": ("made a promise", "normal"),
    "update_promise": ("settled a promise", "normal"),
    "edit_promise": ("edited a promise", "normal"),
    "remember": ("saved a memory", "normal"),
    "forget": ("deleted a memory", "loud"),
    "propose_change": ("filed a proposal", "normal"),
    "approve_change": ("approved a proposal", "normal"),
    "reject_change": ("rejected a proposal", "loud"),
    "set_authority": ("changed an agent's authority", "loud"),
    "create_api_key": ("minted an API key", "loud"),
    "revoke_api_key": ("revoked an API key", "loud"),
    "revoke_all_api_keys": ("revoked every API key", "loud"),
    "revoke_api_keys_for": ("revoked a person's API keys", "loud"),
    "request_key": ("requested an API key", "normal"),
    "rename_user": ("renamed a teammate", "loud"),
    "set_user_active": ("changed whether a teammate is active", "loud"),
    "set_context_strategy": ("changed the long-chat strategy", "loud"),
    # loud like the strategy above: it changes what every chat costs
    "set_model_pick": ("changed the team model", "loud"),
    # loud for the reason above it: a capacity limit moved for the whole team
    "set_tuning": ("changed a deployment limit", "loud"),
    "set_team_theme": ("set the team default theme", "quiet"),
    "set_growth_interests": ("updated growth interests", "quiet"),
    "record_lesson": ("recorded a lesson", "normal"),
    "record_feedback": ("recorded feedback", "quiet"),
    "ingest_notes": ("ingested meeting notes", "normal"),
    "instantiate_playbook": ("started an engagement from a playbook", "normal"),
    "generate_handoff": ("generated a handoff package", "normal"),
    "exec_readout": ("published an exec readout", "normal"),
    "schedule_event": ("scheduled an event", "normal"),
    "cancel_event": ("cancelled an event", "loud"),
    "allocate": ("allocated a person to an engagement", "normal"),
    "deallocate": ("removed a person from an engagement", "normal"),
    "apply_weekly_plan": ("applied the weekly plan", "normal"),
    "disposition_finding": ("dispositioned a finding", "normal"),
    "publish_context_pack": ("published the context pack", "quiet"),
    "publish_digest": ("published the daily digest", "quiet"),
    "run_findings": ("ran the findings sweep", "quiet"),
    "retention_prune": ("pruned old records", "quiet"),
    "week_open": ("opened the week", "quiet"),
    "week_close": ("closed the week", "quiet"),
    "notify_passive": ("filed a passive notification", "quiet"),
}


# Actors that are processes, not people. An allowlist, because the previous
# blocklist-of-registered-humans was default-open: a human writing under a
# name that never got a users row (the Slack path did this) leaked to every
# viewer's feed labeled "system".
# 'forge' is the webhook acting for a push whose login names nobody on the
# roster. It belongs here for the same reason 'scheduler' does: the filter
# below is default-CLOSED, so an actor missing from this tuple writes rows
# that NO viewer can ever see, and the task appears to move by itself.
SYSTEM_ACTORS = ("system", "scheduler", "team", "forge")


def visible_actor_filter(viewer: str) -> tuple[str, list]:
    """SQL fragment limiting rows to the viewer's own strand: their actor,
    agent identities, and the known system processes. Default-CLOSED — an
    actor this cannot classify is hidden, never shown as system."""
    agents = [r["name"] for r in db.query("SELECT name FROM users WHERE kind = 'agent'")]
    allowed = [viewer, *agents, *SYSTEM_ACTORS]
    marks = ", ".join("?" for _ in allowed)
    return f"actor IN ({marks})", allowed


def feed(viewer: str, limit: int = 50, before: int = 0) -> dict:
    """The activity feed: one sentence per ledger row, newest first.

    SCOPE IS THE POINT, and it is enforced here in the service, not in a
    route: the feed shows agent and system actors plus the viewer's OWN rows.
    Another human's rows never appear — person-level data is for planning the
    future, not for watching colleagues (the anti-surveillance rule). There
    is deliberately no way to pass a different person.

    Covers chained rows only (seq is the cursor — monotonic and gap-free by
    construction, where rowid can be resequenced without breaking the chain).

    A row the nightly job ADOPTED takes a seq at the tail, so it enters this
    feed at its adoption time and not at its own created_at. On the first run
    after an upgrade that puts every pre-036 row at the top of the page, dated
    years ago. That is a consequence of paginating on seq, and the receipt
    beside them says what happened — but it surprises a reader, so it is
    written down here rather than left to be discovered.
    """
    limit = max(1, min(int(limit), 200))
    agents = {r["name"] for r in db.query("SELECT name FROM users WHERE kind = 'agent'")}
    actor_sql, params = visible_actor_filter(viewer)
    where = f"seq IS NOT NULL AND {actor_sql}"
    if before:
        where += " AND seq < ?"
        params.append(before)
    # INDEXED BY: left alone, the optimizer picks idx_activity_actor and then
    # sorts with a temp B-tree — and the visible actor set is most of the
    # table, so that plan re-sorts nearly the whole ledger on every page. The
    # seq index already IS the sort order; walking it descending stops at
    # `limit` rows no matter how large the ledger grows. Hard couplings:
    # idx_activity_seq lives in migrations/001_baseline.sql — rename or
    # drop it and this query raises OperationalError instead of replanning —
    # and it is partial on seq IS NOT NULL, so the WHERE above must keep that
    # predicate first or SQLite rejects the index the same way.
    rows = db.query(
        f"SELECT seq, actor, action, detail, created_at FROM activity"  # noqa: S608 — placeholders built above
        f" INDEXED BY idx_activity_seq WHERE {where} ORDER BY seq DESC LIMIT ?",
        (*params, limit + 1),
    )
    has_more = len(rows) > limit
    entries = []
    for row in rows[:limit]:
        verb = VERBS.get(row["action"])
        if row["actor"] == viewer:
            who = "you"
        elif row["actor"] in agents:
            who = "agent"
        else:
            who = "system"  # allowlisted literals only — nothing else gets here
        entries.append(
            {
                "seq": row["seq"],
                "actor": row["actor"],
                "who": who,
                # honesty over guessing: an unregistered action renders as the
                # raw action name, clearly generic, never a fabricated verb
                "sentence": (
                    f"{row['actor']} {verb[0]}" if verb else f"{row['actor']}: {row['action']}"
                ),
                "salience": verb[1] if verb else "normal",
                "registered": verb is not None,
                "action": row["action"],
                "detail": row["detail"] or "",
                "created_at": row["created_at"],
            }
        )
    return {
        "entries": entries,
        "next_before": entries[-1]["seq"] if has_more and entries else None,
    }
