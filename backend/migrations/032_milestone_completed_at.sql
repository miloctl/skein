-- slip forecasting needs the actual completion time — updated_at moves on
-- every post-done correction (relinks, title fixes) and poisoned the basis
ALTER TABLE milestones ADD COLUMN completed_at TEXT;
-- best-effort backfill: last touch is the closest thing history has
UPDATE milestones SET completed_at = updated_at WHERE status = 'done';
