-- services/erasure.py erases a person's private data GRACE_DAYS after
-- deactivation. The clock of an account already inactive starts now: an
-- erasure must never land sooner than the deactivate confirmation said.
ALTER TABLE users ADD COLUMN deactivated_at text, ADD COLUMN erased_at text;
UPDATE users
SET deactivated_at = to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"+00:00"')
WHERE active = 0;
