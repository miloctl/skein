-- OAuth 2.1 sign-in for a personal MCP server (agents/mcp_oauth.py). The
-- tokens and the dynamically registered client are sealed like the bearer
-- token: ciphertext under SKEIN_CREDENTIAL_KEY, which never enters this
-- database. The redirect URI is the one registered with the authorization
-- server, kept so a later connect builds the same client metadata.
ALTER TABLE mcp_servers
    ADD COLUMN auth text NOT NULL DEFAULT 'token' CHECK (auth IN ('token', 'oauth')),
    ADD COLUMN oauth_tokens_sealed bytea,
    ADD COLUMN oauth_client_sealed bytea,
    ADD COLUMN oauth_redirect_uri text NOT NULL DEFAULT '';
