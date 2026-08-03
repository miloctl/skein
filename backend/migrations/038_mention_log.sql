-- The first @mention of a person on a row notifies. Every later re-parse of
-- the same text is a no-op: an edit or a typo fix must never notify twice.
CREATE TABLE IF NOT EXISTS mention_log (
    entity TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    person TEXT NOT NULL,
    mentioned_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (entity, entity_id, person)
);
