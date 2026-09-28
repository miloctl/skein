-- The meeting an item came out of (docs/intent/calendar.md). One nullable
-- column per kind the capture grammar files, not a link table: each child's
-- own tier filter governs every read of it. SET NULL, because cancel_event,
-- erasure and my_data.delete_private hard-delete events, and a cancelled
-- meeting must not take its tasks and decisions with it. The partial indexes
-- keep that delete from scanning seven tables.
ALTER TABLE notes ADD COLUMN event_id bigint REFERENCES events(id) ON DELETE SET NULL;
ALTER TABLE tasks ADD COLUMN event_id bigint REFERENCES events(id) ON DELETE SET NULL;
ALTER TABLE decisions ADD COLUMN event_id bigint REFERENCES events(id) ON DELETE SET NULL;
ALTER TABLE questions ADD COLUMN event_id bigint REFERENCES events(id) ON DELETE SET NULL;
ALTER TABLE blockers ADD COLUMN event_id bigint REFERENCES events(id) ON DELETE SET NULL;
ALTER TABLE promises ADD COLUMN event_id bigint REFERENCES events(id) ON DELETE SET NULL;
ALTER TABLE intake_requests ADD COLUMN event_id bigint REFERENCES events(id) ON DELETE SET NULL;
CREATE INDEX idx_notes_event_link ON notes(event_id) WHERE event_id IS NOT NULL;
CREATE INDEX idx_tasks_event_link ON tasks(event_id) WHERE event_id IS NOT NULL;
CREATE INDEX idx_decisions_event_link ON decisions(event_id) WHERE event_id IS NOT NULL;
CREATE INDEX idx_questions_event_link ON questions(event_id) WHERE event_id IS NOT NULL;
CREATE INDEX idx_blockers_event_link ON blockers(event_id) WHERE event_id IS NOT NULL;
CREATE INDEX idx_promises_event_link ON promises(event_id) WHERE event_id IS NOT NULL;
CREATE INDEX idx_intake_requests_event_link ON intake_requests(event_id) WHERE event_id IS NOT NULL;
