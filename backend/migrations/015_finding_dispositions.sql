CREATE TABLE IF NOT EXISTS finding_dispositions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id INTEGER NOT NULL REFERENCES findings(id),
    rule_id TEXT NOT NULL,
    subject TEXT NOT NULL,
    disposition TEXT NOT NULL
        CHECK (disposition IN ('dismissed', 'deferred', 'converted', 'resolved')),
    reason TEXT NOT NULL DEFAULT '',
    deferred_until TEXT,
    created_by TEXT NOT NULL DEFAULT '',
    origin TEXT NOT NULL DEFAULT 'human',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_finding_dispositions_rule
    ON finding_dispositions (rule_id, subject, created_at);

ALTER TABLE tasks ADD COLUMN source_finding_id INTEGER REFERENCES findings(id);
ALTER TABLE questions ADD COLUMN source_finding_id INTEGER REFERENCES findings(id);
