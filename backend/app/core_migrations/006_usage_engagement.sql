-- Direct engagement attribution on a usage row. The thread-link join stays
-- the chat path (linking a thread later attributes its past turns, which is
-- the retroactive "bill a chat" feature) — this column is for turns with no
-- linkable thread at all: the unattended agent runner, whose spend all sat
-- under '(unlinked)' however clearly it belonged to one engagement's work.
ALTER TABLE usage_log ADD COLUMN engagement_id bigint
    REFERENCES engagements(id) ON DELETE SET NULL;
