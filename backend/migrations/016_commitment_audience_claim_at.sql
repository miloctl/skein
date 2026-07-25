ALTER TABLE commitments ADD COLUMN audience TEXT NOT NULL DEFAULT 'external'
    CHECK (audience IN ('external', 'team'));

ALTER TABLE pending_changes ADD COLUMN claim_at TEXT;
