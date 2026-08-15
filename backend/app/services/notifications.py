"""Notification tiers (noise budget):
- immediate: in-app row + Slack webhook post right now (if configured)
- digest:    in-app row, flushed to Slack in the twice-daily batch
- passive:   activity log only (no row, no ping)

Slack is optional — without SLACK_WEBHOOK_URL everything still lands in-app.
"""

import json
import logging
import threading
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .. import config, db

if TYPE_CHECKING:
    from . import scope

log = logging.getLogger(__name__)

TIERS = ("immediate", "digest", "passive")
NotificationBody = str | Callable[[dict], str | None]
_SOURCE_ALIASES = {
    "blocker_edit": "blocker",
    "event_cancel": "event",
    "intake_edit": "intake",
    "memory_forget": "memory",
    "note_delete": "note",
    "note_edit": "note",
    "promise_edit": "promise",
    "promise_settle": "promise",
    "task_completion": "task",
}


def _post_slack(text: str) -> None:
    if not config.SLACK_WEBHOOK_URL:
        return

    def send():
        try:
            req = urllib.request.Request(
                config.SLACK_WEBHOOK_URL,
                data=json.dumps({"text": text}).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception as exc:  # best-effort, but leave a trace for debugging
            log.warning("Slack webhook post failed: %s", exc)

    threading.Thread(target=send, daemon=True).start()


def notify(
    user: str,
    message: NotificationBody,
    tier: str = "digest",
    link: str = "",
    *,
    source_entity: str = "",
    source_id: int = 0,
) -> dict | None:
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}")
    if tier == "passive":
        if not isinstance(message, str):
            raise ValueError("a passive notification needs plain text")
        db.log_activity("notifier", "notify_passive", message[:120])
        return None
    source_entity = _SOURCE_ALIASES.get(source_entity, source_entity)
    from . import policy_context

    if source_id > 0 and policy_context.supports_resource(source_entity):
        if not callable(message):
            raise ValueError("a typed notification needs a source-row message builder")
        with db.transaction():
            source_row = policy_context.resource_row(source_entity, source_id)
            if source_row is None:
                return None
            rendered = message(source_row)
            if not rendered:
                return None
            return _insert_notification(
                user,
                rendered,
                tier,
                link,
                source_entity,
                source_id,
                policy_context.existing(source_entity, source_id),
            )
    rendered = message({"id": source_id}) if callable(message) else message
    if not rendered:
        return None
    return _insert_notification(user, rendered, tier, link, source_entity, source_id, {})


def _insert_notification(
    user: str,
    message: str,
    tier: str,
    link: str,
    source_entity: str,
    source_id: int,
    source_context: dict[str, str],
) -> dict:
    ts = db.now()
    nid = db.execute(
        "INSERT INTO notifications"
        ' ("user", tier, message, link, sent_at, created_at, source_entity, source_id,'
        " source_policy_context) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " RETURNING id",
        (
            user,
            tier,
            message,
            link,
            ts if tier == "immediate" else None,
            ts,
            source_entity,
            source_id or None,
            json.dumps(source_context, sort_keys=True, separators=(",", ":")),
        ),
    )
    if tier == "immediate":
        # The SAME rule flush_digest_tier follows, and for the same reason:
        # this posts into ONE shared channel, `notifications` carries no tier
        # to test (scope.UNSCOPED says why), and the callers quote scoped row
        # titles into `message` — blockers.resolve_blocker, delegation and
        # mentions all do. A count carries nothing, whatever a caller writes.
        # No emoji: Slack is not one of Skein's own surfaces (CLAUDE.md).
        def post() -> None:
            _post_slack(f"Skein — 1 notification for {user}. Open Skein to read it.")

        if not db.on_commit(post):
            post()
    return {"id": nid, "tier": tier}


# Unread, for ONE reader. A personal row is unread while its own `read_at` is
# NULL. A 'team' row is a single shared record, so "read" is per person and
# lives in `notification_reads` (009) — without that table the first teammate
# to press dismiss cleared the announcement for everybody else.
UNREAD_FOR = (
    "\"user\" IN (?, 'team') AND read_at IS NULL"
    ' AND id NOT IN (SELECT notification_id FROM notification_reads WHERE "user" = ?)'
)


def list_notifications(
    user: str, unread_only: bool = True, *, limit: int = 50, offset: int = 0
) -> list[dict]:
    # 'team' notifications are addressed to everyone — same rule as briefing
    if unread_only:
        return db.query(
            f"SELECT * FROM notifications WHERE {UNREAD_FOR}"  # noqa: S608 — UNREAD_FOR is a module constant with bound marks
            " ORDER BY id DESC LIMIT ? OFFSET ?",
            (user, user, limit, offset),
        )
    return db.query(
        "SELECT * FROM notifications WHERE \"user\" IN (?, 'team') ORDER BY id DESC LIMIT ? OFFSET ?",
        (user, limit, offset),
    )


def list_notifications_filtered(
    user: str,
    unread_only: bool,
    resource_filter: Callable[[str, int, dict[str, str]], bool] | None,
    *,
    allow_unclassified: bool,
    viewer: "scope.Viewer | None" = None,
    limit: int = 50,
) -> list[dict]:
    """List up to `limit` policy-permitted notifications.

    The SQL LIMIT must not run before the policy filter: 50 denied rows at
    the head of the inbox otherwise hide a permitted row behind them forever.
    Pages are scanned until the limit fills, bounded at ten pages so a
    pathological inbox cannot turn one read into a full-table policy walk.
    """
    result: list[dict] = []
    page = 200
    for step in range(10):
        rows = list_notifications(user, unread_only, limit=page, offset=step * page)
        if not rows:
            break
        result.extend(
            policy_filter(
                rows,
                resource_filter,
                allow_unclassified=allow_unclassified,
                viewer=viewer,
            )
        )
        if len(result) >= limit or len(rows) < page:
            break
    return result[:limit]


def policy_filter(
    rows: list[dict],
    resource_filter: Callable[[str, int, dict[str, str]], bool] | None,
    *,
    allow_unclassified: bool,
    viewer: "scope.Viewer | None" = None,
) -> list[dict]:
    """Filter notification bodies by their typed source resource.

    Old rows have no source. Keep them when no applicable workplace rule
    exists. Otherwise, omit them because their body cannot be classified
    safely.
    """
    if resource_filter is None:
        return [_public_row(row) for row in rows]
    from . import policy_context

    resources = [source_resource(row) for row in rows]
    supported = [
        resource
        for resource in resources
        if policy_context.supports_resource(resource[0]) and resource[1] > 0
    ]
    current_contexts = (
        {resource: policy_context.existing(resource[0], resource[1]) for resource in supported}
        if viewer is None
        else policy_context.resource_contexts(supported, viewer)
    )
    result: list[dict] = []
    for row, resource in zip(rows, resources, strict=True):
        entity, entity_id = resource
        if not entity or entity_id <= 0 or not policy_context.supports_resource(entity):
            if allow_unclassified:
                result.append(_public_row(row))
            continue
        current = current_contexts.get((entity, entity_id))
        if current is None:
            # A deleted or unsupported source cannot make free-form text safe.
            continue
        if str(current.get("relationship_conflict") or "").lower() == "true":
            # Do not show the saved unscoped parent context to a policy rule.
            # The current viewer cannot classify this relationship safely.
            continue
        if not resource_filter(entity, entity_id, current):
            continue
        try:
            snapshot = json.loads(str(row.get("source_policy_context") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            snapshot = {}
        if not isinstance(snapshot, dict) or not snapshot:
            if allow_unclassified and resource_filter(entity, entity_id, current):
                result.append(_public_row(row))
            continue
        saved = {str(key): str(value) for key, value in snapshot.items()}
        if resource_filter(entity, entity_id, saved):
            result.append(_public_row(row))
    return result


def _public_row(row: dict) -> dict:
    return {
        key: value
        for key, value in row.items()
        if key not in {"source_entity", "source_id", "source_policy_context"}
    }


def source_resource(row: dict) -> tuple[str, int]:
    """Return the canonical domain resource that produced a notification."""
    entity = str(row.get("source_entity") or "")
    return _SOURCE_ALIASES.get(entity, entity), int(row.get("source_id") or 0)


def mark_read_matching(prefix: str) -> int:
    """Clear unread notifications whose message starts with `prefix` —
    used when the thing they point at (a review, a blocker) is resolved."""
    return db.execute_rowcount(
        "UPDATE notifications SET read_at = ? WHERE read_at IS NULL AND message LIKE ?",
        (db.now(), prefix + "%"),
    )


def mark_read(user: str, notification_id: int = 0) -> dict:
    """Dismiss for THIS reader.

    A row addressed to the reader by name clears its own `read_at`. A 'team'
    row is one shared record, so dismissing it records a per-person read in
    `notification_reads` (009) and leaves the row unread for everybody else —
    before that table the first reader silently dismissed the team's
    announcement for the whole roster.

    A dismiss the reader already made is not an error and not a second row:
    ON CONFLICT DO NOTHING against the (notification_id, user) primary key, so a
    double click counts once.
    """
    now = db.now()
    if notification_id:
        n = db.execute_rowcount(
            'UPDATE notifications SET read_at = ? WHERE id = ? AND "user" = ?',
            (now, notification_id, user),
        )
        n += db.execute_rowcount(
            'INSERT INTO notification_reads (notification_id, "user", read_at)'
            " SELECT id, ?, ? FROM notifications WHERE id = ? AND \"user\" = 'team'"
            " ON CONFLICT DO NOTHING",
            (user, now, notification_id),
        )
    else:
        n = db.execute_rowcount(
            'UPDATE notifications SET read_at = ? WHERE "user" = ? AND read_at IS NULL',
            (now, user),
        )
        n += db.execute_rowcount(
            'INSERT INTO notification_reads (notification_id, "user", read_at)'
            " SELECT id, ?, ? FROM notifications"
            " WHERE \"user\" = 'team' AND read_at IS NULL"
            " ON CONFLICT DO NOTHING",
            (user, now),
        )
    return {"marked": n}


def flush_digest_tier(*, claim: bool = False) -> dict:
    """Twice-daily job: batch unsent digest-tier notifications to Slack.
    claim=True makes the run once-only per hour bucket (scheduler path)."""
    if claim:
        bucket = datetime.now(UTC).strftime("%Y-%m-%dT%H")
        if not db.claim_job("notification-flush", bucket):
            return {"flushed": 0, "skipped": "already flushed this run"}
    pending = db.query(
        "SELECT * FROM notifications WHERE tier = 'digest' AND sent_at IS NULL ORDER BY id"
    )
    if pending:
        # COUNTS, never the messages. Every notify() addresses somebody who
        # can read the row it quotes, but this posts them all into ONE Slack
        # channel — so a crew task's title addressed to one member lands in
        # front of everybody. `notifications` carries no tier to filter on
        # (services/scope.py::UNSCOPED says why), and adding one would put the
        # rule at every notify() call site. A count carries nothing, whatever
        # a future caller writes.
        #
        # Nothing is lost: this post is a NUDGE. Every body is already an
        # in-app notification row, which is where the reader opens it — the
        # post itself carries no link, only the count and the app's name.
        #
        # No emoji: Slack is not one of Skein's own surfaces (CLAUDE.md).
        by_user: dict[str, int] = {}
        for n in pending:
            by_user[n["user"]] = by_user.get(n["user"], 0) + 1
        # the NOUN stays: "3 for Ana" dropped it along with the bodies, and a
        # bare integer in a channel next to real work names nothing at all
        counts = ", ".join(
            f"{n} notification{'' if n == 1 else 's'} for {u}" for u, n in sorted(by_user.items())
        )
        closer = "Open Skein to read it." if len(pending) == 1 else "Open Skein to read them."
        _post_slack(f"Skein digest — {counts}. {closer}")
        # Stamp exactly the rows we posted — a notification inserted between
        # the SELECT and this UPDATE must stay pending for the next flush.
        ids = [n["id"] for n in pending]
        db.execute_rowcount(
            f"UPDATE notifications SET sent_at = ? WHERE id IN ({','.join('?' * len(ids))})",  # noqa: S608 — keys hardcoded, id is a bound mark
            (db.now(), *ids),
        )
    return {"flushed": len(pending)}
