ALTER TABLE notifications
ADD COLUMN pending_change_id bigint REFERENCES pending_changes(id) ON DELETE SET NULL;

CREATE INDEX notifications_pending_change_idx
ON notifications (pending_change_id)
WHERE pending_change_id IS NOT NULL;
