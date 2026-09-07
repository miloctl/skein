-- A sign-in's authorization code returns to whichever process serves the
-- callback, so the waiting connect reads it from here (agents/mcp_oauth.py).
-- Rows live FLOW_SECONDS and hold a one-time code, never a token.
CREATE TABLE mcp_oauth_flows (
    state text PRIMARY KEY,
    server_id text NOT NULL,
    code text NOT NULL DEFAULT '',
    error text NOT NULL DEFAULT '',
    done boolean NOT NULL DEFAULT FALSE,
    created_at text NOT NULL,
    expires_at text NOT NULL
);
ALTER TABLE mcp_servers ADD COLUMN oauth_signin_required boolean NOT NULL DEFAULT FALSE;
