-- Keep notification text bound to the policy context in effect when the text
-- was written. Existing typed rows stay unclassified and fail closed when an
-- applicable workplace rule is active.
ALTER TABLE notifications ADD COLUMN source_policy_context TEXT NOT NULL DEFAULT '{}';

-- Opaque aggregate checks use indexed existence probes for legacy broken
-- relationships. They must not materialize all historical rows per request.
CREATE INDEX idx_tasks_engagement_link
    ON tasks (engagement_id) WHERE engagement_id IS NOT NULL;
CREATE INDEX idx_tasks_milestone_link
    ON tasks (milestone_id) WHERE milestone_id IS NOT NULL;
CREATE INDEX idx_blockers_task_link
    ON blockers (task_id) WHERE task_id IS NOT NULL;
CREATE INDEX idx_milestones_engagement_link
    ON milestones (engagement_id) WHERE engagement_id IS NOT NULL;
CREATE INDEX idx_events_engagement_link
    ON events (engagement_id) WHERE engagement_id IS NOT NULL;
CREATE INDEX idx_promises_engagement_link
    ON promises (engagement_id) WHERE engagement_id IS NOT NULL;
CREATE INDEX idx_memories_engagement_link
    ON memories (engagement_id) WHERE engagement_id IS NOT NULL;
CREATE INDEX idx_lessons_engagement_link
    ON lessons (engagement_id) WHERE engagement_id IS NOT NULL;
CREATE INDEX idx_artifacts_engagement_link
    ON artifacts (engagement_id) WHERE engagement_id IS NOT NULL;
