CREATE TABLE browser_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    mode TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('api-key', 'oidc')),
    key_id BIGINT REFERENCES api_keys(id) ON DELETE CASCADE,
    sealed_tokens BYTEA,
    issuer TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    client_binding TEXT NOT NULL DEFAULT '',
    access_expires_at DOUBLE PRECISION,
    expires_at TEXT NOT NULL,
    refresh_nonce TEXT NOT NULL DEFAULT '',
    refresh_until DOUBLE PRECISION NOT NULL DEFAULT 0,
    origin TEXT NOT NULL DEFAULT 'human' CHECK (origin = 'human'),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (
        (kind = 'api-key' AND key_id IS NOT NULL AND sealed_tokens IS NULL)
        OR (kind = 'oidc' AND key_id IS NULL AND sealed_tokens IS NOT NULL
            AND issuer != '' AND subject != '' AND client_binding != ''
            AND access_expires_at IS NOT NULL)
    )
);
CREATE INDEX idx_browser_sessions_user ON browser_sessions(user_id, created_at);
CREATE INDEX idx_browser_sessions_expiry ON browser_sessions(expires_at);
