"""A read-only recent summary and explicit per-reader review metadata."""

import hashlib
import json
from datetime import timedelta

from .. import db
from . import refs, scope

FINDING_CAP = 50
PROJECTION_VERSION = 1


def _review_metadata(user: str) -> dict:
    row = db.query_one("SELECT value FROM app_settings WHERE key = ?", (f"delta_reviewed:{user}",))
    return json.loads(row["value"]) if row else {"revision": 0, "snapshot_id": ""}


def brief(user: str, viewer: scope.Viewer = scope.NOBODY) -> dict:
    """Seven team dates. Review state never changes what is returned."""
    from .portfolio import engagement_health, health_changes

    today = db.today()
    start = today - timedelta(days=6)
    day = start.isoformat()
    since = db.local_midnight_utc(start)
    until = db.local_midnight_utc(today + timedelta(days=1))
    items: list[dict] = []

    # Health that MOVED. `health_changes` compares against the most recent
    # snapshot at or before `since`, so a colour that has been red all month
    # is correctly silent here — it is not news, and the manager queue is
    # where a standing condition belongs.
    for moved in health_changes(engagement_health(viewer, as_of=today), start):
        worse = _WORSE.get((moved["from"] or "", moved["to"]))
        # a FIRST score of green is not news. Every engagement is unscored
        # until the daily snapshot job has run once, so without this the first
        # brief on a fresh deployment is a list of every green engagement —
        # the exact wall of already-true rows this surface exists to avoid.
        if not moved["from"] and moved["to"] == "green":
            continue
        items.append(
            {
                "kind": "health_moved",
                "entity": "engagement",
                "entity_id": moved["id"],
                "headline": (
                    f"{moved['name']} went {moved['to']}"
                    if moved["from"]
                    else f"{moved['name']} is scored {moved['to']} for the first time"
                ),
                "direction": "worse" if worse else "better" if worse is False else "new",
                "receipts": [
                    refs.receipt(
                        f"engagement #{moved['id']} {moved['from'] or 'unscored'} to {moved['to']}"
                    )
                ],
                "link": f"/engagement/{moved['id']}",
            }
        )

    # Eligibility and subject deduplication precede the cap. Otherwise old or
    # system findings can hide every eligible row while the preview says quiet.
    from .intervention import _SYSTEM_AUDIENCE

    findings = db.query(
        "SELECT * FROM (SELECT DISTINCT ON (f.rule_id, f.subject)"
        " f.id, f.rule_id, f.subject, f.severity, f.message, f.created_at"
        " FROM findings f WHERE f.created_at >= ? AND f.created_at < ?"
        " AND NOT (f.rule_id = ANY(?))"
        # By finding id, never by subject: resolved and converted do not
        # suppress a re-fire (insights._suppressed), so a subject-wide
        # exclusion here hides the row Insights lists.
        " AND NOT EXISTS (SELECT 1 FROM finding_dispositions d WHERE d.finding_id = f.id)"
        " AND NOT EXISTS (SELECT 1 FROM findings old WHERE old.rule_id = f.rule_id"
        " AND old.subject = f.subject AND old.created_at < ?)"
        " ORDER BY f.rule_id, f.subject, f.created_at, f.id) eligible"
        " ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1"
        " WHEN 'low' THEN 2 ELSE 3 END, created_at, id LIMIT ?",
        (since, until, sorted(_SYSTEM_AUDIENCE), since, FINDING_CAP + 1),
    )
    truncated = len(findings) > FINDING_CAP
    for f in findings[:FINDING_CAP]:
        items.append(
            {
                "kind": "finding_new",
                "entity": "finding",
                "entity_id": f["id"],
                "headline": f["message"],
                "rule_id": f["rule_id"],
                "severity": f["severity"],
                "direction": "new",
                "receipts": [refs.receipt(f"finding #{f['id']} ({f['severity']})")],
                "link": "/insights",
            }
        )

    # A promise crosses its due date at the next team midnight. Using start
    # as the due-date floor drops promises that became overdue on the first day.
    for p in db.query(
        f"SELECT id, promise, to_whom, due_date FROM promises"  # noqa: S608 — module constant
        f" WHERE status = 'open' AND direction = 'given' AND {scope.WORKSPACE_ONLY}"
        " AND due_date IS NOT NULL AND due_date < ? AND due_date >= ?"
        " ORDER BY due_date, id",
        (today.isoformat(), (start - timedelta(days=1)).isoformat()),
    ):
        items.append(
            {
                "kind": "promise_broke",
                "entity": "promise",
                "entity_id": p["id"],
                "headline": f"The promise to {p['to_whom'] or 'the team'} passed its date",
                "direction": "worse",
                "receipts": [refs.receipt(f"promise #{p['id']} was due {p['due_date']}")],
                "link": f"/portfolio#promise-{p['id']}",
            }
        )

    # Work this reader sponsors that an agent finished asking about. The
    # sponsor is notified once at submission; this is the standing answer to
    # "is anything waiting on my verdict", which a dismissed notification
    # otherwise took away for good.
    tfrag, tp = scope.visible_filter(viewer, "tasks", alias="t")
    for c in db.query(
        # the task carries the tier and this quotes its TITLE, so the join side
        # takes its own filter — being the sponsor is not the same fact as
        # being able to read the row, and only one of them governs a title
        "SELECT p.id, p.entity_id, t.title FROM pending_changes p"  # noqa: S608 — scope.visible_filter emits only bound marks
        f" JOIN tasks t ON t.id = p.entity_id AND {tfrag}"
        " WHERE p.entity = 'task_completion' AND p.status = 'pending'"
        " AND t.sponsor = ? AND p.created_at >= ? AND p.created_at < ?"
        " ORDER BY p.created_at, p.id",
        (*tp, user, since, until),
    ):
        items.append(
            {
                "kind": "acceptance_waiting",
                "entity": "proposal",
                "entity_id": c["id"],
                "headline": f"An agent submitted '{c['title']}' for your acceptance",
                "direction": "new",
                "receipts": [refs.receipt(f"proposal #{c['id']} on task #{c['entity_id']}")],
                "link": f"/review?id={c['id']}",
            }
        )

    snapshot_id = hashlib.sha256(
        json.dumps(
            {"version": PROJECTION_VERSION, "user": user, "items": items, "truncated": truncated},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    reviewed = _review_metadata(user)
    return {
        "user": user,
        "since": since,
        "window_start": day,
        "window_end": today.isoformat(),
        "items": items,
        "quiet": not items,
        "snapshot_id": snapshot_id,
        "review_revision": reviewed["revision"],
        "reviewed": reviewed["snapshot_id"] == snapshot_id,
        "truncated": truncated,
    }


def acknowledge(user: str, snapshot: dict, snapshot_id: str, revision: int) -> dict:
    """Save only the authorized fingerprint, never a source timestamp or body."""
    if snapshot["truncated"]:
        # ValueError, not Conflict: a refresh returns the same incomplete
        # summary, so 409 names a retry that cannot succeed.
        raise ValueError(
            "The summary is incomplete. It cannot be marked reviewed."
            " Open Insights to work through the findings."
        )
    if snapshot["snapshot_id"] != snapshot_id:
        raise db.Conflict("The summary changed. Refresh before you mark it reviewed.")
    # The route must end its read snapshot first. Joining it here leaves the
    # metadata CAS on a stale repeatable-read snapshot after the lock waits.
    with db.transaction():
        db.name_lock(db.LOCK_RECEIPT, f"delta_reviewed:{user}")
        current = _review_metadata(user)
        if current["snapshot_id"] == snapshot_id and revision in (
            current["revision"],
            current["revision"] - 1,
        ):
            return {
                "snapshot_id": snapshot_id,
                "review_revision": current["revision"],
                "reviewed": True,
            }
        if current["revision"] != revision:
            raise db.Conflict("The review state changed. Refresh before you mark it reviewed.")
        metadata = {
            "version": PROJECTION_VERSION,
            "snapshot_id": snapshot_id,
            "revision": revision + 1,
            "origin": "human",
            "created_by": user,
        }
        db.execute(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (f"delta_reviewed:{user}", json.dumps(metadata, separators=(",", ":")), db.now()),
        )
        db.log_activity(user, "review_delta", "origin=human")
    return {"snapshot_id": snapshot_id, "review_revision": revision + 1, "reviewed": True}


# Which way a health move went. `None` for a first score: an engagement that
# never had a colour did not get worse, and calling it worse would invent a
# previous state the snapshot never held.
_WORSE = {
    ("green", "yellow"): True,
    ("green", "red"): True,
    ("yellow", "red"): True,
    ("yellow", "green"): False,
    ("red", "yellow"): False,
    ("red", "green"): False,
}
