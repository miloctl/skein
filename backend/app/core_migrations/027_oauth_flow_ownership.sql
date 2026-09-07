-- Legacy flows have no owner fence and hold plaintext codes. Discard them:
-- resuming an old grant can overwrite a newer client's tokens after upgrade.
DROP TABLE mcp_oauth_flows;
CREATE TABLE mcp_oauth_flows (
    claim_id text PRIMARY KEY,
    server_id bigint NOT NULL UNIQUE REFERENCES mcp_servers(id) ON DELETE CASCADE,
    owner text NOT NULL,
    state text UNIQUE,
    code_sealed bytea,
    refused boolean NOT NULL DEFAULT FALSE,
    done boolean NOT NULL DEFAULT FALSE,
    created_at text NOT NULL,
    expires_at text NOT NULL
);
CREATE INDEX idx_mcp_oauth_flows_expiry ON mcp_oauth_flows(expires_at);
