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
from datetime import datetime, timezone

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
        _post_slack(f"🔔 {user}: {message}")
    return {"id": nid, "tier": tier}


def list_notifications(user: str, unread_only: bool = True) -> list[dict]:
    # 'team' notifications are addressed to everyone — same rule as briefing
    if unread_only:
        return db.query(
            "SELECT * FROM notifications WHERE user IN (?, 'team') AND read_at IS NULL"
            " ORDER BY id DESC LIMIT 50", (user,),
        )
    return db.query(
        "SELECT * FROM notifications WHERE user IN (?, 'team')"
        " ORDER BY id DESC LIMIT 50", (user,)
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
            "UPDATE notifications SET read_at = ? WHERE user IN (?, 'team')"
            " AND read_at IS NULL",
            (db.now(), user),
        )
    return {"marked": n}


def flush_digest_tier(*, claim: bool = False) -> dict:
    """Twice-daily job: batch unsent digest-tier notifications to Slack.
    claim=True makes the run once-only per hour bucket (scheduler path)."""
    if claim:
        bucket = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        if not db.claim_job("notification-flush", bucket):
            return {"flushed": 0, "skipped": "already flushed this run"}
    pending = db.query(
        "SELECT * FROM notifications WHERE tier = 'digest' AND sent_at IS NULL ORDER BY id"
    )
    if pending:
        by_user: dict[str, list[str]] = {}
        for n in pending:
            by_user.setdefault(n["user"], []).append(n["message"])
        lines = [f"*{u}*: " + " · ".join(msgs) for u, msgs in by_user.items()]
        _post_slack("📬 Skein digest\n" + "\n".join(lines))
        # Stamp exactly the rows we posted — a notification inserted between
        # the SELECT and this UPDATE must stay pending for the next flush.
        ids = [n["id"] for n in pending]
        db.execute_rowcount(
            f"UPDATE notifications SET sent_at = ? WHERE id IN ({','.join('?' * len(ids))})",  # noqa: S608
            (db.now(), *ids),
        )
    return {"flushed": len(pending)}
