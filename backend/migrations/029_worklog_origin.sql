-- provenance parity: task_worklog rows must say which write path produced
-- them ('agent' direct, 'agent_verified' via an accepted proposal, 'human')
ALTER TABLE task_worklog ADD COLUMN origin TEXT NOT NULL DEFAULT 'agent';
