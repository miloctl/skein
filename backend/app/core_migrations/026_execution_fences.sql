-- A process can acquire the same resource more than once. An old worker must
-- not release or write through its successor's acquisition.
ALTER TABLE agent_wakeups ADD COLUMN lease_token text NOT NULL DEFAULT '';
ALTER TABLE chat_agent_runs ADD COLUMN lease_token text NOT NULL DEFAULT '';
ALTER TABLE job_runs ADD COLUMN lease_token text NOT NULL DEFAULT '';
CREATE UNIQUE INDEX agent_wakeups_lease_token ON agent_wakeups (lease_token) WHERE lease_token != '';
CREATE UNIQUE INDEX chat_agent_runs_lease_token ON chat_agent_runs (lease_token) WHERE lease_token != '';
CREATE UNIQUE INDEX job_runs_lease_token ON job_runs (lease_token) WHERE lease_token != '';
