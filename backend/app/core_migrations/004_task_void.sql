-- 'void': the task never should have existed. A terminal state, not a
-- delete — blockers, waiting_on edges, proposals and the activity ledger
-- all reference task ids, and docs/CORRECTIONS.md rule 2 says records that
-- carry history get a terminal state. 'done' could not carry this meaning:
-- it feeds throughput, cycle time and kept-%, so voiding-by-done polluted
-- every metric it touched.
ALTER TABLE tasks DROP CONSTRAINT tasks_status_check;
ALTER TABLE tasks ADD CONSTRAINT tasks_status_check
    CHECK (status IN ('todo', 'in_progress', 'blocked', 'done', 'void'));
