CREATE UNIQUE INDEX IF NOT EXISTS ux_context_packs_version
    ON context_packs(version);

CREATE INDEX IF NOT EXISTS idx_pending_changes_proposer_entity
    ON pending_changes(proposed_by, entity, id);
