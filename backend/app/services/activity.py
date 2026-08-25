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
db.log_activity records a row unchained when a standalone chained append fails
(a business write must not 500 over bookkeeping). The nightly job ADOPTS them —
assigns each the tail seq and hash, appends one chained receipt naming the
rows, and lowers the unchained baseline to what remains. Adoption attests
content from that moment on, never provenance: an adopted row cannot be edited
or deleted silently afterwards, which an unchained row always could.

The receipt is the audit trail, and it names TWO counts: rows adopted, and rows
EXPECTED — the pre-chain baseline plus the fallback appends this process
recorded (db.UNCHAINED_FALLBACKS). Adopted above expected is a row nothing here
wrote. A count is what a machine can check: pointed at the server log instead,
an operator who found one genuine warning cleared every row adopted that night,
because one receipt covers them all. The row and counter share one transaction
and one fallback lock. A counter savepoint can fail while the row survives, so
the counter under-counts. The comparison then errs toward reporting.

WHAT THIS CATCHES, and what it does not. The digest is unkeyed and every input
to it is stored in the row it protects, so anyone who can write the database can
recompute the whole chain. The append path stores the live tip sequence and
digest in the same transaction as each row. Verification compares that tip,
the last verified anchor, and the legacy unchained baseline. These marks live
in the same database, so they do not defeat an attacker who also rewrites
app_settings.

The ANCHOR LOG covers that case. Each successful nightly verification appends
the verified tip (seq + digest) to a file beside the backups AND,
independently, to the same file on SKEIN_BACKUP_MIRROR. The daily findings
rule replays every line ever anchored — from BOTH files — against the ledger
as it exists now, so a re-forge or a truncation has to contradict a record
made on an earlier day, and deleting or rewriting the local log is caught by
the mirror's copy of the same lines. Honest limits, in order of sharpness:
rows newer than the last nightly line are covered only by the in-DB marks
until tonight. Without a mirror, the local log is the only independent history.
Losing it raises a missing-anchor fault while the in-DB anchor remains. An
attacker who can rewrite the database and every anchor file is not caught. A
missing mirror is skipped when the local anchor succeeds. Detection, never
prevention.
"""

import logging
import os
import re
from bisect import bisect_right
from itertools import pairwise

from .. import db

log = logging.getLogger("skein")

ANCHOR_SEQ = "activity_chain_seq"
ANCHOR_HASH = "activity_chain_hash"
HIGH_SEQ = db.ACTIVITY_HIGH_SEQ
HIGH_HASH = db.ACTIVITY_HIGH_HASH
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


def _mark_int(got: dict[str, str], key: str, *, required: bool = False) -> int:
    raw = got.get(key)
    if raw is None:
        if required:
            raise ValueError(f"{key} is missing")
        return 0
    if (
        not re.fullmatch(r"(?:0|[1-9][0-9]*)", raw)
        or len(raw) > 19
        or (len(raw) == 19 and raw > "9223372036854775807")
    ):
        raise ValueError(f"{key} is invalid")
    return int(raw)


def _mark_hash(got: dict[str, str], key: str, *, required: bool = False) -> str:
    value = got.get(key, "")
    if not value and not required:
        return ""
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{key} is invalid")
    return value


def _anchor() -> tuple[int, str]:
    got = _settings(ANCHOR_SEQ, ANCHOR_HASH)
    seq = _mark_int(got, ANCHOR_SEQ)
    if seq == 0 and ANCHOR_HASH in got:
        raise ValueError(f"{ANCHOR_HASH} has no sequence")
    return seq, _mark_hash(got, ANCHOR_HASH, required=seq > 0)


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
        row["detail"],
        db.GENESIS_PREV if row["seq"] == 1 else row["prev_hash"],
    )


def _shape_fault(since_seq: int) -> dict | None:
    sql = (
        "SELECT id, seq FROM activity WHERE ("
        " detail IS NULL OR seq <= 0"
        " OR (seq IS NULL AND (hash IS NOT NULL OR prev_hash IS NOT NULL))"
        " OR (seq = 1 AND (hash IS NULL OR hash !~ '^[0-9a-f]{64}$'"
        " OR prev_hash IS NOT NULL))"
        " OR (seq > 1 AND (hash IS NULL OR hash !~ '^[0-9a-f]{64}$'"
        " OR prev_hash IS NULL OR prev_hash !~ '^[0-9a-f]{64}$')))"
    )
    params: tuple = ()
    if since_seq:
        # The incremental check covers its anchor and suffix. The daily full
        # walk owns old chained rows, or this cheap path becomes O(N) too.
        sql += " AND (seq IS NULL OR seq >= ?)"
        params = (since_seq,)
    return db.query_one(sql + " ORDER BY id LIMIT 1", params)


def _verify_chain_snapshot(since_seq: int = 0, expected_prev: str = "") -> tuple[dict, int, str]:
    """Walk one repeatable-read snapshot and return its exact live tip."""
    rows = db.query(
        "SELECT seq, hash, prev_hash, actor, action, detail, created_at FROM activity"
        " WHERE seq IS NOT NULL AND seq > ? ORDER BY seq ASC",
        (since_seq,),
    )
    unchained = db.query_row("SELECT COUNT(*) AS n FROM activity WHERE seq IS NULL")["n"]
    latest = db.query_row("SELECT COALESCE(MAX(seq), 0) AS seq FROM activity")["seq"]
    tail = db.query_one(
        "SELECT seq, hash FROM activity WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1"
    )
    marks = _settings(HIGH_SEQ, HIGH_HASH, LEGACY_UNCHAINED)
    out: dict = {
        "ok": True,
        "entries": len(rows),
        "chained_from": rows[0]["seq"] if rows else None,
        "chained_through": rows[-1]["seq"] if rows else None,
        "broken_at": None,
        "reason": "",
        "unchained_rows": unchained,
        "unchained_baseline": 0,
    }

    if fault := _shape_fault(since_seq):
        out.update(
            ok=False,
            broken_at=fault["seq"],
            reason=(
                f"activity row {fault['id']} has invalid chain fields. Compare the"
                " ledger with the most recent backup."
            ),
        )
        return out, latest, tail["hash"] if tail else ""

    try:
        legacy = _mark_int(marks, LEGACY_UNCHAINED, required=True)
        anchor_seq, anchor_hash = _anchor()
        if latest:
            live_seq = _mark_int(marks, HIGH_SEQ, required=True)
            live_hash = _mark_hash(marks, HIGH_HASH, required=True)
        else:
            if HIGH_SEQ in marks or HIGH_HASH in marks:
                raise ValueError("the empty chain has a live tip")
            live_seq, live_hash = 0, ""
    except ValueError as exc:
        out.update(
            ok=False,
            reason=(
                f"An activity-chain mark is invalid ({exc}). Compare app_settings"
                " with the most recent backup."
            ),
        )
        return out, latest, tail["hash"] if tail else ""

    out["unchained_baseline"] = legacy
    if latest != live_seq:
        removed = latest < live_seq
        out.update(
            ok=False,
            broken_at=min(latest, live_seq) + 1,
            reason=(
                f"The chain ends at {latest}, but its append-owned tip is {live_seq}."
                if removed
                else f"The chain reaches {latest}, but its append-owned tip is {live_seq}."
            )
            + " Compare the ledger with the most recent backup.",
        )
        return out, latest, tail["hash"] if tail else ""
    if unchained != legacy:
        difference = abs(unchained - legacy)
        if unchained > legacy:
            noun = "1 row sits" if difference == 1 else f"{difference} rows sit"
            reason = (
                f"{noun} outside the chain. The nightly adoption chains every"
                " unchained row and records an adopt_unchained receipt with the"
                " adopted and expected counts. If the adopted count is larger, a"
                " row reached the database outside the service layer. Treat that as"
                " tampering. Compare the ledger with the most recent backup in"
                " data/backups."
            )
        else:
            noun = "1 unchained row is" if difference == 1 else f"{difference} unchained rows are"
            reason = (
                f"{noun} missing from the recorded baseline. Compare the ledger"
                " with the most recent backup in data/backups."
            )
        out.update(ok=False, reason=reason)
        # Continue the digest walk. An unchained-row change must not mask a
        # forged chained row, which is the more specific fault.

    prev = db.GENESIS_PREV
    if since_seq > 0:
        anchor = db.query_one(
            "SELECT seq, hash, prev_hash, actor, action, detail, created_at FROM activity"
            " WHERE seq = ?",
            (since_seq,),
        )
        if anchor is None:
            out.update(ok=False, broken_at=since_seq, reason="The anchor row is missing.")
            return out, latest, tail["hash"] if tail else ""
        prev = _digest(anchor)
        if prev != (expected_prev or anchor["hash"]):
            out.update(
                ok=False,
                broken_at=since_seq,
                reason="The row content does not match its digest.",
            )
            return out, latest, tail["hash"] if tail else ""
    elif rows and rows[0]["seq"] != 1:
        out.update(
            ok=False,
            broken_at=rows[0]["seq"],
            reason=(
                f"The chain starts at {rows[0]['seq']}, not 1. Compare the ledger"
                " with the most recent backup."
            ),
        )
        return out, latest, tail["hash"] if tail else ""

    want = 1 if since_seq == 0 else since_seq + 1
    for row in rows:
        seq = row["seq"]
        if seq != want:
            out.update(ok=False, broken_at=want, reason=f"Sequence {want} is missing.")
            return out, latest, tail["hash"] if tail else ""
        expected_link = db.GENESIS_PREV if seq == 1 else row["prev_hash"]
        if expected_link != prev:
            out.update(
                ok=False,
                broken_at=seq,
                reason="The previous digest does not match the row before it.",
            )
            return out, latest, tail["hash"] if tail else ""
        digest = _digest(row)
        if digest != row["hash"]:
            out.update(
                ok=False,
                broken_at=seq,
                reason="The row content does not match its digest.",
            )
            return out, latest, tail["hash"] if tail else ""
        if since_seq == 0 and seq == anchor_seq and anchor_hash and digest != anchor_hash:
            out.update(
                ok=False,
                broken_at=seq,
                reason="The verified anchor does not match this row.",
            )
            return out, latest, tail["hash"] if tail else ""
        prev = digest
        want += 1

    if since_seq == 0 and anchor_seq > latest:
        out.update(
            ok=False,
            broken_at=latest + 1,
            reason=(
                f"The verified anchor points at sequence {anchor_seq}, past the chain"
                " end. Compare the ledger with the most recent backup."
            ),
        )
        return out, latest, tail["hash"] if tail else ""
    if latest and (tail is None or tail["hash"] != live_hash):
        out.update(
            ok=False,
            broken_at=latest,
            reason=(
                "The chain tail does not match its append-owned digest. Compare the"
                " ledger with the most recent backup."
            ),
        )
    return out, latest, tail["hash"] if tail else ""


def verify_chain(since_seq: int = 0, expected_prev: str = "") -> dict:
    """Recompute the chain from one read snapshot without changing trust marks."""
    with db.read_transaction():
        result, _, _ = _verify_chain_snapshot(since_seq, expected_prev)
    return result


def _advance_anchor(seq: int, digest: str) -> None:
    """Advance the verified anchor without letting concurrent checks regress it."""
    with db.transaction():
        db.hold_activity_chain()
        current_seq, current_hash = _anchor()
        if current_seq > seq:
            return
        if current_seq == seq:
            if current_hash != digest:
                raise db.ActivityChainError("the verified anchor changed at one sequence")
            return
        row = db.query_one("SELECT hash FROM activity WHERE seq = ?", (seq,))
        if row is None or row["hash"] != digest:
            raise db.ActivityChainError("the verified tip changed before it was anchored")
        _set_anchor(seq, digest)


def verify_tail(*, advance: bool = False) -> dict:
    """Verify the suffix after the stored anchor.

    Reads are pure by default. The scheduled job sets advance=True after it
    obtains a valid result, so a weak GET cannot change the trust baseline.
    """
    with db.read_transaction():
        try:
            seq, digest = _anchor()
        except ValueError as exc:
            return {
                "ok": False,
                "entries": 0,
                "chained_from": None,
                "chained_through": None,
                "broken_at": None,
                "reason": f"The verified anchor is invalid ({exc}).",
                "unchained_rows": 0,
                "unchained_baseline": 0,
            }
        result, tip_seq, tip_hash = _verify_chain_snapshot(seq, digest)
    if advance and result["ok"] and tip_seq:
        _advance_anchor(tip_seq, tip_hash)
    return result


ANCHOR_LOG = "activity-anchors.log"
# `unchained=` is optional so lines written before it existed still parse.
# NOT symmetric: the old pattern was $-anchored, so an OLD binary reading a
# NEW log matches nothing and, because non-matching lines are skipped,
# reports ok with checked=0. Rolling back past this change means the anchor
# replay silently stops checking.
_ANCHOR_LINE = re.compile(
    r"^\S+ seq=([1-9][0-9]{0,18}) hash=([0-9a-f]{64})"
    r"(?: unchained=(0|[1-9][0-9]{0,18}))?$"
)


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
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for raw in reversed(lines):
        m = _ANCHOR_LINE.match(raw.strip())
        if m:
            seq = int(m.group(1))
            if seq <= 9_223_372_036_854_775_807:
                return seq, m.group(2)
    return None


def _sync_anchor(path) -> None:
    """Flush one anchor and its directory entry before reporting success."""
    with path.open("rb") as fh:
        os.fsync(fh.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _append_anchor_line(path, line: str) -> None:
    prefix = ""
    if path.exists() and path.stat().st_size:
        with path.open("rb") as fh:
            fh.seek(-1, 2)
            if fh.read(1) != b"\n":
                # a crash mid-append left a torn line with no newline;
                # gluing the next line onto it would lose BOTH
                prefix = "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(prefix + line)
    _sync_anchor(path)


_BACKUP_ANCHOR_LINE = re.compile(r"^\S+ backup=(\S+) sha256=([0-9a-f]{64})$")


def record_backup_digest(name: str, digest: str) -> list[str]:
    """Append one backup file's SHA-256 beside the chain anchors.

    Restore verifies the LEDGER by exact anchor matching, but every other row
    of a dump that rested on a writable backup volume restores silently. The
    anchor logs are the existing independent record (local plus mirror, the
    trust boundary docs/FEATURES.md states), so the digest rides them. Both
    the anchor replay and _tail_anchor_line skip lines they cannot parse, so
    these lines cannot disturb a chain check.
    """
    # A name holding whitespace could inject a forged chain-anchor line into
    # the log this function exists to make trustworthy.
    if re.search(r"\s", name) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("The backup digest line was refused: the name or digest is malformed.")
    line = f"{db.now()} backup={name} sha256={digest}\n"
    written = []
    for path in _anchor_log_paths():
        try:
            _append_anchor_line(path, line)
            written.append(str(path))
        except OSError:
            log.warning("could not record the backup digest in %s", path, exc_info=True)
    return written


def recorded_backup_digests(name: str) -> set[str]:
    """Every digest the anchor logs hold for one backup file name.

    More than one value for the same name means a log disagrees with its
    mirror — the caller must treat the file as unverified.
    """
    digests: set[str] = set()
    for path in _anchor_log_paths():
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw in lines:
            m = _BACKUP_ANCHOR_LINE.match(raw.strip())
            if m and m.group(1) == name:
                digests.add(m.group(2))
    return digests


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
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw in lines:
            m = _ANCHOR_LINE.match(raw.strip())
            if m and m.group(3) is not None:
                value = int(m.group(3))
                if value <= 9_223_372_036_854_775_807:
                    lowest = value if lowest is None else min(lowest, value)
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
    # The migration records the unchained baseline once, and adoption only
    # lowers it. A database writer can still raise that setting to match a row
    # inserted outside the chain. Anchor the LOWEST value ever recorded, or one
    # later anchor would turn the rewritten value into the new external truth.
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
                _sync_anchor(path)
                current.append(str(path))
                continue
            # no mkdir here, deliberately. _backups_dir() already creates the
            # local dir, and manufacturing the MIRROR directory would build the
            # mount point on the local disk when the NAS is unmounted — the
            # append would then succeed onto the wrong disk and be shadowed
            # when the real mount returns, a silent hole in the history whose
            # continuity is the whole point. Let a missing mount raise.
            _append_anchor_line(path, line)
            written.append(str(path))
        except OSError:
            log.warning("could not append the chain anchor to %s", path, exc_info=True)
    # anchored reports what actually landed or was already on record — a night
    # where every append failed must not read as a success in the job outcome
    return {"anchored": seq if written or current else 0, "files": written, "current": current}


def adopt_unchained(actor: str = "scheduler") -> dict:
    """Chain every row that sits outside the chain, and record a receipt.

    One transaction holding db.py's activity advisory lock across read-tail,
    the updates, and the receipt, so no chained append can interleave and take
    a seq this function is about to assign. Only seq-NULL
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
        # FIRST in the transaction, before the tail read the seqs come from.
        # db.py's flush takes this same lock last, so taking it after any
        # write here would close a deadlock cycle with every ordinary append.
        db.hold_activity_chain()
        # SECOND, after the chain lock. Fallback writers take only this lock, so
        # adoption can snapshot their rows and counter without a deadlock cycle.
        db.hold_activity_fallbacks()
        marks = _settings(LEGACY_UNCHAINED, db.UNCHAINED_FALLBACKS)
        # The migration owns this baseline. If it is absent or malformed,
        # adoption must not replace the fault with a fresh zero.
        legacy = _mark_int(marks, LEGACY_UNCHAINED, required=True)
        recorded = _mark_int(marks, db.UNCHAINED_FALLBACKS)
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
        # The receipt appended at commit validates the tail against this mark.
        # Move both in this transaction, or adoption creates the contradiction
        # that the append guard exists to stop.
        db.advance_activity_tip(seq, prev)
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
        accounted = legacy + recorded
        # The counter is best-effort. A savepoint preserves the unchained row if
        # its counter update fails, so it can under-count but cannot leave stale
        # credit after adoption consumes the row. Under-counting reports too
        # much, which is the safe direction for this signal.
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
    prior_seq, _ = _anchor()
    if prior_seq:
        existing = check_anchor_log()
        if not existing["ok"]:
            return {
                "ok": False,
                "status": "error",
                "reason": (
                    "The existing activity anchor record is incomplete. Check the"
                    f" backup paths before another anchor is written. {existing['reason']}"
                ),
                "adopted": 0,
                "anchor_check": existing,
            }
    adopted = adopt_unchained()
    result = verify_tail(advance=True)
    if not result["ok"]:
        result["status"] = "error"
    else:
        result["anchor"] = record_anchor()
        seq, _ = _anchor()
        if seq and not result["anchor"]["anchored"]:
            result["status"] = "error"
            result["reason"] = (
                "The verified activity tip was not saved to an anchor file."
                " Check the backup paths and file permissions."
            )
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
    honest lines for the same seq. Without a mirror, the local file is the only
    independent record. Losing its newest line raises a fault while the in-DB
    anchor remains.
    Lines that do not parse are skipped, not failed: a crash mid-append tears
    a line, and a false tamper alarm teaches the operator to ignore the true
    one.
    """
    out: dict = {"ok": True, "checked": 0, "seq": None, "reason": ""}
    recorded: dict[int, str] = {}
    baselines: dict[int, int] = {}
    for path in _anchor_log_paths():
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for raw in lines:
            m = _ANCHOR_LINE.match(raw.strip())
            if not m:
                continue
            seq, digest = int(m.group(1)), m.group(2)
            baseline = int(m.group(3)) if m.group(3) is not None else None
            if seq > 9_223_372_036_854_775_807 or (
                baseline is not None and baseline > 9_223_372_036_854_775_807
            ):
                continue
            if recorded.get(seq, digest) != digest:
                out.update(
                    ok=False,
                    seq=seq,
                    reason="the anchor logs disagree about this entry",
                )
                return out
            recorded[seq] = digest
            if baseline is not None:
                if seq in baselines and baselines[seq] != baseline:
                    out.update(
                        ok=False,
                        seq=seq,
                        reason="the anchor logs disagree about this entry",
                    )
                    return out
                baselines[seq] = baseline
    out["checked"] = len(recorded)
    return _check_anchor_database(recorded, baselines, out)


def _check_anchor_database(recorded: dict[int, str], baselines: dict[int, int], out: dict) -> dict:
    """Compare every permanent file claim inside one repeatable snapshot."""
    with db.read_transaction():
        try:
            stored_seq, stored_hash = _anchor()
        except ValueError as exc:
            out.update(ok=False, reason=f"The verified anchor is invalid ({exc}).")
            return out
        newest_anchor_missing = bool(stored_seq and recorded.get(stored_seq) != stored_hash)
        points = sorted(baselines.items())
        decreases = [
            (before_seq, after_seq)
            for (before_seq, before), (after_seq, after) in pairwise(points)
            if after < before
        ]
        receipts: list[int] = []
        if decreases:
            receipts = [
                int(row["seq"])
                for row in db.query(
                    "SELECT seq FROM activity WHERE action = 'adopt_unchained'"
                    " AND seq > ? AND seq <= ? ORDER BY seq",
                    (
                        min(before for before, _after in decreases),
                        max(after for _before, after in decreases),
                    ),
                )
            ]
        for (before_seq, before), (after_seq, after) in pairwise(points):
            if after > before:
                out.update(
                    ok=False,
                    seq=after_seq,
                    reason="an anchor raised the migration-owned unchained baseline",
                )
            elif after < before:
                index = bisect_right(receipts, before_seq)
                if index >= len(receipts) or receipts[index] > after_seq:
                    out.update(
                        ok=False,
                        seq=after_seq,
                        reason="an anchored baseline decrease has no adoption receipt",
                    )

        try:
            current_baseline = _mark_int(
                _settings(LEGACY_UNCHAINED), LEGACY_UNCHAINED, required=True
            )
        except ValueError as exc:
            current_baseline = None
            out.update(ok=False, reason=f"The unchained baseline is invalid ({exc}).")
        if points and current_baseline != points[-1][1]:
            out.update(
                ok=False,
                seq=points[-1][0],
                reason=(
                    "The database baseline does not match the newest anchor. Check"
                    " backups/activity-anchors.log and its mirror."
                ),
            )

        expected = iter(sorted(recorded))
        next_seq = next(expected, None)
        if next_seq is not None:
            for batch in db.query_batches(
                "SELECT seq, hash, prev_hash, actor, action, detail, created_at"
                " FROM activity WHERE seq = ANY(?) ORDER BY seq",
                (sorted(recorded),),
                batch_size=1000,
            ):
                for row in batch:
                    seq = int(row["seq"])
                    if next_seq is None or seq != next_seq:
                        out.update(
                            ok=False,
                            seq=next_seq,
                            reason="an anchored entry is no longer in the ledger",
                        )
                        return out
                    if _digest(row) != recorded[seq]:
                        out.update(
                            ok=False,
                            seq=seq,
                            reason=(
                                "an anchored entry no longer matches the record"
                                " made when it was verified"
                            ),
                        )
                        return out
                    next_seq = next(expected, None)
            if next_seq is not None:
                out.update(
                    ok=False,
                    seq=next_seq,
                    reason="an anchored entry is no longer in the ledger",
                )
                return out
        if newest_anchor_missing:
            out.update(
                ok=False,
                seq=stored_seq,
                reason=(
                    "The newest verified anchor is not in the anchor files. Check the"
                    " backup paths and file permissions."
                ),
            )
        return out


def chain_health() -> dict:
    """Compact structural status from one read snapshot.

    This does not walk every digest. The daily findings rule owns that cost.
    It checks the append-owned tip, unchained count, and stored anchor row.
    Malformed marks do not remove the rest of the health response.
    """
    with db.read_transaction():
        latest = db.query_row("SELECT COALESCE(MAX(seq), 0) AS seq FROM activity")["seq"]
        unchained = db.query_row("SELECT COUNT(*) AS n FROM activity WHERE seq IS NULL")["n"]
        marks = _settings(HIGH_SEQ, HIGH_HASH, LEGACY_UNCHAINED, ANCHOR_SEQ, ANCHOR_HASH)
        tail = db.query_one(
            "SELECT seq, hash, prev_hash, actor, action, detail, created_at FROM activity"
            " WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1"
        )
        marks_ok = True
        try:
            seq, anchor_hash = _anchor()
            baseline = _mark_int(marks, LEGACY_UNCHAINED, required=True)
            if latest:
                high = _mark_int(marks, HIGH_SEQ, required=True)
                high_hash = _mark_hash(marks, HIGH_HASH, required=True)
            else:
                high, high_hash = 0, ""
                if HIGH_SEQ in marks or HIGH_HASH in marks:
                    raise ValueError("the empty chain has a live tip")
        except ValueError:
            marks_ok = False
            seq, anchor_hash, baseline, high, high_hash = 0, "", 0, 0, ""

        try:
            tail_ok = (not latest and tail is None) or bool(
                tail
                and tail["seq"] == latest
                and tail["hash"] == high_hash
                and _digest(tail) == high_hash
            )
            anchor = (
                db.query_one(
                    "SELECT seq, hash, prev_hash, actor, action, detail, created_at"
                    " FROM activity WHERE seq = ?",
                    (seq,),
                )
                if seq
                else None
            )
            anchor_ok = (not seq and not anchor_hash) or bool(
                anchor and anchor["hash"] == anchor_hash and _digest(anchor) == anchor_hash
            )
        except (AttributeError, TypeError):
            tail_ok = anchor_ok = False

        marks_ok = (
            marks_ok
            and high == latest
            and unchained == baseline
            and seq <= latest
            and tail_ok
            and anchor_ok
        )
        return {
            "verified_through": seq,
            "latest": latest,
            "unverified": latest - seq,
            "high_water": high,
            "unchained_rows": unchained,
            "unchained_baseline": baseline,
            "marks_ok": marks_ok,
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
    "create_shared_chat": ("created a private shared chat", "normal"),
    "add_shared_chat_agent": ("added an agent to a private shared chat", "loud"),
    # Loud: each one changes who can read a private transcript.
    "invite_to_shared_chat": ("invited someone to a private shared chat", "loud"),
    "revoke_shared_chat_invitation": ("revoked a private shared-chat invitation", "loud"),
    "accept_shared_chat": ("accepted a private shared-chat invitation", "loud"),
    "decline_shared_chat": ("declined a private shared-chat invitation", "normal"),
    "set_shared_chat_role": ("changed a private shared-chat steward", "loud"),
    "leave_shared_chat": ("left a private shared chat", "loud"),
    "remove_shared_chat_member": ("removed someone from a private shared chat", "loud"),
    "archive_shared_chat": ("archived a private shared chat", "normal"),
    "restore_shared_chat": ("restored a private shared chat", "normal"),
    "rename_shared_chat": ("renamed a private shared chat", "normal"),
    "link_shared_chat_engagement": ("linked a private shared chat to work", "normal"),
    # Quiet: the detail carries an id, never private message text.
    "post_shared_chat_message": ("posted in a private shared chat", "quiet"),
    "invoke_shared_chat_agent": ("called an agent in a private shared chat", "quiet"),
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
    # The actor is the SCHEDULER, not the agent — the scheduler is what the
    # feed shows to every viewer, and an agent name here would put one
    # agent's row in front of the whole team under a system actor's exemption.
    # So the verb names what the scheduler did, and the agent is in the
    # detail. Loud: a turn nobody watched, and the only feed row that says it
    # happened at all (services/agent_runner.py).
    "agent_run": ("started an unattended agent run", "loud"),
    "claim_task": ("claimed a delegated task", "normal"),
    "report_progress": ("logged progress on a task", "normal"),
    "add_absence": ("recorded time away", "normal"),
    "delete_absence": ("deleted a time-away entry", "loud"),
    "add_promise": ("made a promise", "normal"),
    # the other direction: somebody outside the team owes US this one
    "await_promise": ("recorded a promise made to the team", "normal"),
    "update_promise": ("settled a promise", "normal"),
    "edit_promise": ("edited a promise", "normal"),
    "remember": ("saved a memory", "normal"),
    "forget": ("deleted a memory", "loud"),
    "propose_change": ("filed a proposal", "normal"),
    "propose_private_change": ("requested a private agent review", "quiet"),
    "approve_change": ("approved a proposal", "normal"),
    "reject_change": ("rejected a proposal", "loud"),
    "set_authority": ("changed an agent's authority", "loud"),
    "create_api_key": ("minted an API key", "loud"),
    "revoke_api_key": ("revoked an API key", "loud"),
    "revoke_all_api_keys": ("revoked every API key", "loud"),
    "revoke_api_keys_for": ("revoked a person's API keys", "loud"),
    "request_key": ("requested an API key", "normal"),
    "rename_user": ("renamed a teammate", "loud"),
    "repair_identity_ownership": ("repaired identity ownership", "loud"),
    "claim_content_identity": ("assigned content identity ownership", "loud"),
    "claim_machine_identity": ("assigned machine identity ownership", "loud"),
    "set_user_active": ("changed whether a teammate is active", "loud"),
    "set_context_strategy": ("changed the long-chat strategy", "loud"),
    # loud like the strategy above: it changes what every chat costs
    "set_model_pick": ("changed the team model", "loud"),
    "backup": ("took a manual backup", "normal"),
    # The portable file leaves Skein with workspace and crew work in it.
    "export": ("exported portable work data", "loud"),
    # loud for the reason above it: a capacity limit moved for the whole team
    "set_tuning": ("changed a deployment limit", "loud"),
    "set_team_theme": ("set the team default theme", "quiet"),
    "set_growth_interests": ("updated growth interests", "quiet"),
    "record_lesson": ("recorded a lesson", "normal"),
    "record_feedback": ("recorded feedback", "quiet"),
    "ingest_notes": ("ingested meeting notes", "normal"),
    "instantiate_playbook": ("started an engagement from a playbook", "normal"),
    "workflow_action": ("ran a workflow action", "normal"),
    "external_tool": ("ran a governed external tool", "normal"),
    "playbook_closeout": ("closed a playbook engagement and drafted its lesson", "normal"),
    "plan_snapshot": ("recorded the plan an engagement started with", "quiet"),
    "generate_handoff": ("generated a handoff package", "normal"),
    # quiet: attaching a file to your own chat turn is not the team's news,
    # and the detail carries no title — the row names the artifact id and its
    # size, never the filename, which is caller-controlled text going into a
    # hash-chained ledger that cannot be edited afterwards
    "upload_file": ("attached a file", "quiet"),
    # loud like every other destruction, and the detail names the id and the
    # size it freed, never the filename — that is caller-controlled text going
    # into a ledger that cannot be edited afterwards
    "delete_file": ("deleted an attached file", "loud"),
    "create_document": ("wrote a document", "normal"),
    "edit_document": ("changed a document", "normal"),
    "exec_readout": ("published an exec readout", "normal"),
    "schedule_event": ("scheduled an event", "normal"),
    "record_outcome": ("recorded what came out of a meeting", "quiet"),
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
    construction: `id` is outside the digest, so ordering by it would let the
    visible timeline disagree with the verified one).

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
    # idx_activity_seq must stay the plan for this query. Given the choice the
    # planner can pick idx_activity_actor and sort afterwards, and the visible
    # actor set is most of the table — that plan re-sorts nearly the whole
    # ledger on every page. The seq index already IS the sort order, so
    # walking it descending stops at `limit` rows however large the ledger
    # grows. The index is partial on seq IS NOT NULL, which is why the WHERE
    # above keeps that predicate: without it the index does not apply. It
    # lives in core_migrations/001_baseline.sql; dropping it costs a full sort
    # here, silently. There is no INDEXED BY to force it — PostgreSQL has no
    # planner hint, so the shape of the query is the whole lever.
    rows = db.query(
        f"SELECT seq, actor, action, detail, created_at FROM activity"  # noqa: S608 — placeholders built above
        f" WHERE {where} ORDER BY seq DESC LIMIT ?",
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
