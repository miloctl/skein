-- Per-person read state for team-wide notifications.
--
-- `notifications.read_at` is ONE column on ONE row. A notification addressed
-- to 'team' is a single shared record, so the first person to press dismiss
-- cleared it for the whole roster — everybody else simply never saw it. That
-- is the opposite of what a team announcement is for, and it made the digest
-- tier quietly lossy in exactly the deployments where it matters most (a busy
-- team, where somebody always reads first).
--
-- A side table rather than a row per person per notification: the fan-out on
-- write would be one INSERT per active teammate for every team notification,
-- and the roster changes underneath it. This records only what somebody has
-- actually dismissed, so an unread team notification costs no rows at all.
--
-- `read_at` stays authoritative for personal rows and for mark_read_matching,
-- which clears a notification because the thing it points at is settled — that
-- is a fact about the world, not about one reader.
CREATE TABLE IF NOT EXISTS notification_reads (
    notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
    user TEXT NOT NULL,
    read_at TEXT NOT NULL,
    PRIMARY KEY (notification_id, user)
);

-- The unread query filters `notifications` by (id, user) pairs absent from
-- here, and the primary key already serves that lookup. This reverse index
-- serves the other direction: `retention.prune` asks, per old team row,
-- whether EVERY active human has dismissed it, which walks the roster and
-- probes this table by user.
CREATE INDEX IF NOT EXISTS idx_notification_reads_user ON notification_reads (user);
