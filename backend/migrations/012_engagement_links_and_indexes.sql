ALTER TABLE milestones ADD COLUMN engagement_id INTEGER REFERENCES engagements(id);
UPDATE milestones SET engagement_id =
    (SELECT id FROM engagements WHERE engagements.name = milestones.project);

ALTER TABLE allocations ADD COLUMN origin TEXT NOT NULL DEFAULT 'human';
ALTER TABLE allocations ADD COLUMN created_by TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_activity_actor ON activity(actor, created_at);
CREATE INDEX IF NOT EXISTS idx_activity_action ON activity(action, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_blockers_status ON blockers(status);
CREATE INDEX IF NOT EXISTS idx_milestones_project ON milestones(project);
