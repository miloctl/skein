-- Remote MCP servers registered through the API rather than SKEIN_MCP_SERVERS.
-- auth_token_sealed is Fernet ciphertext under SKEIN_CREDENTIAL_KEY, which
-- lives in the deployment Secret and never in this database: a backup or
-- export of this table carries ciphertext only (services/credentials.py).
-- scope 'team' is reserved for crew-scoped rows; only 'personal' is written.
CREATE TABLE mcp_servers (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope text NOT NULL CHECK (scope IN ('team', 'personal')),
    owner text NOT NULL,
    name text NOT NULL,
    url text NOT NULL,
    auth_token_sealed bytea,
    origin text NOT NULL DEFAULT 'human',
    created_by text NOT NULL DEFAULT '',
    created_at text NOT NULL,
    updated_at text NOT NULL,
    UNIQUE (scope, owner, name)
);
