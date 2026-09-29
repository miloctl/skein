-- One flat thread per task, decision or blocker (docs/intent/task-threads.md).
-- Three parent columns, not a polymorphic (entity, entity_id): each has its own
-- foreign key, so the cascade covers Your data and the offboarding erase with
-- no service code. A comment takes its parent's tier at write and keeps it
-- (services/comments.py). The partial indexes serve the thread read and keep a
-- parent delete from scanning the table.
CREATE TABLE comments (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id bigint REFERENCES tasks(id) ON DELETE CASCADE,
    decision_id bigint REFERENCES decisions(id) ON DELETE CASCADE,
    blocker_id bigint REFERENCES blockers(id) ON DELETE CASCADE,
    created_by text NOT NULL,
    origin text NOT NULL CHECK (origin IN ('human', 'agent', 'agent_verified')),
    body text NOT NULL,
    created_at text NOT NULL,
    edited_at text,
    deleted_at text,
    deleted_by text NOT NULL DEFAULT '',
    visibility text NOT NULL DEFAULT 'workspace'
        CHECK (visibility IN ('private', 'crew', 'workspace')),
    crew_id bigint REFERENCES crews(id),
    -- exactly one parent, so a row can never sit in two threads
    CHECK (num_nonnulls(task_id, decision_id, blocker_id) = 1),
    -- a tombstone holds no text, whatever path wrote it
    CHECK (deleted_at IS NULL OR body = '')
);
CREATE INDEX idx_comments_task ON comments (task_id, id) WHERE task_id IS NOT NULL;
CREATE INDEX idx_comments_decision ON comments (decision_id, id) WHERE decision_id IS NOT NULL;
CREATE INDEX idx_comments_blocker ON comments (blocker_id, id) WHERE blocker_id IS NOT NULL;
