-- Persist workplace approval requirements on the proposal they govern.
ALTER TABLE pending_changes ADD COLUMN policy_obligations TEXT NOT NULL DEFAULT '[]';
ALTER TABLE pending_changes ADD COLUMN approver_groups TEXT NOT NULL DEFAULT '[]';
ALTER TABLE pending_changes ADD COLUMN approver_capabilities TEXT NOT NULL DEFAULT '[]';

-- Public command idempotency belongs to the core command boundary. The
-- extension owns its external mapping data, not this receipt.
CREATE TABLE extension_command_receipts (
    namespace TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    result_type TEXT NOT NULL,
    result_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (namespace, idempotency_key)
);

-- Arguments for a reviewed extension action are not copied into the review
-- queue. The queue contains a safe summary. Only the approval executor reads
-- this operational record.
CREATE TABLE extension_review_invocations (
    change_id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('tool', 'workflow')),
    invocation TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected')),
    result TEXT NOT NULL DEFAULT '{}',
    error_code TEXT NOT NULL DEFAULT '',
    executed_at TEXT,
    FOREIGN KEY (change_id) REFERENCES pending_changes(id)
);
