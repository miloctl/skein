CREATE TABLE forge_receipts (
    namespace TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    provider TEXT NOT NULL CHECK (provider IN ('github', 'gitea')),
    event TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    task_id BIGINT REFERENCES tasks(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT 'forge',
    PRIMARY KEY (namespace, delivery_id)
);
CREATE INDEX forge_receipts_task ON forge_receipts(task_id) WHERE task_id IS NOT NULL;

CREATE TABLE github_recovery_api (
    namespace TEXT PRIMARY KEY,
    retry_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT 'scheduler'
);

CREATE TABLE github_recovery_hooks (
    namespace TEXT PRIMARY KEY,
    repository TEXT NOT NULL,
    hook_id BIGINT NOT NULL,
    cursor TEXT NOT NULL DEFAULT '',
    scan_started_at TEXT NOT NULL DEFAULT '',
    drain_scan TEXT NOT NULL DEFAULT '',
    last_drained_at TEXT NOT NULL DEFAULT '',
    last_complete_at TEXT NOT NULL DEFAULT '',
    retry_at TEXT NOT NULL DEFAULT '',
    failures INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    gap_since TEXT NOT NULL DEFAULT '',
    gap_until TEXT NOT NULL DEFAULT '',
    reconciled_at TEXT NOT NULL DEFAULT '',
    reconciled_by TEXT NOT NULL DEFAULT '',
    reconciliation_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT 'scheduler'
);

CREATE TABLE github_recovery_deliveries (
    namespace TEXT NOT NULL REFERENCES github_recovery_hooks(namespace),
    guid TEXT NOT NULL,
    delivery_id BIGINT NOT NULL,
    event TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    successful BOOLEAN NOT NULL DEFAULT FALSE,
    state TEXT NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'requested', 'unknown', 'successful', 'local_receipt', 'unavailable')),
    attempts INTEGER NOT NULL DEFAULT 0,
    retry_at TEXT NOT NULL DEFAULT '',
    seen_scan TEXT NOT NULL,
    attempted_scan TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT 'scheduler',
    PRIMARY KEY (namespace, guid)
);
CREATE INDEX github_recovery_pending ON github_recovery_deliveries(namespace, retry_at)
    WHERE state IN ('pending', 'requested', 'unknown');

CREATE TABLE github_recovery_attempts (
    id BIGSERIAL PRIMARY KEY,
    namespace TEXT NOT NULL,
    guid TEXT NOT NULL,
    delivery_id BIGINT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('unknown', 'accepted', 'failed')),
    error_code TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT 'scheduler',
    FOREIGN KEY (namespace, guid) REFERENCES github_recovery_deliveries(namespace, guid)
);
