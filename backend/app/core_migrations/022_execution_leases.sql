-- A running row names the process that holds it and when that claim lapses.
-- '' sorts before every ISO timestamp, so a cleared or pre-migration lease
-- reads as already expired (services/leases.py).
ALTER TABLE agent_wakeups
    ADD COLUMN lease_owner text NOT NULL DEFAULT '',
    ADD COLUMN lease_until text NOT NULL DEFAULT '';
ALTER TABLE chat_agent_runs
    ADD COLUMN lease_owner text NOT NULL DEFAULT '',
    ADD COLUMN lease_until text NOT NULL DEFAULT '';
