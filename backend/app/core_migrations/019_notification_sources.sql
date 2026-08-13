-- Bind free-form notification text to the resource that produced it.
-- Existing rows stay unclassified. Policy-aware readers omit those legacy
-- bodies when workplace rules are active.
ALTER TABLE notifications ADD COLUMN source_entity TEXT NOT NULL DEFAULT '';
ALTER TABLE notifications ADD COLUMN source_id INTEGER;

CREATE INDEX idx_notifications_source
    ON notifications (source_entity, source_id);
