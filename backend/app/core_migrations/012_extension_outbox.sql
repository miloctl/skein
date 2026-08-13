-- Durable delivery for public, versioned events.
--
-- The payload contains the public event contract. It contains resource
-- identifiers and safe change summaries. It does not contain row bodies.
CREATE TABLE extension_outbox (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    payload TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'workspace',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'delivered', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    delivered_at TEXT
);

CREATE INDEX idx_extension_outbox_delivery
    ON extension_outbox (status, created_at);

-- A subscriber records an event only after its handler returns successfully.
-- The primary key makes repeated delivery claims idempotent.
CREATE TABLE extension_event_deliveries (
    event_id TEXT NOT NULL REFERENCES extension_outbox(event_id) ON DELETE CASCADE,
    subscriber TEXT NOT NULL,
    delivered_at TEXT NOT NULL,
    PRIMARY KEY (event_id, subscriber)
);

-- Retry state is per subscriber. One tolerant subscriber must not extend a
-- stricter subscriber's retry budget.
CREATE TABLE extension_event_attempts (
    event_id TEXT NOT NULL REFERENCES extension_outbox(event_id) ON DELETE CASCADE,
    subscriber TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'dead')),
    last_error_code TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (event_id, subscriber)
);
