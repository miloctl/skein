-- A personal MCP server signs in with a bearer token only. OAuth sign-in is
-- removed, with its flow table and the columns that held its grant.
-- A row added with OAuth stays: its name and URL are kept, and it becomes a
-- token server with no token. Its sealed grant leaves with the columns, so
-- its connect fails and the Settings card shows it as not connected. The
-- owner deletes the row and adds the server again with a token.
DROP TABLE mcp_oauth_flows;
ALTER TABLE mcp_servers
    DROP COLUMN auth,
    DROP COLUMN oauth_tokens_sealed,
    DROP COLUMN oauth_client_sealed,
    DROP COLUMN oauth_redirect_uri,
    DROP COLUMN oauth_signin_required;
