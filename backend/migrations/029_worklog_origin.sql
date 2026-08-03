-- provenance parity: task_worklog rows must say which write path produced
-- them ('agent' direct, 'agent_verified' via an accepted proposal, 'human').
-- The DEFAULT backfills 'agent', not 'human' like other origin columns:
-- the only writer 028 shipped with was the report_progress agent tool, so
-- every row that predates this migration is agent-written
ALTER TABLE task_worklog ADD COLUMN origin TEXT NOT NULL DEFAULT 'agent';
