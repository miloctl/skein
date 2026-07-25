ALTER TABLE tasks ADD COLUMN waiting_on_type TEXT
    CHECK (waiting_on_type IN ('task', 'blocker', 'commitment'));
ALTER TABLE tasks ADD COLUMN waiting_on_id INTEGER;

ALTER TABLE decisions ADD COLUMN category TEXT NOT NULL DEFAULT '';

ALTER TABLE users ADD COLUMN growth_interests TEXT NOT NULL DEFAULT '';

ALTER TABLE agent_authority ADD COLUMN review_by TEXT;
