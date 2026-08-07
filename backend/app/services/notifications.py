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
from datetime import UTC, datetime

from .. import config, db

log = logging.getLogger(__name__)

TIERS = ("immediate", "digest", "passive")


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


def notify(user: str, message: str, tier: str = "digest", link: str = "") -> dict | None:
    if tier not in TIERS:
        raise ValueError(f"tier must be one of {TIERS}")
    if tier == "passive":
        db.log_activity("notifier", "notify_passive", message[:120])
        return None
    ts = db.now()
    nid = db.execute(
        "INSERT INTO notifications (user, tier, message, link, sent_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (user, tier, message, link, ts if tier == "immediate" else None, ts),
    )
    if tier == "immediate":
        # The SAME rule flush_digest_tier follows, and for the same reason:
        # this posts into ONE shared channel, `notifications` carries no tier
        # to test (scope.UNSCOPED says why), and the callers quote scoped row
        # titles into `message` — blockers.resolve_blocker, delegation and
        # mentions all do. A count carries nothing, whatever a caller writes.
        # No emoji: Slack is not one of Skein's own surfaces (CLAUDE.md).
        _post_slack(f"Skein — 1 notification for {user}. Open Skein to read it.")
    return {"id": nid, "tier": tier}


def list_notifications(user: str, unread_only: bool = True) -> list[dict]:
    # 'team' notifications are addressed to everyone — same rule as briefing
    if unread_only:
        return db.query(
            "SELECT * FROM notifications WHERE user IN (?, 'team') AND read_at IS NULL"
            " ORDER BY id DESC LIMIT 50",
            (user,),
        )
    return db.query(
        "SELECT * FROM notifications WHERE user IN (?, 'team') ORDER BY id DESC LIMIT 50", (user,)
    )


def mark_read_matching(prefix: str) -> int:
    """Clear unread notifications whose message starts with `prefix` —
    used when the thing they point at (a review, a blocker) is resolved."""
    return db.execute_rowcount(
        "UPDATE notifications SET read_at = ? WHERE read_at IS NULL AND message LIKE ?",
        (db.now(), prefix + "%"),
    )


def mark_read(user: str, notification_id: int = 0) -> dict:
    # 'team' rows are a single shared record — the first reader clears them
    # for everyone, same semantics as listing them for everyone
    if notification_id:
        n = db.execute_rowcount(
            "UPDATE notifications SET read_at = ? WHERE id = ? AND user IN (?, 'team')",
            (db.now(), notification_id, user),
        )
    else:
        n = db.execute_rowcount(
            "UPDATE notifications SET read_at = ? WHERE user IN (?, 'team') AND read_at IS NULL",
            (db.now(), user),
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
        # rule at all 16 notify() call sites. A count carries nothing, whatever
        # a future caller writes.
        #
        # Nothing is lost: this post is a NUDGE. The bodies are already in the
        # app, and every line here links to them.
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
            f"UPDATE notifications SET sent_at = ? WHERE id IN ({','.join('?' * len(ids))})",  # noqa: S608
            (db.now(), *ids),
        )
    return {"flushed": len(pending)}
