-- A delete of a chat message checks every row that references it. Without an
-- index on the referencing column that is a whole-table scan per message, and
-- usage_log is kept forever (tests/test_foreign_key_indexes.py).
CREATE INDEX IF NOT EXISTS idx_usage_log_trigger_message ON usage_log (trigger_message_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_reply_to ON chat_messages (reply_to_message_id);
CREATE INDEX IF NOT EXISTS idx_chat_agent_runs_trigger_message
    ON chat_agent_runs (trigger_message_id);
CREATE INDEX IF NOT EXISTS idx_chat_agent_runs_response_message
    ON chat_agent_runs (response_message_id);
