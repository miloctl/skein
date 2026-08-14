-- A route or job whose write needs a workplace verdict can now queue the
-- command itself. Before this the kind list refused the row, so an
-- unattended integration could be stopped by policy but never sent to a
-- human: the route answered 409 and the job answered
-- POLICY_REVIEW_UNSUPPORTED, and neither left anything to approve.
CREATE TABLE extension_review_invocations_v4 (
    change_id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL
        CHECK (kind IN ('tool', 'workflow', 'mcp_tool', 'core_tool', 'public_command')),
    invocation TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    result TEXT NOT NULL DEFAULT '{}',
    error_code TEXT NOT NULL DEFAULT '',
    executed_at TEXT,
    FOREIGN KEY (change_id) REFERENCES pending_changes(id)
);

INSERT INTO extension_review_invocations_v4
    (change_id, kind, invocation, status, result, error_code, executed_at)
SELECT change_id, kind, invocation, status, result, error_code, executed_at
FROM extension_review_invocations;

DROP TABLE extension_review_invocations;
ALTER TABLE extension_review_invocations_v4 RENAME TO extension_review_invocations;
