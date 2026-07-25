-- Tasks can link straight to an engagement (no milestone required):
-- agent-created work like "a task on the Alerting rules engagement" was
-- silently dropping the linkage because tasks only knew milestone_id.
ALTER TABLE tasks ADD COLUMN engagement_id INTEGER REFERENCES engagements(id);
