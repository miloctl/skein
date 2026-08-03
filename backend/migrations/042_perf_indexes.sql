-- Indexes for the hot read paths that were full-scanning as tables grow.
--
-- activity(seq) WHERE seq IS NULL: /health counts unchained rows on every
-- probe, and idx_activity_seq only covers seq IS NOT NULL -- the count was
-- walking the whole ledger. The partial index holds exactly the pre-036
-- rows plus fallback appends, so the count reads a handful of entries.
--
-- The rest serve range predicates that had no index at all: usage_log by
-- created_at (cost windows), standups by created_at (the standup chain's
-- lookback), events by starts_at (today/upcoming views and the ICS feed).
CREATE INDEX idx_activity_unchained ON activity(seq) WHERE seq IS NULL;
CREATE INDEX idx_usage_log_created ON usage_log(created_at);
CREATE INDEX idx_standups_created ON standups(created_at);
CREATE INDEX idx_events_starts_at ON events(starts_at)
