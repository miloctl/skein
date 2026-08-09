-- Promises made TO the team, not only BY it.
--
-- The ledger recorded one direction. A manager's week is full of the other
-- one — "legal will send the redlines Thursday", "the vendor confirms
-- Friday" — and those lived in memory, in a DM, or nowhere. The ones that go
-- quiet are exactly the ones that hurt, and nothing was watching them.
--
-- `direction` on the SAME table rather than a new one: a promise is a promise
-- is a promise. It has a due date, an other party, and one of the same four
-- terminal states, and every reader it already has (the exec readout, the
-- findings rules, `waiting_on: promise:N`) should keep working without
-- learning a second shape. 'given' is the default, so every existing row
-- means exactly what it meant before this migration ran.
--
-- `to_whom` is THE OTHER PARTY in both directions, which is the one piece of
-- overloading here and is deliberate: for a given promise it is who we owe,
-- for a received one it is who owes us. A second column would be null on
-- half the rows and would have to be kept in step with `direction` by every
-- writer. The reader that needs the distinction reads `direction`.
--
-- `last_nudged_at` is the chaser's own memory. Without it the hourly rule
-- either nags every hour or has to infer silence from notification rows,
-- which retention prunes after 90 days (services/retention.py).
ALTER TABLE promises ADD COLUMN direction TEXT NOT NULL DEFAULT 'given'
    CHECK (direction IN ('given', 'received'));

ALTER TABLE promises ADD COLUMN last_nudged_at TEXT;

-- how many cycles have been chased, so the escalation fires on the SECOND
-- one rather than on however many times the hourly job happened to run.
-- Counted rather than derived from notification rows, which retention prunes
-- after 90 days (services/retention.py).
ALTER TABLE promises ADD COLUMN nudge_count INTEGER NOT NULL DEFAULT 0;

-- the chaser scans open received promises by due date every hour
CREATE INDEX IF NOT EXISTS idx_promises_direction_status
    ON promises (direction, status, due_date);
