-- Stock tools with specialized write rules can also require a workplace verdict.
CREATE TABLE extension_review_invocations_v3 (
    change_id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('tool', 'workflow', 'mcp_tool', 'core_tool')),
    invocation TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    result TEXT NOT NULL DEFAULT '{}',
    error_code TEXT NOT NULL DEFAULT '',
    executed_at TEXT,
    FOREIGN KEY (change_id) REFERENCES pending_changes(id)
);

INSERT INTO extension_review_invocations_v3
    (change_id, kind, invocation, status, result, error_code, executed_at)
SELECT change_id, kind, invocation, status, result, error_code, executed_at
FROM extension_review_invocations;

DROP TABLE extension_review_invocations;
ALTER TABLE extension_review_invocations_v3 RENAME TO extension_review_invocations;
