-- The browser that started an MCP sign-in. The callback refuses any other
-- browser, so an authorization URL sent to a colleague cannot deliver their
-- grant to the sender's server. NULL on flows started before this column: a
-- flow lives five minutes, and those fail their callback once.
ALTER TABLE mcp_oauth_flows ADD COLUMN browser_binding text;
