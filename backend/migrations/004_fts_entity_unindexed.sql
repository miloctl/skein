DROP TABLE IF EXISTS search_index;

CREATE VIRTUAL TABLE search_index USING fts5(
    entity UNINDEXED, entity_id UNINDEXED, title, body
);

INSERT INTO search_index (entity, entity_id, title, body)
    SELECT 'milestone', id, title, description || ' ' || project || ' ' || owner FROM milestones;
INSERT INTO search_index (entity, entity_id, title, body)
    SELECT 'task', id, title, description || ' ' || assignee FROM tasks;
INSERT INTO search_index (entity, entity_id, title, body)
    SELECT 'question', id, substr(question, 1, 120), question || ' ' || COALESCE(answer, '') FROM questions;
INSERT INTO search_index (entity, entity_id, title, body)
    SELECT 'decision', id, title, decision || ' ' || context FROM decisions;
INSERT INTO search_index (entity, entity_id, title, body)
    SELECT 'note', id, topic, content FROM notes;
INSERT INTO search_index (entity, entity_id, title, body)
    SELECT 'blocker', id, title, detail || ' ' || owner FROM blockers;
INSERT INTO search_index (entity, entity_id, title, body)
    SELECT 'intake', id, title, detail || ' ' || requester || ' ' || project_class FROM intake_requests;
INSERT INTO search_index (entity, entity_id, title, body)
    SELECT 'engagement', id, name, summary || ' ' || project_class || ' ' || lead FROM engagements;
INSERT INTO search_index (entity, entity_id, title, body)
    SELECT 'lesson', id, substr(lesson, 1, 120), lesson || ' ' || recommendation FROM lessons;
