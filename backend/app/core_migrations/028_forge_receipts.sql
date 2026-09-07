CREATE TABLE forge_receipts (
    namespace TEXT NOT NULL,
    delivery_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    event TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    task_id BIGINT REFERENCES tasks(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'human',
    created_by TEXT NOT NULL DEFAULT 'forge',
    PRIMARY KEY (namespace, delivery_id)
);
CREATE INDEX forge_receipts_task ON forge_receipts(task_id) WHERE task_id IS NOT NULL;
