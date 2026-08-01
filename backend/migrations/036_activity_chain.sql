-- Tamper-evident activity chain. Each new row hashes its own fields plus the
-- previous row's hash, so an edited or deleted row breaks verification at a
-- known seq.
--
-- Rows written before this migration keep NULL seq/hash and are reported as
-- unchained, never as verified. There is deliberately no backfill: a chain
-- computed today over rows written yesterday proves nothing about yesterday.
ALTER TABLE activity ADD COLUMN seq INTEGER;
ALTER TABLE activity ADD COLUMN hash TEXT;
ALTER TABLE activity ADD COLUMN prev_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_activity_seq ON activity(seq) WHERE seq IS NOT NULL
