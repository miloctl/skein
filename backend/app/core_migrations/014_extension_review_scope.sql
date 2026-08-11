-- Bind extension reviews to an audience and record why the reviewer qualified.
ALTER TABLE pending_changes ADD COLUMN review_visibility TEXT NOT NULL DEFAULT 'workspace'
    CHECK (review_visibility IN ('private', 'crew', 'workspace'));
ALTER TABLE pending_changes ADD COLUMN review_crew_id INTEGER;
ALTER TABLE pending_changes ADD COLUMN review_owner TEXT NOT NULL DEFAULT '';
ALTER TABLE pending_changes ADD COLUMN reviewer_qualifications TEXT NOT NULL DEFAULT '{}';

-- MCP tool calls use the same durable review queue as contributed tools and workflows.
CREATE TABLE extension_review_invocations_v2 (
    change_id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('tool', 'workflow', 'mcp_tool')),
    invocation TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    result TEXT NOT NULL DEFAULT '{}',
    error_code TEXT NOT NULL DEFAULT '',
    executed_at TEXT,
    FOREIGN KEY (change_id) REFERENCES pending_changes(id)
);

INSERT INTO extension_review_invocations_v2
    (change_id, kind, invocation, status, result, error_code, executed_at)
SELECT change_id, kind, invocation, status, result, error_code, executed_at
FROM extension_review_invocations;

DROP TABLE extension_review_invocations;
ALTER TABLE extension_review_invocations_v2 RENAME TO extension_review_invocations;
