-- retention.prune clears the text of a settled proposal past its horizon
-- and stamps this, so a daily run skips what it already cleared and the
-- review list can say why a proposal has no text.
ALTER TABLE pending_changes ADD COLUMN text_cleared_at text;
